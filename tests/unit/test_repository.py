import json
import re

import pytest

from militech_equipment.repository import (
    EmployeeRepository,
    JsonlError,
    append_jsonl,
    load_jsonl,
)
from tests.conftest import SEED_DIR


def test_load_jsonl_skips_blank_lines(tmp_path):
    path = tmp_path / "rows.jsonl"
    path.write_text('{"a": 1}\n\n{"a": 2}\n')

    assert load_jsonl(path) == [{"a": 1}, {"a": 2}]


def test_load_jsonl_reports_line_number_of_malformed_row(tmp_path):
    path = tmp_path / "rows.jsonl"
    path.write_text('{"a": 1}\n{"a": oops}\n')

    with pytest.raises(JsonlError, match=r"rows\.jsonl:2"):
        load_jsonl(path)


def test_load_jsonl_rejects_non_object_rows(tmp_path):
    path = tmp_path / "rows.jsonl"
    path.write_text("[1, 2]\n")

    with pytest.raises(JsonlError, match=r"rows\.jsonl:1"):
        load_jsonl(path)


def test_load_jsonl_missing_file_returns_empty(tmp_path):
    assert load_jsonl(tmp_path / "absent.jsonl") == []


def test_append_jsonl_creates_file_and_appends(tmp_path):
    path = tmp_path / "nested" / "out.jsonl"
    append_jsonl(path, {"n": 1})
    append_jsonl(path, {"n": 2})

    assert load_jsonl(path) == [{"n": 1}, {"n": 2}]


def test_employee_repository_get(data_dir):
    repo = EmployeeRepository.from_dir(data_dir)

    assert repo.get("E1004")["name"] == "Kira Vasquez"
    assert repo.get("E9999") is None


def test_employee_repository_rejects_duplicate_ids(tmp_path):
    row = {"employee_id": "E1001", "name": "A", "role": "netrunner", "hire_date": "2020-01-01"}
    (tmp_path / "employees.jsonl").write_text((json.dumps(row) + "\n") * 2)

    with pytest.raises(JsonlError, match="duplicate employee_id E1001"):
        EmployeeRepository.from_dir(tmp_path)


# --- seed data integrity: the demos and edge cases depend on these facts ---


def test_seed_has_eight_well_formed_employees():
    rows = load_jsonl(SEED_DIR / "employees.jsonl")

    assert [r["employee_id"] for r in rows] == [f"E100{i}" for i in range(1, 9)]
    assert all(re.fullmatch(r"E\d{4}", r["employee_id"]) for r in rows)


def test_seed_policies_cover_every_role_and_item():
    rows = load_jsonl(SEED_DIR / "policies.jsonl")
    pairs = {(r["role"], r["item"]) for r in rows}

    assert len(rows) == 24
    assert len(pairs) == 24  # no duplicates


# --- employee record validation: bad records fail at load, with the employee named ---


def _write_employee(directory, **overrides):
    unit = {"asset_id": "MT-1", "item": "terminal", "issued_date": "2025-01-01"}
    row = {
        "employee_id": "E2001",
        "name": "Test Subject",
        "role": "netrunner",
        "hire_date": "2020-01-01",
        "equipment": [unit],
    }
    row.update(overrides)
    (directory / "employees.jsonl").write_text(json.dumps(row) + "\n")


def test_employee_repository_normalizes_role_and_item_names(tmp_path):
    unit = {"asset_id": "MT-1", "item": " Terminal ", "issued_date": "2025-01-01"}
    _write_employee(tmp_path, role="Netrunner ", equipment=[unit])

    record = EmployeeRepository.from_dir(tmp_path).get("E2001")

    assert record["role"] == "netrunner"
    assert record["equipment"][0]["item"] == "terminal"


def test_employee_repository_defaults_equipment_to_empty(tmp_path):
    row = {"employee_id": "E2001", "name": "T", "role": "netrunner", "hire_date": "2020-01-01"}
    (tmp_path / "employees.jsonl").write_text(json.dumps(row) + "\n")

    assert EmployeeRepository.from_dir(tmp_path).get("E2001")["equipment"] == []


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"role": ""}, "role must be a non-empty string"),
        ({"role": None}, "role must be a non-empty string"),
        ({"hire_date": "2020-13-01"}, "hire_date: expected a YYYY-MM-DD date"),
        ({"hire_date": "20200101"}, "hire_date: expected a YYYY-MM-DD date"),
        ({"equipment": {}}, "equipment must be a list"),
        ({"equipment": ["terminal"]}, "equipment entries must be objects"),
        ({"equipment": [{"asset_id": "MT-1", "item": "terminal"}]}, "missing issued_date"),
        ({"equipment": [{"asset_id": "MT-1", "item": "combat_drone",
                         "issued_date": "2025-01-01"}]}, "unknown item 'combat_drone'"),
        ({"equipment": [{"asset_id": "MT-1", "item": "terminal",
                         "issued_date": "2025-02-30"}]}, "MT-1: issued_date"),
    ],
)  # fmt: skip
def test_employee_repository_rejects_malformed_records(tmp_path, overrides, message):
    _write_employee(tmp_path, **overrides)

    with pytest.raises(JsonlError, match=message):
        EmployeeRepository.from_dir(tmp_path)


def test_employee_repository_rejects_missing_fields(tmp_path):
    (tmp_path / "employees.jsonl").write_text('{"employee_id": "E2001", "name": "T"}\n')

    with pytest.raises(JsonlError, match="'E2001': missing role, hire_date"):
        EmployeeRepository.from_dir(tmp_path)
