"""Deterministic guardrail (requirements §8, plan M10).

`validate` checks the agent's final submit_decision against what the tools actually returned,
read from the run's trace. It never calls a model or a tool, so the same inputs always give
the same verdict. The loop gives the model one chance to fix a violation; a second one ends in
an UNCLEAR_REQUEST escalation.
"""

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from militech_agent.prompts import DENY_CODES
from militech_equipment import dates


@dataclass(frozen=True)
class Violation:
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _norm(value: Any) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def _successful(trace: list[dict[str, Any]], action: str, key: str) -> list[dict[str, Any]]:
    """Trace entries where `action` ran and returned `key` (e.g. a decision_id)."""
    return [e for e in trace if e["action"] == action and key in (e.get("observation") or {})]


def _last_observation(trace: list[dict[str, Any]], action: str) -> dict[str, Any] | None:
    for entry in reversed(trace):
        observation = entry.get("observation") or {}
        if entry["action"] == action and "error" not in observation:
            return observation
    return None


def _check_replacement_below_cap(
    item: str, trace: list[dict[str, Any]], as_of: date
) -> Violation | None:
    """An explicit replacement below the cap: recompute the oldest unit's age ourselves."""
    eligibility = next(
        (e["observation"] for e in reversed(trace)
         if e["action"] == "check_request_eligibility"
         and _norm((e.get("observation") or {}).get("item")) == item),
        None,
    )  # fmt: skip
    if eligibility and eligibility.get("request_type_assumed") == "replacement":
        return None  # at the cap: check_request_eligibility already applied the refresh rule

    employee = _last_observation(trace, "get_employee_info")
    policy = _last_observation(trace, "get_policy_limits")
    rule = ((policy or {}).get("limits") or {}).get(item)
    issued = sorted(
        unit["issued_date"] for unit in (employee or {}).get("equipment", [])
        if _norm(unit.get("item")) == item
    )  # fmt: skip
    if employee is None or rule is None or not issued:
        return Violation(
            "replacement_not_verified",
            f"A replacement {item} below the cap needs get_employee_info and get_policy_limits "
            "results showing the oldest unit and its refresh_months.",
        )
    due = dates.add_months(date.fromisoformat(issued[0]), rule["refresh_months"])
    if due > as_of:
        return Violation(
            "replacement_not_due",
            f"The oldest {item} (issued {issued[0]}) is not due for replacement until "
            f"{due.isoformat()}.",
        )
    return None


def _employee_status(entry: dict[str, Any]) -> str | None:
    """'found' / 'not_found' if this lookup settles whether the employee exists, else None."""
    observation = entry.get("observation") or {}
    if entry["action"] == "get_employee_info":
        if observation.get("error") == "employee_not_found":
            return "not_found"
        return None if "error" in observation else "found"
    if entry["action"] == "check_request_eligibility":
        if observation.get("status") == "employee_not_found":
            return "not_found"
        # check_request_eligibility verifies the employee before the catalog, so any other
        # status (off_catalog included) means the employee exists.
        return "found" if observation.get("status") else None
    return None


def _check_employee_verified(
    employee_id: Any, code: str | None, trace: list[dict[str, Any]]
) -> Violation | None:
    """An escalation must rest on a lookup of the employee, and its code must agree with it."""
    wanted = (employee_id or "").strip().upper() if isinstance(employee_id, str) else ""
    statuses = [
        status for e in trace
        if (e["args"].get("employee_id") or "").strip().upper() == wanted
        and (status := _employee_status(e)) is not None
    ]  # fmt: skip
    if not statuses:
        return Violation(
            "employee_not_verified",
            f"Check the employee first: call get_employee_info or check_request_eligibility for "
            f"{wanted or 'the employee'} before escalating, so the reason code is right.",
        )
    if statuses[-1] == "not_found" and code != "EMPLOYEE_NOT_FOUND":
        return Violation(
            "employee_status_mismatch",
            f"{wanted} is not on file: escalate it as EMPLOYEE_NOT_FOUND, not {code}.",
        )
    if statuses[-1] == "found" and code == "EMPLOYEE_NOT_FOUND":
        return Violation(
            "employee_status_mismatch",
            f"{wanted} is on file, so EMPLOYEE_NOT_FOUND doesn't apply.",
        )
    return None


def validate(
    submission: dict[str, Any], trace: list[dict[str, Any]], *, as_of: date | None = None
) -> list[Violation]:
    """Every way `submission` disagrees with the trace (empty list = consistent)."""
    as_of = as_of or dates.today()
    decision = submission.get("decision")
    item = _norm(submission.get("item"))
    request_type = submission.get("request_type") or None
    code = submission.get("reason_code") or None
    draft = submission.get("draft_response") or ""
    records = _successful(trace, "record_decision", "decision_id")
    tickets = _successful(trace, "flag_for_human_review", "ticket_id")
    violations: list[Violation] = []

    if decision in ("approve", "deny"):
        if decision == "deny" and code not in DENY_CODES:
            violations.append(Violation(
                "deny_code_invalid",
                f"A deny needs one of {', '.join(DENY_CODES)} as its reason_code, not {code}.",
            ))  # fmt: skip
        matching = [r for r in records
                    if _norm(r["args"].get("decision")) == decision
                    and _norm(r["args"].get("item")) == item]  # fmt: skip
        if not matching:
            violations.append(Violation(
                f"{decision}_not_recorded",
                f"No successful record_decision({decision}) for {item or 'this item'}: record "
                "it before submitting.",
            ))  # fmt: skip
        else:
            recorded = matching[-1]
            decision_id = recorded["observation"]["decision_id"]
            if _norm(recorded["args"].get("request_type")) != _norm(request_type):
                violations.append(Violation(
                    "request_type_mismatch",
                    f"record_decision used request_type {recorded['args'].get('request_type')}, "
                    f"but the submission says {request_type}.",
                ))  # fmt: skip
            if decision_id not in draft:
                violations.append(Violation(
                    "draft_missing_id", f"The draft must quote the decision_id {decision_id}."
                ))  # fmt: skip
            if decision == "approve" and request_type == "replacement":
                problem = _check_replacement_below_cap(item, trace, as_of)
                if problem is not None:
                    violations.append(problem)
        if tickets:
            violations.append(Violation(
                "conflicting_records",
                f"A ticket ({tickets[-1]['observation']['ticket_id']}) was opened as well as a "
                f"decision: a request gets one outcome.",
            ))  # fmt: skip

    elif decision == "escalate":
        if not tickets:
            violations.append(Violation(
                "escalation_not_flagged",
                "No ticket_id from flag_for_human_review: open a ticket before submitting.",
            ))  # fmt: skip
        else:
            ticket = tickets[-1]
            ticket_id = ticket["observation"]["ticket_id"]
            flagged_code = (ticket["args"].get("reason_code") or "").strip().upper()
            if flagged_code != (code or "").upper():
                violations.append(Violation(
                    "escalation_code_mismatch",
                    f"The ticket was opened with {flagged_code}, but the submission says {code}.",
                ))  # fmt: skip
            if ticket_id not in draft:
                violations.append(Violation(
                    "draft_missing_id", f"The draft must quote the ticket_id {ticket_id}."
                ))  # fmt: skip
            problem = _check_employee_verified(ticket["args"].get("employee_id"), code, trace)
            if problem is not None:
                violations.append(problem)
        if records:
            violations.append(Violation(
                "conflicting_records",
                f"A decision ({records[-1]['observation']['decision_id']}) was recorded as well "
                "as an escalation: a request gets one outcome.",
            ))  # fmt: skip

    return violations
