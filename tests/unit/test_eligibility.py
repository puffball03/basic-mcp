"""check_request_eligibility: the §4.2 rule, evaluated as of the pinned 2026-11-23."""

import json

import pytest

from militech_equipment.eligibility import check_request_eligibility
from militech_equipment.repository import EmployeeRepository, PolicyRepository


@pytest.fixture
def employees(data_dir):
    return EmployeeRepository.from_dir(data_dir)


@pytest.fixture
def policies(data_dir):
    return PolicyRepository.from_dir(data_dir)


@pytest.fixture
def check(monkeypatch, data_dir, employees, policies, as_of):
    # Decisions default to <data dir>/decisions.jsonl: point that at the temp copy, never ./data.
    monkeypatch.setenv("MILITECH_DATA_DIR", str(data_dir))

    def _check(employee_id, item, *, employee_repo=None, decisions=None):
        extra = {} if decisions is None else {"decisions": decisions}
        return check_request_eligibility(
            employee_id, item, employees=employee_repo or employees, policies=policies,
            as_of=as_of, **extra,
        )  # fmt: skip

    return _check


def _one_employee(role, *issued, item="terminal"):
    """A repo holding E2001 with one `item` unit per issued date."""
    units = [
        {"asset_id": f"MT-9{n:04d}", "item": item, "issued_date": d} for n, d in enumerate(issued)
    ]
    record = {"employee_id": "E2001", "name": "Test Subject", "role": role,
              "hire_date": "2020-01-01", "equipment": units}  # fmt: skip
    return EmployeeRepository({"E2001": record})


# --- one case per status, on the seed data ---


@pytest.mark.parametrize(
    ("employee_id", "item", "expected"),
    [
        # Demo 1: tech holds 1 of 2 holo-displays -> another one is within policy.
        ("E1001", "holo_display", {
            "eligible": True, "status": "within_policy", "request_type_assumed": "additional",
            "held": 1, "max_quantity": 2, "refresh_months": 36,
            "oldest_issued": "2024-02-10", "next_eligible_date": None,
        }),
        # Demo 6: executive terminal issued 2024-11-20, 18-month refresh -> replaceable.
        ("E1005", "terminal", {
            "eligible": True, "status": "within_policy", "request_type_assumed": "replacement",
            "held": 1, "max_quantity": 1, "refresh_months": 18,
            "oldest_issued": "2024-11-20", "next_eligible_date": "2026-05-20",
        }),
        # Corpo terminal is 14 months old against a 36-month refresh.
        ("E1002", "terminal", {
            "eligible": False, "status": "refresh_not_due", "request_type_assumed": "replacement",
            "held": 1, "max_quantity": 1, "refresh_months": 36,
            "oldest_issued": "2025-09-15", "next_eligible_date": "2028-09-15",
        }),
        # Demo 2: corpo staff are never entitled to a cyberdeck.
        ("E1003", "cyberdeck", {
            "eligible": False, "status": "not_entitled", "request_type_assumed": None,
            "held": 0, "max_quantity": 0, "refresh_months": 0,
            "oldest_issued": None, "next_eligible_date": None,
        }),
        # Netrunner with nothing on file -> first cyberdeck is an additional unit.
        ("E1006", "cyberdeck", {
            "eligible": True, "status": "within_policy", "request_type_assumed": "additional",
            "held": 0, "max_quantity": 1, "refresh_months": 18,
            "oldest_issued": None, "next_eligible_date": None,
        }),
    ],
)  # fmt: skip
def test_policy_statuses(check, employee_id, item, expected):
    result = check(employee_id, item)

    assert result == {"employee_id": employee_id, "item": item, "conflict": None, **expected}


# Every status carries the same keys; ones that don't apply are None.
NO_DETAILS = {
    "conflict": None, "request_type_assumed": None, "next_eligible_date": None,
    "held": None, "max_quantity": None, "refresh_months": None, "oldest_issued": None,
}  # fmt: skip


def test_employee_not_found(check):  # demo 5
    assert check("E9999", "terminal") == {
        "eligible": False,
        "status": "employee_not_found",
        "employee_id": "E9999",
        "item": "terminal",
        **NO_DETAILS,
    }


