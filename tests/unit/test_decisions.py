"""record_decision: the AI's final approve/deny, audited (requirements §5, §7).

The server refuses an approval the policy doesn't allow, whatever the agent concluded.
"""

import json
import re
from datetime import UTC, datetime, timedelta

import pytest

from militech_equipment.decisions import DECISIONS, REQUEST_TYPES, record_decision
from militech_equipment.repository import EmployeeRepository

NOW = datetime(2026, 11, 23, 14, 5, 0, tzinfo=UTC)  # same day as conftest.AS_OF


@pytest.fixture
def record(data_dir):
    """Call the tool against the temp data dir at a pinned instant."""

    def _record(employee_id="E1001", item="holo_display", decision="approve",
                request_type="additional", reason="Second display for implant diagnostics",
                *, now=NOW, employees=None):  # fmt: skip
        return record_decision(
            employee_id, item, decision, request_type, reason,
            directory=data_dir, now=now, employees=employees,
        )  # fmt: skip

    return _record


@pytest.fixture
def log(data_dir):
    """The decision records written so far."""

    def _log():
        path = data_dir / "decisions.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    return _log


def _one_employee(role, item, *issued):
    units = [
        {"asset_id": f"MT-9{n:04d}", "item": item, "issued_date": d} for n, d in enumerate(issued)
    ]
    record = {"employee_id": "E2001", "name": "Test Subject", "role": role,
              "hire_date": "2020-01-01", "equipment": units}  # fmt: skip
    return EmployeeRepository({"E2001": record})


def test_vocabularies_match_requirements():
    assert DECISIONS == ("approve", "deny")
    assert REQUEST_TYPES == ("additional", "replacement")


# --- the happy path ---


def test_approve_returns_the_decision(record):  # demo 1
    assert record() == {
        "decision_id": "DEC-20261123-0001",
        "created_at": "2026-11-23T14:05:00+00:00",
    }


def test_appends_exactly_one_record(record, log):
    record()

    assert log() == [
        {
            "decision_id": "DEC-20261123-0001",
            "employee_id": "E1001",
            "item": "holo_display",
            "decision": "approve",
            "request_type": "additional",
            "reason": "Second display for implant diagnostics",
            "created_at": "2026-11-23T14:05:00+00:00",
        }
    ]


def test_creates_the_file_if_missing(record, data_dir):
    assert not (data_dir / "decisions.jsonl").exists()

    record()

    assert (data_dir / "decisions.jsonl").exists()


def test_decision_id_format_and_sequence(record):
    first = record(decision="deny", reason="a")["decision_id"]
    second = record(decision="deny", reason="b")["decision_id"]
    next_day = record(decision="deny", reason="c", now=NOW + timedelta(days=1))["decision_id"]

    assert re.fullmatch(r"DEC-\d{8}-\d{4}", first)
    assert (first, second, next_day) == (
        "DEC-20261123-0001", "DEC-20261123-0002", "DEC-20261124-0001",
    )  # fmt: skip


def test_demo_6_executive_terminal_replacement_is_approved(record, log):
    result = record("E1005", "terminal", "approve", "replacement", "Terminal is past refresh")

    assert result["decision_id"] == "DEC-20261123-0001"
    assert log()[0]["request_type"] == "replacement"


# --- deny always records (given valid input) ---


@pytest.mark.parametrize(
    ("employee_id", "item", "request_type"),
    [
        ("E1003", "cyberdeck", "additional"),  # demo 2: not entitled
        ("E1002", "terminal", "replacement"),  # refresh not due
        ("E1001", "holo_display", "additional"),  # eligible, but the agent may still deny
    ],
)
def test_deny_records_whatever_eligibility_says(record, log, employee_id, item, request_type):
    result = record(employee_id, item, "deny", request_type, "Policy says no")

    assert result["decision_id"].startswith("DEC-")
    assert log()[0]["decision"] == "deny"


# --- the server-side guardrail: approvals must match the policy ---


def test_approve_refused_when_not_entitled(record, log):  # demo 2
    result = record("E1003", "cyberdeck", "approve", "additional", "Wants one")

    assert result["error"] == "approval_not_permitted"
    assert result["status"] == "not_entitled"
    assert log() == []


def test_approve_refused_when_refresh_not_due(record, log):
    result = record("E1002", "terminal", "approve", "replacement", "Slow")

    assert result["error"] == "approval_not_permitted"
    assert result["status"] == "refresh_not_due"
    assert result["next_eligible_date"] == "2028-09-15"
    assert log() == []


