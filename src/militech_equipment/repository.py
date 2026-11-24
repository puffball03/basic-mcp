"""JSONL storage: read seed data, append runtime records (requirements §10 / plan M2)."""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from militech_equipment.catalog import CATALOG, ROLES

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


class JsonlError(ValueError):
    """A JSONL file is malformed; the message names the file and line."""


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
    """Employees from employees.jsonl, keyed by employee_id."""

    employees: dict[str, dict[str, Any]]

    @classmethod
    def from_dir(cls, directory: Path | None = None) -> "EmployeeRepository":
        directory = Path(directory) if directory is not None else data_dir()
        employees: dict[str, dict[str, Any]] = {}
        for row in load_jsonl(directory / "employees.jsonl"):
            employee_id = row["employee_id"]
            if employee_id in employees:
                raise JsonlError(f"employees.jsonl: duplicate employee_id {employee_id}")
            employees[employee_id] = row
        return cls(employees)

    def get(self, employee_id: str) -> dict[str, Any] | None:
        return self.employees.get(employee_id)


@dataclass(frozen=True)
class PolicyRepository:
    """Policy rules from policies.jsonl: role -> item -> rule, every role x catalog item."""

    rules: dict[str, dict[str, dict[str, int]]]

    REQUIRED_FIELDS = ("role", "item", "max_quantity", "refresh_months")

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
            if role not in ROLES:
                raise JsonlError(f"policies.jsonl: {key}: unknown role {role}")
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

        gaps = [f"{r}/{i}" for r in ROLES for i in CATALOG if (r, i) not in found]
        if gaps:
            raise JsonlError(f"policies.jsonl: missing rules for {', '.join(gaps)}")
        return cls({role: {item: found[(role, item)] for item in CATALOG} for role in ROLES})

    def roles(self) -> list[str]:
        return list(self.rules)

    def limits_for(self, role: str) -> dict[str, dict[str, int]] | None:
        """Every catalog item's rule for `role`, in catalog order; None if the role is unknown."""
        return self.rules.get(role)
