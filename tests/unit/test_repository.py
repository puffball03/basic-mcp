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
    (tmp_path / "employees.jsonl").write_text(
        '{"employee_id": "E1001"}\n{"employee_id": "E1001"}\n'
    )

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
