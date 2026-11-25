"""flag_for_human_review: escalate an ambiguous request to a human (requirements §6, §7)."""

import json
import re
from datetime import UTC, datetime, timedelta, timezone

import pytest

from militech_equipment.escalations import REASON_CODES, flag_for_human_review

NOW = datetime(2026, 11, 23, 14, 5, 0, tzinfo=UTC)


@pytest.fixture
def flag(data_dir):
    """Call the tool against the temp data dir at a pinned instant."""

    def _flag(employee_id="E1002", request="Need a new terminal", reason="EMP fried it",
              reason_code="EXCEPTION_CLAIMED", *, now=NOW):  # fmt: skip
        return flag_for_human_review(
            employee_id, request, reason, reason_code, directory=data_dir, now=now
        )

    return _flag


@pytest.fixture
def log(data_dir):
    """The escalation records written so far."""

    def _log():
        path = data_dir / "escalations.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    return _log


def test_reason_codes_match_requirements():
    assert REASON_CODES == (
        "OFF_CATALOG", "EXCEPTION_CLAIMED", "UNCLEAR_REQUEST",
        "EMPLOYEE_NOT_FOUND", "THIRD_PARTY_REQUEST", "DATA_CONFLICT",
    )  # fmt: skip


# --- the happy path: one ticket, one line ---


def test_returns_the_ticket(flag):
    assert flag() == {
        "ticket_id": "ESC-20261123-0001",
        "status": "open",
        "created_at": "2026-11-23T14:05:00+00:00",
    }


def test_appends_exactly_one_record(flag, log):
    flag()

    assert log() == [
        {
            "ticket_id": "ESC-20261123-0001",
            "employee_id": "E1002",
            "request": "Need a new terminal",
            "reason": "EMP fried it",
            "reason_code": "EXCEPTION_CLAIMED",
            "created_at": "2026-11-23T14:05:00+00:00",
            "status": "open",
        }
    ]


def test_creates_the_file_if_missing(flag, data_dir):
    assert not (data_dir / "escalations.jsonl").exists()

    flag()

    assert (data_dir / "escalations.jsonl").exists()


# --- ticket numbering ---


def test_ticket_id_format(flag):
    assert re.fullmatch(r"ESC-\d{8}-\d{4}", flag()["ticket_id"])


def test_sequence_increments_within_a_day(flag, log):
    ids = [flag()["ticket_id"] for _ in range(3)]

    assert ids == ["ESC-20261123-0001", "ESC-20261123-0002", "ESC-20261123-0003"]
    assert [r["ticket_id"] for r in log()] == ids


def test_sequence_restarts_on_a_new_day(flag):
    flag()
    flag()

    assert flag(now=NOW + timedelta(days=1))["ticket_id"] == "ESC-20261124-0001"


def test_sequence_continues_after_an_earlier_day(flag):
    flag()
    flag(now=NOW + timedelta(days=1))

    assert flag()["ticket_id"] == "ESC-20261123-0002"


def test_ticket_date_and_timestamp_are_utc(flag):
    eastern = timezone(timedelta(hours=-5))
    late_evening_eastern = datetime(2026, 11, 23, 23, 30, tzinfo=eastern)  # 04:30 UTC next day

    result = flag(now=late_evening_eastern)

    assert result["ticket_id"] == "ESC-20261124-0001"
    assert result["created_at"] == "2026-11-24T04:30:00+00:00"


# --- escalations that must always be possible ---


def test_unknown_employee_can_be_escalated(flag, log):  # demo 5
    result = flag("E9999", "Need a terminal", "Not on file", "EMPLOYEE_NOT_FOUND")

    assert result["status"] == "open"
    assert log()[0]["employee_id"] == "E9999"


@pytest.mark.parametrize("code", REASON_CODES)
def test_every_reason_code_is_accepted(flag, code):
    assert flag(reason_code=code)["status"] == "open"


# --- input normalization ---


def test_reason_code_is_case_and_whitespace_insensitive(flag, log):
    flag(reason_code=" off_catalog ")

    assert log()[0]["reason_code"] == "OFF_CATALOG"


def test_employee_id_is_normalized(flag, log):
    flag(employee_id=" e1002 ")

    assert log()[0]["employee_id"] == "E1002"


def test_request_and_reason_are_trimmed(flag, log):
    flag(request="  Need a terminal \n", reason="\tbroken  ")

    assert (log()[0]["request"], log()[0]["reason"]) == ("Need a terminal", "broken")


# --- bad input: an error result, and nothing written ---


@pytest.mark.parametrize("code", ["DAMAGED", "", "off catalog", None, 3])
def test_invalid_reason_code(flag, log, code):
    result = flag(reason_code=code)

    assert result["error"] == "invalid_reason_code"
    assert result["valid_codes"] == list(REASON_CODES)
    assert log() == []


@pytest.mark.parametrize("field", ["request", "reason"])
@pytest.mark.parametrize("value", ["", "   ", None, 7])
def test_empty_request_or_reason(flag, log, field, value):
    result = flag(**{field: value})

    assert result == {"error": "missing_field", "field": field,
                      "message": f"{field} must be a non-empty string"}  # fmt: skip
    assert log() == []


@pytest.mark.parametrize("raw", ["1002", "EMP-1002", "", None])
def test_malformed_employee_id(flag, log, raw):
    assert flag(employee_id=raw)["error"] == "invalid_employee_id"
    assert log() == []


def test_existing_records_are_preserved(flag, log, data_dir):
    earlier = {"ticket_id": "ESC-20261120-0001", "employee_id": "E1001", "request": "x",
               "reason": "y", "reason_code": "OFF_CATALOG",
               "created_at": "2026-11-20T09:00:00+00:00", "status": "open"}  # fmt: skip
    (data_dir / "escalations.jsonl").write_text(json.dumps(earlier) + "\n")

    flag()

    assert log()[0] == earlier
    assert len(log()) == 2


def test_defaults_to_data_dir_and_now(monkeypatch, data_dir, log):
    monkeypatch.setenv("MILITECH_DATA_DIR", str(data_dir))

    result = flag_for_human_review("E1003", "A cyberdeck", "For a project", "UNCLEAR_REQUEST")

    assert result["status"] == "open"
    assert len(log()) == 1