@pytest.mark.parametrize(
    ("employee_id", "item", "status"),
    [("E1007", "holo_display", "data_conflict"), ("E1008", "terminal", "data_conflict")],
)
def test_approve_refused_on_data_conflict(record, log, employee_id, item, status):
    result = record(employee_id, item, "approve", "replacement", "x")

    assert (result["error"], result["status"]) == ("approval_not_permitted", status)
    assert log() == []


@pytest.mark.parametrize(
    "employee_id",
    [
        "E1004",  # 3 of 3, oldest exactly 24 months old: a *replacement* would be approved
        "E1005",  # 2 of 2, oldest not yet past refresh
    ],
)
def test_explicit_additional_at_the_cap_is_refused(record, log, employee_id):
    # An *additional* unit at the cap is QUANTITY_CAP_REACHED, whatever the replacement
    # reading says (§4.2).
    result = record(employee_id, "holo_display", "approve", "additional", "Wants another")

    assert result["error"] == "approval_not_permitted"
    assert result["status"] == "quantity_cap_reached"
    assert log() == []


def test_explicit_replacement_below_the_cap_is_approved_once_past_refresh(record, log):
    employees = _one_employee("netrunner", "haptic_keyboard", "2024-01-01")  # 1 of 2, 12 mo
    result = record("E2001", "haptic_keyboard", "approve", "replacement", "Worn out keys",
                    employees=employees)  # fmt: skip

    assert result["decision_id"] == "DEC-20261123-0001"


def test_explicit_replacement_below_the_cap_is_refused_before_refresh(record, log):
    # E1001 holds 1 of 2 holo-displays, issued 2024-02-10 with a 36-month refresh.
    result = record("E1001", "holo_display", "approve", "replacement", "Want the new model")

    assert result["error"] == "approval_not_permitted"
    assert result["status"] == "refresh_not_due"
    assert result["next_eligible_date"] == "2027-02-10"
    assert log() == []


def test_replacement_of_nothing_is_refused(record, log):
    result = record("E1006", "cyberdeck", "approve", "replacement", "Replace my deck")

    assert result["error"] == "approval_not_permitted"
    assert result["status"] == "nothing_to_replace"
    assert log() == []


# --- bad input: an error result, and nothing written ---


@pytest.mark.parametrize("raw", ["maybe", "", None, "escalate"])
def test_invalid_decision(record, log, raw):
    result = record(decision=raw)

    assert result["error"] == "invalid_decision"
    assert result["valid_decisions"] == list(DECISIONS)
    assert log() == []


@pytest.mark.parametrize("raw", ["upgrade", "", None])
def test_invalid_request_type(record, log, raw):
    result = record(request_type=raw)

    assert result["error"] == "invalid_request_type"
    assert result["valid_request_types"] == list(REQUEST_TYPES)
    assert log() == []


@pytest.mark.parametrize("raw", ["combat_drone", "laptop", "", None])
def test_invalid_item(record, log, raw):
    assert record(item=raw)["error"] == "invalid_item"
    assert log() == []


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_missing_reason(record, log, raw):
    assert record(reason=raw) == {
        "error": "missing_field",
        "field": "reason",
        "message": "reason must be a non-empty string",
    }
    assert log() == []


def test_malformed_employee_id(record, log):
    assert record(employee_id="EMP-1001")["error"] == "invalid_employee_id"
    assert log() == []


@pytest.mark.parametrize("decision", DECISIONS)
def test_unknown_employee_cannot_be_decided(record, log, decision):
    # Unknown employees are escalated (EMPLOYEE_NOT_FOUND), never approved or denied.
    result = record("E9999", "terminal", decision, "additional", "x")

    assert result == {"error": "employee_not_found", "employee_id": "E9999"}
    assert log() == []


# --- input normalization ---


def test_inputs_are_normalized(record, log):
    record(" e1001 ", " Holo_Display ", " APPROVE ", "Additional", "  ok  ")

    written = log()[0]
    assert (written["employee_id"], written["item"], written["decision"]) == (
        "E1001", "holo_display", "approve",
    )  # fmt: skip
    assert (written["request_type"], written["reason"]) == ("additional", "ok")


def test_defaults_to_data_dir_and_now(monkeypatch, data_dir, log):
    monkeypatch.setenv("MILITECH_DATA_DIR", str(data_dir))

    result = record_decision("E1003", "cyberdeck", "deny", "additional", "Not entitled")

    assert result["decision_id"].startswith("DEC-")
    assert len(log()) == 1
