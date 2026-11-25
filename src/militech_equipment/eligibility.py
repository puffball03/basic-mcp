"""check_request_eligibility: may this employee get one more of this item? (requirements §4.2)

Rule order (first match wins):
  1. unknown employee                                   -> employee_not_found
  2. item not in the catalog                            -> off_catalog
  3. unknown role / future issued_date / held > cap     -> data_conflict
  4. max_quantity == 0                                  -> not_entitled
  5. held < max                                         -> within_policy (additional)
  6. held == max, oldest unit >= refresh_months old     -> within_policy (replacement)
     otherwise                                          -> refresh_not_due (+ next_eligible_date)

Held count is the units on file plus approvals in decisions.jsonl (rule step 5): an approved
additional unit counts as issued on the decision date, and an approved replacement resets
the oldest unit's date to the decision date. Denials hold nothing.
"""

from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

from militech_equipment import dates
from militech_equipment.catalog import CATALOG, normalize_name
from militech_equipment.employees import (
    employee_repo,
    invalid_employee_id_error,
    normalize_employee_id,
)
from militech_equipment.policies import policy_repo
from militech_equipment.repository import (
    EmployeeRepository,
    PolicyRepository,
    data_dir,
    load_jsonl,
)

DECISIONS_FILE = "decisions.jsonl"

# Every outcome carries these keys (None when they don't apply), so callers get one shape.
_EMPTY_DETAILS: dict[str, Any] = {
    "conflict": None,
    "request_type_assumed": None,
    "next_eligible_date": None,
    "held": None,
    "max_quantity": None,
    "refresh_months": None,
    "oldest_issued": None,
}


def _outcome(
    status: str, employee_id: str, item: Any, *, eligible: bool = False, **details: Any
) -> dict[str, Any]:
    head = {"eligible": eligible, "status": status, "employee_id": employee_id, "item": item}
    return {**head, **_EMPTY_DETAILS, **details}


def load_decisions() -> list[dict[str, Any]]:
    """Every recorded decision in the current data directory (empty if none yet)."""
    return load_jsonl(data_dir() / DECISIONS_FILE)


def _held_dates(
    units: Iterable[date], employee_id: str, item: str, decisions: Iterable[dict[str, Any]]
) -> list[date]:
    """Issue dates of the units this employee holds, with approved decisions applied in order."""
    held = sorted(units)
    approvals = sorted(
        (
            d
            for d in decisions
            if d.get("decision") == "approve"
            and d.get("employee_id") == employee_id
            and d.get("item") == item
        ),
        key=lambda d: d["created_at"],
    )
    for approval in approvals:
        decided = datetime.fromisoformat(approval["created_at"]).date()
        if approval.get("request_type") == "replacement":
            if not held:
                continue  # nothing to replace (record_decision refuses these)
            held.pop(0)  # the oldest unit is swapped for a new one
        held.append(decided)
        held.sort()
    return held


def check_request_eligibility(
    employee_id: Any,
    item: Any,
    *,
    employees: EmployeeRepository | None = None,
    policies: PolicyRepository | None = None,
    as_of: date | None = None,
    decisions: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Evaluate one request. Input errors come back as {"error": ...}; outcomes as a status."""
    normalized_id = normalize_employee_id(employee_id)
    if normalized_id is None:
        return invalid_employee_id_error(employee_id)
    normalized_item = normalize_name(item)
    if normalized_item is None:
        return {"error": "invalid_item", "item": item, "message": "item must be a non-empty string"}

    shown_item = normalized_item if normalized_item in CATALOG else item  # off-catalog: as asked
    record = employee_repo(employees).get(normalized_id)
    if record is None:
        return _outcome("employee_not_found", normalized_id, shown_item)
    if normalized_item not in CATALOG:
        return _outcome("off_catalog", normalized_id, shown_item)

    role = record["role"]  # normalized by EmployeeRepository
    limits = policy_repo(policies).limits_for(role)
    if limits is None:
        return _outcome(
            "data_conflict", normalized_id, shown_item, conflict="unknown_role", role=role
        )

    today = as_of or dates.today()
    issued = _held_dates(
        (
            date.fromisoformat(unit["issued_date"])
            for unit in record["equipment"]
            if unit["item"] == normalized_item
        ),
        normalized_id,
        normalized_item,
        load_decisions() if decisions is None else decisions,
    )
    rule = limits[normalized_item]
    max_quantity, refresh_months = rule["max_quantity"], rule["refresh_months"]
    numbers = {
        "held": len(issued),
        "max_quantity": max_quantity,
        "refresh_months": refresh_months,
        "oldest_issued": issued[0].isoformat() if issued else None,
    }

    def result(status: str, **details: Any) -> dict[str, Any]:
        return _outcome(status, normalized_id, shown_item, **numbers, **details)

    # Future dates are checked per requested item (requirements §4.2 rule 3).
    if issued and issued[-1] > today:
        return result("data_conflict", conflict="future_issued_date")
    if len(issued) > max_quantity:
        return result("data_conflict", conflict="held_above_cap")
    if max_quantity == 0:
        return result("not_entitled")
    if len(issued) < max_quantity:
        return result("within_policy", eligible=True, request_type_assumed="additional")

    next_eligible = dates.add_months(issued[0], refresh_months)
    due = next_eligible <= today
    return result(
        "within_policy" if due else "refresh_not_due",
        eligible=due,
        request_type_assumed="replacement",
        next_eligible_date=next_eligible.isoformat(),
    )
