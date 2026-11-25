"""The six acceptance demos (requirements §9), driven in-process through the MCP tools by hand.

Each test makes the tool calls a well-behaved agent should make for that demo, on the pinned
2026-11-23, and checks the server leads to the expected outcome. When the agent arrives
(M8–M12) it must reach the same outcomes; these tests show the server alone supports them.

In-process: `Client(server)` connects to the server object in this process, so the clock can
be pinned (and moved) with monkeypatch. Each test runs in both of the SDK's in-process modes:
  - "legacy": in-memory streams carrying real JSON-RPC, initialize handshake included.
  - "auto":   the SDK's direct dispatcher (no streams or framing), the default path.
Fixtures `connect` and `pin_today` live in tests/integration/conftest.py. Starting the server
for real (`python -m`, stdio, env handoff) is covered by test_server.py.
"""

import json
from datetime import timedelta


async def _call(client, tool: str, **arguments) -> dict:
    result = await client.call_tool(tool, arguments)
    assert result.is_error is False
    return json.loads(result.content[0].text)


def _lines(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


async def test_demo_1_second_holo_display_is_approved(connect, data_dir):
    async with connect() as client:
        info = await _call(client, "get_employee_info", employee_id="E1001")
        check = await _call(
            client, "check_request_eligibility", employee_id="E1001", item="holo_display"
        )
        decision = await _call(
            client, "record_decision", employee_id="E1001", item="holo_display",
            decision="approve", request_type="additional",
            reason="Second display within the cyberware_tech cap of 2",
        )  # fmt: skip

    assert info["role"] == "cyberware_tech"
    assert (check["status"], check["request_type_assumed"]) == ("within_policy", "additional")
    assert decision["decision_id"] == "DEC-20261123-0001"


async def test_demo_1_follow_up_a_third_display_is_not_eligible(connect):
    async with connect() as client:
        args = {"employee_id": "E1001", "item": "holo_display"}
        await _call(client, "record_decision", **args, decision="approve",
                    request_type="additional", reason="Second display")  # fmt: skip
        again = await _call(client, "check_request_eligibility", **args)

    # The approval now counts: 2 of 2 held, so a third reads as a replacement of the
    # 2024-02-10 unit, which isn't due until 2027-02-10.
    assert (again["held"], again["status"]) == (2, "refresh_not_due")
    assert again["next_eligible_date"] == "2027-02-10"


async def test_demo_2_corpo_cyberdeck_is_denied(connect, data_dir):
    async with connect() as client:
        check = await _call(
            client, "check_request_eligibility", employee_id="E1003", item="cyberdeck"
        )
        approve = await _call(
            client, "record_decision", employee_id="E1003", item="cyberdeck",
            decision="approve", request_type="additional", reason="Lunch-break netrunning",
        )  # fmt: skip
        deny = await _call(
            client, "record_decision", employee_id="E1003", item="cyberdeck",
            decision="deny", request_type="additional",
            reason="NOT_ENTITLED: corpo_staff are never issued cyberdecks",
        )  # fmt: skip

    assert check["status"] == "not_entitled"
    assert (approve["error"], approve["status"]) == ("approval_not_permitted", "not_entitled")
    assert deny["decision_id"] == "DEC-20261123-0001"
    assert [d["decision"] for d in _lines(data_dir / "decisions.jsonl")] == ["deny"]


async def test_demo_3_emp_fried_terminal_is_escalated(connect, data_dir):
    async with connect() as client:
        check = await _call(
            client, "check_request_eligibility", employee_id="E1002", item="terminal"
        )
        approve = await _call(
            client, "record_decision", employee_id="E1002", item="terminal",
            decision="approve", request_type="replacement", reason="Fried by an EMP",
        )  # fmt: skip
        ticket = await _call(
            client, "flag_for_human_review", employee_id="E1002",
            request="An EMP surge on the maglev fried my terminal. Need a replacement.",
            reason="Damage claimed before the 36-month refresh", reason_code="EXCEPTION_CLAIMED",
        )  # fmt: skip

    assert (check["status"], check["next_eligible_date"]) == ("refresh_not_due", "2028-09-15")
    assert approve["error"] == "approval_not_permitted"  # policy says no; a human decides
    assert ticket["ticket_id"] == "ESC-20261123-0001"
    assert ticket["created_at"] == "2026-11-23T12:00:00+00:00"  # the pinned clock, noon UTC
    assert _lines(data_dir / "decisions.jsonl") == []


async def test_demo_4_combat_drone_is_escalated(connect, data_dir):
    async with connect() as client:
        check = await _call(
            client, "check_request_eligibility", employee_id="E1004", item="combat drone"
        )
        decide = await _call(
            client, "record_decision", employee_id="E1004", item="combat drone",
            decision="deny", request_type="additional", reason="Off catalog",
        )  # fmt: skip
        ticket = await _call(
            client, "flag_for_human_review", employee_id="E1004",
            request="Requisitioning a military-grade combat drone for a run next week.",
            reason="Combat drone is not a catalog item", reason_code="OFF_CATALOG",
        )  # fmt: skip

    assert check["status"] == "off_catalog"
    assert decide["error"] == "invalid_item"  # off-catalog items can't be decided, only escalated
    assert ticket["status"] == "open"


async def test_demo_5_unknown_employee_is_escalated(connect, data_dir):
    async with connect() as client:
        info = await _call(client, "get_employee_info", employee_id="E9999")
        check = await _call(
            client, "check_request_eligibility", employee_id="E9999", item="neural_headset"
        )
        decide = await _call(
            client, "record_decision", employee_id="E9999", item="neural_headset",
            decision="approve", request_type="additional", reason="Wants one",
        )  # fmt: skip
        ticket = await _call(
            client, "flag_for_human_review", employee_id="E9999",
            request="Need a neural headset, choom.", reason="Employee not on file",
            reason_code="EMPLOYEE_NOT_FOUND",
        )  # fmt: skip

    assert info["error"] == "employee_not_found"
    assert check["status"] == "employee_not_found"
    assert decide["error"] == "employee_not_found"
    assert ticket["ticket_id"] == "ESC-20261123-0001"
    assert _lines(data_dir / "escalations.jsonl")[0]["employee_id"] == "E9999"


async def test_demo_6_executive_terminal_replacement_is_approved(connect, data_dir):
    async with connect() as client:
        check = await _call(
            client, "check_request_eligibility", employee_id="E1005", item="terminal"
        )
        decision = await _call(
            client, "record_decision", employee_id="E1005", item="terminal",
            decision="approve", request_type="replacement",
            reason="Terminal is 24 months old, past the 18-month executive refresh",
        )  # fmt: skip

    assert (check["status"], check["request_type_assumed"]) == ("within_policy", "replacement")
    assert check["next_eligible_date"] == "2026-05-20"  # issued 2024-11-20 + 18 months
    assert decision["decision_id"] == "DEC-20261123-0001"


async def test_demo_6_follow_up_the_replacement_is_approved_only_once(connect):
    async with connect() as client:
        args = {"employee_id": "E1005", "item": "terminal", "decision": "approve",
                "request_type": "replacement", "reason": "Past its 18-month refresh"}  # fmt: skip
        first = await _call(client, "record_decision", **args)
        second = await _call(client, "record_decision", **args)

    # The approved replacement reset the terminal's date to today: next due in 18 months.
    assert first["decision_id"] == "DEC-20261123-0001"
    assert (second["error"], second["status"]) == ("approval_not_permitted", "refresh_not_due")
    assert second["next_eligible_date"] == "2028-05-23"


async def test_all_six_demos_in_one_session_leave_a_clean_audit_trail(connect, data_dir):
    async with connect() as client:
        for employee_id, item, request_type in [
            ("E1001", "holo_display", "additional"),
            ("E1005", "terminal", "replacement"),
        ]:
            await _call(
                client, "record_decision", employee_id=employee_id, item=item,
                decision="approve", request_type=request_type, reason="Within policy",
            )  # fmt: skip
        await _call(client, "record_decision", employee_id="E1003", item="cyberdeck",
                    decision="deny", request_type="additional", reason="NOT_ENTITLED")  # fmt: skip
        for employee_id, code in [
            ("E1002", "EXCEPTION_CLAIMED"),
            ("E1004", "OFF_CATALOG"),
            ("E9999", "EMPLOYEE_NOT_FOUND"),
        ]:
            await _call(client, "flag_for_human_review", employee_id=employee_id,
                        request="demo", reason="needs a human", reason_code=code)  # fmt: skip

    decisions = _lines(data_dir / "decisions.jsonl")
    escalations = _lines(data_dir / "escalations.jsonl")
    assert [d["decision_id"] for d in decisions] == [
        "DEC-20261123-0001", "DEC-20261123-0002", "DEC-20261123-0003",
    ]  # fmt: skip
    assert [(d["employee_id"], d["decision"]) for d in decisions] == [
        ("E1001", "approve"), ("E1005", "approve"), ("E1003", "deny"),
    ]  # fmt: skip
    assert [e["reason_code"] for e in escalations] == [
        "EXCEPTION_CLAIMED", "OFF_CATALOG", "EMPLOYEE_NOT_FOUND",
    ]  # fmt: skip
    assert [e["ticket_id"][-4:] for e in escalations] == ["0001", "0002", "0003"]


# --- the pinned clock reaches the server (only possible in-process) ---


async def test_tenure_uses_the_pinned_date(connect):
    async with connect() as client:
        info = await _call(client, "get_employee_info", employee_id="E1002")

    assert info["tenure_months"] == 39  # hired 2023-08-21, as of 2026-11-23


async def test_refresh_boundary_moves_with_the_pinned_date(connect, pin_today, as_of):
    async with connect() as client:
        # E1004's oldest holo-display hits its 24-month refresh exactly on as_of.
        args = {"employee_id": "E1004", "item": "holo_display"}
        on_the_day = await _call(client, "check_request_eligibility", **args)
        pin_today(as_of - timedelta(days=1))
        day_before = await _call(client, "check_request_eligibility", **args)

    assert on_the_day["status"] == "within_policy"
    assert day_before["status"] == "refresh_not_due"
