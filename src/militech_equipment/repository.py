"""JSONL storage: read seed data, append runtime records (requirements §10 / plan M2)."""

import json
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from militech_equipment.catalog import CATALOG, normalize_name

ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


class JsonlError(ValueError):
    """A JSONL file is malformed; the message names the file and line."""


def _check_iso_date(value: Any, where: str) -> None:
    """Raise JsonlError unless `value` is a YYYY-MM-DD string."""
    try:
        if not (isinstance(value, str) and ISO_DATE.fullmatch(value)):
            raise ValueError
        date.fromisoformat(value)  # also rejects impossible dates like 2025-13-01
    except ValueError:
        raise JsonlError(f"{where}: expected a YYYY-MM-DD date, got {value!r}") from None


def data_dir() -> Path:
    """Data directory from MILITECH_DATA_DIR, else the repo's ./data."""
    return Path(os.environ.get("MILITECH_DATA_DIR", DEFAULT_DATA_DIR))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read one JSON object per line. Blank lines are skipped; a missing file is empty."""
    path = Path(path)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise JsonlError(f"{path.name}:{line_no}: invalid JSON ({exc.msg})") from exc
            if not isinstance(row, dict):
                raise JsonlError(f"{path.name}:{line_no}: expected a JSON object")
            rows.append(row)
    return rows


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Append one record as a single line, creating the file and parents if needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


@dataclass(frozen=True)
class EmployeeRepository:
    """Employees from employees.jsonl, keyed by employee_id.

    Records are validated and normalized at load (role and item names lowercased, dates
    checked as ISO), so tools can trust their shape. Policy questions (unknown role, future
    issued_date, held above cap) are left for check_request_eligibility to report.
    """

    employees: dict[str, dict[str, Any]]

    REQUIRED_FIELDS = ("employee_id", "name", "role", "hire_date")
    UNIT_FIELDS = ("asset_id", "item", "issued_date")

    @classmethod
    def from_dir(cls, directory: Path | None = None) -> "EmployeeRepository":
        directory = Path(directory) if directory is not None else data_dir()
        employees: dict[str, dict[str, Any]] = {}
        for row in load_jsonl(directory / "employees.jsonl"):
            employee_id = row.get("employee_id")
            if employee_id in employees:
                raise JsonlError(f"employees.jsonl: duplicate employee_id {employee_id}")
            employees[employee_id] = cls._validated(row)
        return cls(employees)

    @classmethod
    def _validated(cls, row: dict[str, Any]) -> dict[str, Any]:
        where = f"employees.jsonl: {row.get('employee_id')!r}"
        missing = [field for field in cls.REQUIRED_FIELDS if field not in row]
        if missing:
            raise JsonlError(f"{where}: missing {', '.join(missing)}")
        role = normalize_name(row["role"])
        if role is None:
            raise JsonlError(f"{where}: role must be a non-empty string")
        _check_iso_date(row["hire_date"], f"{where}: hire_date")

        equipment = row.get("equipment", [])
        if not isinstance(equipment, list):
            raise JsonlError(f"{where}: equipment must be a list")
        units = []
        for unit in equipment:
            if not isinstance(unit, dict):
                raise JsonlError(f"{where}: equipment entries must be objects")
            missing = [field for field in cls.UNIT_FIELDS if field not in unit]
            if missing:
                raise JsonlError(f"{where}: equipment unit missing {', '.join(missing)}")
            unit_where = f"{where}: {unit['asset_id']}"
            item = normalize_name(unit["item"])
            if item not in CATALOG:
                raise JsonlError(f"{unit_where}: unknown item {unit['item']!r}")
            _check_iso_date(unit["issued_date"], f"{unit_where}: issued_date")
            units.append({**unit, "item": item})
        return {**row, "role": role, "equipment": units}

    def get(self, employee_id: str) -> dict[str, Any] | None:
        return self.employees.get(employee_id)


@dataclass(frozen=True)
class PolicyRepository:
    """Policy rules from policies.jsonl: role -> item -> rule.

    Roles are inferred from the file (no hardcoded list); every role it names must have a
    rule for every catalog item.
    """

    rules: dict[str, dict[str, dict[str, int]]]

    REQUIRED_FIELDS = ("role", "item", "max_quantity", "refresh_months")
    ROLE_NAME = re.compile(r"[a-z][a-z0-9_]*")  # already normalized, e.g. corpo_staff

    @classmethod
    def from_dir(cls, directory: Path | None = None) -> "PolicyRepository":
        """Load and validate the policy table; any gap or bad value raises JsonlError."""
        directory = Path(directory) if directory is not None else data_dir()
        path = directory / "policies.jsonl"
        if not path.exists():
            raise JsonlError(f"policies.jsonl not found in {directory}")

        found: dict[tuple[str, str], dict[str, int]] = {}
        for row in load_jsonl(path):
            missing = [field for field in cls.REQUIRED_FIELDS if field not in row]
            if missing:
                raise JsonlError(f"policies.jsonl: {row!r}: missing {', '.join(missing)}")
            role, item = row["role"], row["item"]
            key = f"{role}/{item}"
            if not (isinstance(role, str) and cls.ROLE_NAME.fullmatch(role)):
                raise JsonlError(f"policies.jsonl: {key}: invalid role name {role!r}")
            if item not in CATALOG:
                raise JsonlError(f"policies.jsonl: {key}: unknown item {item}")
            if (role, item) in found:
                raise JsonlError(f"policies.jsonl: duplicate rule {key}")
            max_quantity, refresh_months = row["max_quantity"], row["refresh_months"]
            # type() rather than isinstance(): bool is an int subclass and must be rejected.
            if not all(type(n) is int and n >= 0 for n in (max_quantity, refresh_months)):
                raise JsonlError(f"policies.jsonl: {key}: limits must be non-negative integers")
            if max_quantity > 0 and refresh_months == 0:
                raise JsonlError(f"policies.jsonl: {key}: entitled items need refresh_months > 0")
            found[(role, item)] = {"max_quantity": max_quantity, "refresh_months": refresh_months}

        roles = list(dict.fromkeys(role for role, _ in found))  # file order
        if not roles:
            raise JsonlError(f"policies.jsonl: no policy rules in {directory}")
        gaps = [f"{r}/{i}" for r in roles for i in CATALOG if (r, i) not in found]
        if gaps:
            raise JsonlError(f"policies.jsonl: missing rules for {', '.join(gaps)}")
        return cls({role: {item: found[(role, item)] for item in CATALOG} for role in roles})

    def roles(self) -> list[str]:
        return list(self.rules)

    def limits_for(self, role: str) -> dict[str, dict[str, int]] | None:
        """Every catalog item's rule for `role`, in catalog order; None if the role is unknown."""
        return self.rules.get(role)
