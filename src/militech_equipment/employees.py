"""get_employee_info: role, tenure and equipment on file (requirements §7)."""

import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from militech_equipment import dates
from militech_equipment.repository import EmployeeRepository, data_dir

EMPLOYEE_ID_PATTERN = re.compile(r"E\d{4}")


def normalize_employee_id(raw: Any) -> str | None:
    """Return the canonical 'E####' form, or None if `raw` can't be an employee ID."""
    if not isinstance(raw, str):
        return None
    candidate = raw.strip().upper()
    return candidate if EMPLOYEE_ID_PATTERN.fullmatch(candidate) else None


def invalid_employee_id_error(raw: Any) -> dict[str, Any]:
    """The shared error result for input that can't be an employee ID."""
    return {
        "error": "invalid_employee_id",
        "employee_id": raw,
        "message": "Employee IDs look like E#### (e.g. E1001).",
    }


@lru_cache(maxsize=8)
def _load_repo(directory: Path, mtime_ns: int) -> EmployeeRepository:
    """Parse employees.jsonl once per version of the file (`mtime_ns` is the cache key)."""
    return EmployeeRepository.from_dir(directory)


def employee_repo(repo: EmployeeRepository | None = None) -> EmployeeRepository:
    """`repo` if given, else the cached employees for the current data directory.

    Keyed on the file's mtime, so a rewritten employees.jsonl is picked up on the next call.
    """
    if repo is not None:
        return repo
    directory = data_dir()
    path = directory / "employees.jsonl"
    mtime_ns = path.stat().st_mtime_ns if path.exists() else -1
    return _load_repo(directory, mtime_ns)


def get_employee_info(
    employee_id: Any,
    *,
    repo: EmployeeRepository | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Look up an employee. Expected failures come back as {"error": ...}, never raised."""
    normalized = normalize_employee_id(employee_id)
    if normalized is None:
        return invalid_employee_id_error(employee_id)

    record = employee_repo(repo).get(normalized)
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
        "tenure_months": dates.months_between(hire_date, today),
        "equipment": equipment,
    }
