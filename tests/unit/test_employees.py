import os

import pytest

from militech_equipment.employees import employee_repo, get_employee_info
from militech_equipment.repository import EmployeeRepository


@pytest.fixture
def repo(data_dir):
    return EmployeeRepository.from_dir(data_dir)


def test_returns_profile_tenure_and_equipment(repo, as_of):
    info = get_employee_info("E1002", repo=repo, as_of=as_of)

    assert info == {
        "employee_id": "E1002",
        "name": "Mina Castellano",
        "role": "corpo_staff",
        "hire_date": "2023-08-21",
        "tenure_months": 39,
        "equipment": [
            {"asset_id": "MT-10022", "item": "holo_display", "issued_date": "2024-01-08"},
            {"asset_id": "MT-10023", "item": "haptic_keyboard", "issued_date": "2024-05-02"},
            {"asset_id": "MT-10024", "item": "gesture_band", "issued_date": "2024-05-02"},
            {"asset_id": "MT-10021", "item": "terminal", "issued_date": "2025-09-15"},
        ],
    }


def test_equipment_is_sorted_oldest_first(repo, as_of):
    info = get_employee_info("E1004", repo=repo, as_of=as_of)
    dates = [unit["issued_date"] for unit in info["equipment"]]

    assert dates == sorted(dates)


def test_new_hire_tenure(repo, as_of):
    assert get_employee_info("E1003", repo=repo, as_of=as_of)["tenure_months"] == 2


def test_employee_with_no_equipment(repo, as_of):
    assert get_employee_info("E1006", repo=repo, as_of=as_of)["equipment"] == []


def test_unknown_role_is_returned_as_is(repo, as_of):
    # Data conflicts are judged by eligibility (M4); the info tool just reports the record.
    assert get_employee_info("E1008", repo=repo, as_of=as_of)["role"] == "fixer"


@pytest.mark.parametrize("raw", ["e1001", " E1001 ", "E1001\n"])
def test_id_is_normalized(repo, as_of, raw):
    assert get_employee_info(raw, repo=repo, as_of=as_of)["employee_id"] == "E1001"


def test_unknown_employee(repo, as_of):
    assert get_employee_info("E9999", repo=repo, as_of=as_of) == {
        "error": "employee_not_found",
        "employee_id": "E9999",
    }


@pytest.mark.parametrize("raw", ["", "1001", "E10011", "EMP-1001", "E1O01", None, 1001])
def test_malformed_id(repo, as_of, raw):
    result = get_employee_info(raw, repo=repo, as_of=as_of)

    assert result["error"] == "invalid_employee_id"
    assert "E####" in result["message"]


def test_defaults_to_today_and_seed_data(monkeypatch, data_dir):
    monkeypatch.setenv("MILITECH_DATA_DIR", str(data_dir))

    info = get_employee_info("E1001")

    assert info["name"] == "Reyes Okafor"
    assert isinstance(info["tenure_months"], int)


def test_default_repo_is_cached_until_the_file_changes(monkeypatch, data_dir):
    monkeypatch.setenv("MILITECH_DATA_DIR", str(data_dir))
    first = employee_repo()
    assert employee_repo() is first  # no re-parse while the file is unchanged

    path = data_dir / "employees.jsonl"
    path.write_text(path.read_text().replace("Reyes Okafor", "Reyes Renamed"))
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    assert employee_repo().get("E1001")["name"] == "Reyes Renamed"
