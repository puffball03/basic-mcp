"""record_decision: the AI's final approve/deny, appended to decisions.jsonl (requirements §5, §7).

Server-side guardrail: an approval is written only if the policy allows it right now, whatever
the agent concluded. Denials always record (given valid input). Approved decisions then count
toward eligibility (see eligibility._held_dates).
"""

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from militech_equipment import dates
from militech_equipment.catalog import CATALOG, clean_text, normalize_name
from militech_equipment.eligibility import DECISIONS_FILE, check_request_eligibility
from militech_equipment.employees import (
    employee_repo,
    invalid_employee_id_error,
    normalize_employee_id,
)
from militech_equipment.repository import (
    EmployeeRepository,
    PolicyRepository,
    append_jsonl,
    data_dir,
    load_jsonl,
    next_daily_id,
)

DECISIONS: tuple[str, ...] = ("approve", "deny")
REQUEST_TYPES: tuple[str, ...] = ("additional", "replacement")


def _refused(eligibility: dict[str, Any], status: str, message: str) -> dict[str, Any]:
    return {
        "error": "approval_not_permitted",
        "status": status,
        "message": message,
        "next_eligible_date": eligibility["next_eligible_date"],
        "held": eligibility["held"],
        "max_quantity": eligibility["max_quantity"],
    }


def _approval_refusal(
    eligibility: dict[str, Any], request_type: str, today: date
) -> dict[str, Any] | None:
    """Why this approval breaks policy (requirements §4.2), or None if it may be recorded."""
    status = eligibility["status"]
    if status not in ("within_policy", "refresh_not_due"):
        return _refused(eligibility, status, "Policy does not allow this item to be approved.")

    at_cap = eligibility["request_type_assumed"] == "replacement"
    if request_type == "additional":
        if at_cap:
            return _refused(
                eligibility, "quantity_cap_reached", "Already at the cap; deny or escalate."
            )
        return None  # below the cap: within policy as an additional unit

    if eligibility["held"] == 0:
        return _refused(eligibility, "nothing_to_replace", "No unit of this item on file.")
    if at_cap:
        due = eligibility["eligible"]
        next_eligible = eligibility["next_eligible_date"]
    else:  # explicit replacement below the cap: check the oldest unit ourselves
        oldest = date.fromisoformat(eligibility["oldest_issued"])
        next_eligible = dates.add_months(oldest, eligibility["refresh_months"]).isoformat()
        due = date.fromisoformat(next_eligible) <= today
    if due:
        return None
    refusal = _refused(eligibility, "refresh_not_due", "The oldest unit is not due for refresh.")
    return {**refusal, "next_eligible_date": next_eligible}


def _invalid(error: str, field: str, value: Any, valid: tuple[str, ...]) -> dict[str, Any]:
    return {"error": error, field: value, f"valid_{field}s": list(valid)}


def record_decision(
    employee_id: Any,
    item: Any,
    decision: Any,
    request_type: Any,
    reason: Any,
    *,
    directory: Path | None = None,
    now: datetime | None = None,
    employees: EmployeeRepository | None = None,
    policies: PolicyRepository | None = None,
) -> dict[str, Any]:
    """Record a final decision. Bad input or a refused approval writes nothing."""
    normalized_id = normalize_employee_id(employee_id)
    if normalized_id is None:
        return invalid_employee_id_error(employee_id)
    normalized_item = normalize_name(item)
    if normalized_item not in CATALOG:
        return {
            "error": "invalid_item",
            "item": item,
            "message": f"item must be one of {', '.join(CATALOG)}",
        }
    normalized_decision = normalize_name(decision)
    if normalized_decision not in DECISIONS:
        return _invalid("invalid_decision", "decision", decision, DECISIONS)
    normalized_type = normalize_name(request_type)
    if normalized_type not in REQUEST_TYPES:
        return _invalid("invalid_request_type", "request_type", request_type, REQUEST_TYPES)
    cleaned_reason = clean_text(reason)
    if cleaned_reason is None:
        return {
            "error": "missing_field",
            "field": "reason",
            "message": "reason must be a non-empty string",
        }

    employees = employee_repo(employees)
    if employees.get(normalized_id) is None:  # unknown employees are escalated, not decided
        return {"error": "employee_not_found", "employee_id": normalized_id}

    created = (now or dates.now()).astimezone(UTC)
    path = Path(directory or data_dir()) / DECISIONS_FILE
    if normalized_decision == "approve":
        eligibility = check_request_eligibility(
            normalized_id,
            normalized_item,
            employees=employees,
            policies=policies,
            as_of=created.date(),
            decisions=load_jsonl(path),
        )
        refusal = _approval_refusal(eligibility, normalized_type, created.date())
        if refusal is not None:
            return refusal

    record = {
        "decision_id": next_daily_id(path, "DEC", "decision_id", created),
        "employee_id": normalized_id,
        "item": normalized_item,
        "decision": normalized_decision,
        "request_type": normalized_type,
        "reason": cleaned_reason,
        "created_at": created.isoformat(timespec="seconds"),
    }
    append_jsonl(path, record)
    return {"decision_id": record["decision_id"], "created_at": record["created_at"]}
