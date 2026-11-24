"""check_request_eligibility: may this employee get one more of this item? (requirements §4.2)

Rule order (first match wins):
  1. unknown employee                                   -> employee_not_found
  2. item not in the catalog                            -> off_catalog
  3. unknown role / future issued_date / held > cap     -> data_conflict
  4. max_quantity == 0                                  -> not_entitled
  5. held < max                                         -> within_policy (additional)
  6. held == max, oldest unit >= refresh_months old     -> within_policy (replacement)
     otherwise                                          -> refresh_not_due (+ next_eligible_date)

Held count is the units on file. Pending approvals from decisions.jsonl join it in M6.
"""

from datetime import date
from typing import Any

from militech_equipment import dates
from militech_equipment.catalog import CATALOG, normalize_name
from militech_equipment.dates import add_months
from militech_equipment.employees import normalize_employee_id
from militech_equipment.policies import policy_repo
from militech_equipment.repository import EmployeeRepository, PolicyRepository


def check_request_eligibility(
    employee_id: Any,
    item: Any,
    *,
    employees: EmployeeRepository | None = None,
    policies: PolicyRepository | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Evaluate one request. Input errors come back as {"error": ...}; outcomes as a status."""
    normalized_id = normalize_employee_id(employee_id)
    if normalized_id is None:
        return {
            "error": "invalid_employee_id",
            "employee_id": employee_id,
            "message": "Employee IDs look like E#### (e.g. E1001).",
        }
    normalized_item = normalize_name(item)
    if normalized_item is None:
        return {"error": "invalid_item", "item": item, "message": "item must be a non-empty string"}

    shown_item = normalized_item if normalized_item in CATALOG else item  # off-catalog: as asked

    def outcome(status: str, eligible: bool = False, **details: Any) -> dict[str, Any]:
        base = {"eligible": eligible, "status": status, "employee_id": normalized_id}
        return {**base, "item": shown_item, **details}

    record = (employees or EmployeeRepository.from_dir()).get(normalized_id)
    if record is None:
        return outcome("employee_not_found")
    if normalized_item not in CATALOG:
        return outcome("off_catalog")

    role = record["role"]
    limits = policy_repo(policies).limits_for(role)
    if limits is None:
        return outcome("data_conflict", conflict="unknown_role", role=role)

    today = as_of or dates.today()
    issued = sorted(
        date.fromisoformat(unit["issued_date"])
        for unit in record.get("equipment", [])
        if unit["item"] == normalized_item
    )
    rule = limits[normalized_item]
    max_quantity, refresh_months = rule["max_quantity"], rule["refresh_months"]
    numbers = {
        "held": len(issued),
        "max_quantity": max_quantity,
        "refresh_months": refresh_months,
        "oldest_issued": issued[0].isoformat() if issued else None,
    }

    if issued and issued[-1] > today:
        return outcome("data_conflict", conflict="future_issued_date", **numbers)
    if len(issued) > max_quantity:
        return outcome("data_conflict", conflict="held_above_cap", **numbers)
    if max_quantity == 0:
        return outcome(
            "not_entitled", request_type_assumed=None, next_eligible_date=None, **numbers
        )
    if len(issued) < max_quantity:
        return outcome(
            "within_policy",
            True,
            request_type_assumed="additional",
            next_eligible_date=None,
            **numbers,
        )

    next_eligible = add_months(issued[0], refresh_months)
    due = next_eligible <= today
    return outcome(
        "within_policy" if due else "refresh_not_due",
        due,
        request_type_assumed="replacement",
        next_eligible_date=next_eligible.isoformat(),
        **numbers,
    )