@pytest.mark.parametrize("item", ["combat drone", "combat_drone", "monitor", "laptop"])
def test_off_catalog(check, item):  # demo 4; synonyms are the agent's job, not the tool's
    assert check("E1004", item) == {
        "eligible": False,
        "status": "off_catalog",
        "employee_id": "E1004",
        "item": item,
        **NO_DETAILS,
    }


# --- data conflicts: the records can't be trusted, so a human must look ---


def test_data_conflict_future_issued_date(check):
    result = check("E1007", "holo_display")  # issued 2027-01-10

    assert result["status"] == "data_conflict"
    assert result["eligible"] is False
    assert result["conflict"] == "future_issued_date"


def test_future_date_on_another_item_does_not_block(check):
    assert check("E1007", "terminal")["status"] == "refresh_not_due"


@pytest.mark.parametrize(
    ("employee_id", "item"),
    [("E9999", "terminal"), ("E1004", "laptop"), ("E1008", "terminal"),
     ("E1007", "holo_display"), ("E1003", "cyberdeck"), ("E1001", "holo_display"),
     ("E1002", "terminal")],
)  # fmt: skip
def test_every_status_has_the_same_keys(check, employee_id, item):
    result = check(employee_id, item)

    assert set(NO_DETAILS) <= set(result)
    assert {"eligible", "status", "employee_id", "item"} <= set(result)


def test_mixed_case_names_in_employee_data_are_counted(check, tmp_path):
    unit = {"asset_id": "MT-1", "item": "Terminal", "issued_date": "2025-06-01"}
    row = {
        "employee_id": "E2001",
        "name": "T",
        "role": " Executive",
        "hire_date": "2020-01-01",
        "equipment": [unit],
    }
    (tmp_path / "employees.jsonl").write_text(json.dumps(row) + "\n")

    result = check("E2001", "terminal", employee_repo=EmployeeRepository.from_dir(tmp_path))

    assert result["held"] == 1  # "Terminal" counts toward the cap
    assert result["status"] == "refresh_not_due"  # role " Executive" resolves to executive


def test_data_conflict_unknown_role(check):
    result = check("E1008", "terminal")  # role "fixer" has no policies

    assert result["status"] == "data_conflict"
    assert result["conflict"] == "unknown_role"
    assert result["role"] == "fixer"


def test_data_conflict_held_above_cap(check):
    repo = _one_employee("executive", "2025-01-01", "2025-06-01")  # 2 terminals, cap 1

    result = check("E2001", "terminal", employee_repo=repo)

    assert result["status"] == "data_conflict"
    assert result["conflict"] == "held_above_cap"
    assert (result["held"], result["max_quantity"]) == (2, 1)


def test_held_item_the_role_is_not_entitled_to_is_a_conflict(check):
    repo = _one_employee("corpo_staff", "2025-01-01", item="cyberdeck")

    assert check("E2001", "cyberdeck", employee_repo=repo)["conflict"] == "held_above_cap"


# --- the refresh boundary ---


def test_unit_exactly_refresh_months_old_is_eligible(check):
    # E1004's oldest holo-display is 2024-11-23: exactly 24 months, at the 3-unit cap.
    result = check("E1004", "holo_display")

    assert result["status"] == "within_policy"
    assert result["request_type_assumed"] == "replacement"
    assert result["next_eligible_date"] == "2026-11-23"


def test_unit_one_day_short_of_refresh_is_not(check):
    repo = _one_employee("netrunner", "2024-11-24")  # terminal, 24-month refresh

    result = check("E2001", "terminal", employee_repo=repo)

    assert result["status"] == "refresh_not_due"
    assert result["next_eligible_date"] == "2026-11-24"


def test_replacement_uses_the_oldest_unit(check):
    repo = _one_employee("netrunner", "2026-01-01", "2024-01-01", item="haptic_keyboard")

    result = check("E2001", "haptic_keyboard", employee_repo=repo)

    assert result["oldest_issued"] == "2024-01-01"
    assert result["status"] == "within_policy"


def test_month_end_clamps(check):
    repo = _one_employee("executive", "2025-05-31")  # +18 months -> 2026-11-30

    assert check("E2001", "terminal", employee_repo=repo)["next_eligible_date"] == "2026-11-30"


