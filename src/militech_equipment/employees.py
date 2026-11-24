"""get_employee_info: role, tenure and equipment on file (requirements §7)."""

import re
from datetime import date
from typing import Any

from militech_equipment import dates
from militech_equipment.dates import months_between
from militech_equipment.repository import EmployeeRepository

EMPLOYEE_ID_PATTERN = re.compile(r"E\d{4}")


def normalize_employee_id(raw: Any) -> str | None:
    """Return the canonical 'E####' form, or None if `raw` can't be an employee ID."""
    if not isinstance(raw, str):
        return None
    candidate = raw.strip().upper()
    return candidate if EMPLOYEE_ID_PATTERN.fullmatch(candidate) else None


def get_employee_info(
    employee_id: Any,
    *,
    repo: EmployeeRepository | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Look up an employee. Expected failures come back as {"error": ...}, never raised."""
    normalized = normalize_employee_id(employee_id)
    if normalized is None:
        return {
            "error": "invalid_employee_id",
            "employee_id": employee_id,
            "message": "Employee IDs look like E#### (e.g. E1001).",
        }

    repo = repo or EmployeeRepository.from_dir()
    record = repo.get(normalized)
    if record is None:
        return {"error": "employee_not_found", "employee_id": normalized}

    today = as_of or dates.today()
    hire_date = date.fromisoformat(record["hire_date"])
    equipment = sorted(
        (
            {
                "asset_id": unit["asset_id"],
                "item": unit["item"],
                "issued_date": unit["issued_date"],
            }
            for unit in record.get("equipment", [])
        ),
        key=lambda unit: (unit["issued_date"], unit["asset_id"]),
    )
    return {
        "employee_id": normalized,
        "name": record["name"],
        "role": record["role"],
        "hire_date": record["hire_date"],
        "tenure_months": months_between(hire_date, today),
        "equipment": equipment,
    }
