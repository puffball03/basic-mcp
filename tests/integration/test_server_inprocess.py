"""The real Militech MCP server, driven in-process by a real MCP client.

No subprocess: `Client(server)` connects to the server object in this process, so
`monkeypatch` reaches it and "today" can be pinned. That makes the date-dependent
demos testable at the protocol level. Each test runs in both of the SDK's in-process
modes:
  - "legacy": in-memory streams carrying real JSON-RPC, initialize handshake included.
  - "auto":   the SDK's direct dispatcher (no streams or framing), the default path.

Starting the server for real (`python -m`, stdio, env handoff) stays covered by
test_server.py.
"""

import json
from datetime import date, timedelta

import pytest
from mcp import Client

from militech_equipment import dates
from militech_equipment.server import server


@pytest.fixture
def pin_today(monkeypatch):
    """Pin dates.today() to a given day; returns the setter so a test can move it."""

    def _pin(day: date) -> None:
        monkeypatch.setattr(dates, "today", lambda: day)

    return _pin


@pytest.fixture(params=["legacy", "auto"])
def connect(request, monkeypatch, data_dir, as_of, pin_today):
    """A factory for a connected client: use it as `async with connect() as client:`.

    Not an async yield fixture: pytest-asyncio can tear those down in a different task
    than set them up, which breaks the SDK's anyio task groups.
    """
    monkeypatch.setenv("MILITECH_DATA_DIR", str(data_dir))
    pin_today(as_of)
    return lambda: Client(server, mode=request.param, raise_exceptions=True)


async def _call(client, tool: str, **arguments) -> dict:
    result = await client.call_tool(tool, arguments)
    assert result.is_error is False
    return json.loads(result.content[0].text)


async def test_exactly_the_three_tools_are_registered(connect):
    async with connect() as client:
        tools = (await client.list_tools()).tools

        assert sorted(tool.name for tool in tools) == [
            "check_request_eligibility",
            "get_employee_info",
            "get_policy_limits",
        ]


async def test_employee_tenure_uses_the_pinned_date(connect):
    async with connect() as client:
        info = await _call(client, "get_employee_info", employee_id="E1002")

        assert info["tenure_months"] == 39  # hired 2023-08-21, as of 2026-11-23


async def test_demo_6_executive_terminal_is_replaceable(connect):
    async with connect() as client:
        result = await _call(
            client, "check_request_eligibility", employee_id="E1005", item="terminal"
        )

        assert result["status"] == "within_policy"
        assert result["request_type_assumed"] == "replacement"
        assert result["next_eligible_date"] == "2026-05-20"


async def test_refresh_not_due_reports_next_eligible_date(connect):
    async with connect() as client:
        result = await _call(
            client, "check_request_eligibility", employee_id="E1002", item="terminal"
        )

        assert result["status"] == "refresh_not_due"
        assert result["next_eligible_date"] == "2028-09-15"


async def test_refresh_boundary_moves_with_the_pinned_date(connect, pin_today, as_of):
    async with connect() as client:
        # E1004's oldest holo-display hits its 24-month refresh exactly on as_of.
        on_the_day = await _call(
            client, "check_request_eligibility", employee_id="E1004", item="holo_display"
        )
        pin_today(as_of - timedelta(days=1))
        day_before = await _call(
            client, "check_request_eligibility", employee_id="E1004", item="holo_display"
        )

        assert on_the_day["status"] == "within_policy"
        assert day_before["status"] == "refresh_not_due"


async def test_input_errors_come_back_as_results_not_protocol_errors(connect):
    async with connect() as client:
        result = await _call(client, "get_employee_info", employee_id="EMP-1001")

        assert result["error"] == "invalid_employee_id"


async def test_unknown_role_lists_known_roles(connect):
    async with connect() as client:
        result = await _call(client, "get_policy_limits", role="fixer")

        assert result["error"] == "unknown_role"
        assert "netrunner" in result["known_roles"]