# --- input normalization ---


@pytest.mark.parametrize("raw", ["Holo_Display", " HOLO_DISPLAY ", "holo_display\n"])
def test_item_is_case_and_whitespace_insensitive(check, raw):
    result = check("E1001", raw)

    assert result["item"] == "holo_display"
    assert result["status"] == "within_policy"


def test_employee_id_is_normalized(check):
    assert check(" e1001 ", "holo_display")["employee_id"] == "E1001"


@pytest.mark.parametrize("raw", ["1001", "EMP-1001", None, 7])
def test_malformed_employee_id(check, raw):
    assert check(raw, "terminal")["error"] == "invalid_employee_id"


@pytest.mark.parametrize("raw", ["", "   ", None, 3])
def test_missing_item(check, raw):
    assert check("E1001", raw)["error"] == "invalid_item"


def test_defaults_to_seed_data_and_today(monkeypatch, data_dir):
    monkeypatch.setenv("MILITECH_DATA_DIR", str(data_dir))

    assert check_request_eligibility("E1003", "cyberdeck")["status"] == "not_entitled"


# --- pending approvals from decisions.jsonl (requirements §4.2 step 5) ---


def _decision(employee_id, item, decision="approve", request_type="additional",
              created_at="2026-11-23T14:05:00+00:00", n=1):  # fmt: skip
    return {"decision_id": f"DEC-{created_at[:10].replace('-', '')}-{n:04d}",
            "employee_id": employee_id, "item": item, "decision": decision,
            "request_type": request_type, "reason": "test", "created_at": created_at}  # fmt: skip


def test_approved_additional_counts_toward_the_cap(check):
    # After demo 1's approval the tech holds 2 of 2, so a third request reads as a
    # replacement of the 2024-02-10 unit, which isn't due until 2027-02-10.
    result = check("E1001", "holo_display", decisions=[_decision("E1001", "holo_display")])

    assert result["held"] == 2
    assert result["request_type_assumed"] == "replacement"
    assert result["status"] == "refresh_not_due"
    assert result["next_eligible_date"] == "2027-02-10"


def test_approved_additional_unit_is_dated_by_the_decision(check):
    approved = _decision("E1006", "cyberdeck", created_at="2026-01-01T09:00:00+00:00")

    result = check("E1006", "cyberdeck", decisions=[approved])

    assert result["oldest_issued"] == "2026-01-01"
    assert result["next_eligible_date"] == "2027-07-01"  # + 18 months
    assert result["status"] == "refresh_not_due"


def test_approved_replacement_resets_the_oldest_unit(check):
    # Demo 6 approved; asking again straight away is no longer within policy.
    replaced = _decision("E1005", "terminal", request_type="replacement")

    result = check("E1005", "terminal", decisions=[replaced])

    assert result["held"] == 1
    assert result["oldest_issued"] == "2026-11-23"
    assert result["status"] == "refresh_not_due"
    assert result["next_eligible_date"] == "2028-05-23"


def test_replacement_moves_on_to_the_next_oldest_unit(check):
    # E1004's 2024-11-23 holo-display is replaced; the oldest is now 2025-04-02.
    replaced = _decision("E1004", "holo_display", request_type="replacement")

    result = check("E1004", "holo_display", decisions=[replaced])

    assert result["held"] == 3
    assert result["oldest_issued"] == "2025-04-02"
    assert result["next_eligible_date"] == "2027-04-02"


@pytest.mark.parametrize(
    "decision",
    [
        _decision("E1001", "holo_display", decision="deny"),  # denials hold nothing
        _decision("E1002", "holo_display"),  # another employee
        _decision("E1001", "terminal"),  # another item
    ],
)
def test_unrelated_decisions_are_ignored(check, decision):
    assert check("E1001", "holo_display", decisions=[decision])["held"] == 1


def test_decisions_default_to_the_data_dir(check, data_dir):
    approved = _decision("E1001", "holo_display")
    (data_dir / "decisions.jsonl").write_text(json.dumps(approved) + "\n")

    assert check("E1001", "holo_display")["held"] == 2
