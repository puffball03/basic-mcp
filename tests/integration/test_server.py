"""The real Militech MCP server, driven over stdio by a real MCP client."""

import json
import os
import re
import sys

import pytest
from mcp import Client, StdioServerParameters

from militech_equipment.catalog import CATALOG
from militech_equipment.decisions import DECISIONS, REQUEST_TYPES
from militech_equipment.escalations import REASON_CODES
from militech_equipment.repository import PolicyRepository


@pytest.fixture
def server_params(data_dir) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "militech_equipment.server"],
        env={**os.environ, "MILITECH_DATA_DIR": str(data_dir)},
    )


def _payload(result) -> dict:
    """Decode a tool result's JSON text content."""
    assert result.is_error is False
    return json.loads(result.content[0].text)


async def test_get_employee_info_is_registered(server_params):
    async with Client(server_params) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    tool = tools["get_employee_info"]
    assert tool.input_schema["required"] == ["employee_id"]
    assert "E####" in tool.description
    assert "\n    " not in tool.description  # explicit description=, not a docstring


async def test_get_employee_info_round_trip(server_params):
    async with Client(server_params) as client:
        result = await client.call_tool("get_employee_info", {"employee_id": "E1004"})

    info = _payload(result)
    assert info["name"] == "Kira Vasquez"
    assert info["role"] == "netrunner"
    assert len(info["equipment"]) == 6


async def test_get_employee_info_error_survives_protocol(server_params):
    async with Client(server_params) as client:
        result = await client.call_tool("get_employee_info", {"employee_id": "E9999"})

    assert _payload(result) == {"error": "employee_not_found", "employee_id": "E9999"}


async def test_get_policy_limits_is_registered(server_params, data_dir):
    async with Client(server_params) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    tool = tools["get_policy_limits"]
    assert tool.input_schema["required"] == ["role"]
    # The description lists the roles the policy data defines, not a hardcoded list.
    assert all(role in tool.description for role in PolicyRepository.from_dir(data_dir).roles())


async def test_get_policy_limits_round_trip(server_params):
    async with Client(server_params) as client:
        result = await client.call_tool("get_policy_limits", {"role": "netrunner"})

    limits = _payload(result)["limits"]
    assert len(limits) == 6
    assert limits["cyberdeck"] == {"max_quantity": 1, "refresh_months": 18, "entitled": True}


async def test_get_policy_limits_error_survives_protocol(server_params):
    async with Client(server_params) as client:
        result = await client.call_tool("get_policy_limits", {"role": "fixer"})

    assert _payload(result)["error"] == "unknown_role"


async def test_check_request_eligibility_is_registered(server_params):
    async with Client(server_params) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    tool = tools["check_request_eligibility"]
    assert tool.input_schema["required"] == ["employee_id", "item"]
    # The description lists the catalog from CATALOG, not a hand-typed copy, and carries no
    # docstring indentation into what the LLM reads.
    assert all(item in tool.description for item in CATALOG)
    assert "\n    " not in tool.description


async def test_check_request_eligibility_round_trip(server_params):
    # Demo 2: not entitled regardless of date, so this holds whatever "today" is.
    async with Client(server_params) as client:
        result = await client.call_tool(
            "check_request_eligibility", {"employee_id": "E1003", "item": "cyberdeck"}
        )

    payload = _payload(result)
    assert payload["status"] == "not_entitled"
    assert payload["eligible"] is False


async def test_check_request_eligibility_off_catalog_survives_protocol(server_params):
    async with Client(server_params) as client:
        result = await client.call_tool(
            "check_request_eligibility", {"employee_id": "E1004", "item": "combat drone"}
        )

    assert _payload(result)["status"] == "off_catalog"


async def test_flag_for_human_review_is_registered(server_params):
    async with Client(server_params) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    tool = tools["flag_for_human_review"]
    assert tool.input_schema["required"] == ["employee_id", "request", "reason", "reason_code"]
    # Reason codes are listed from REASON_CODES, and no docstring indentation leaks through.
    assert all(code in tool.description for code in REASON_CODES)
    assert "\n    " not in tool.description


async def test_flag_for_human_review_writes_to_the_data_dir(server_params, data_dir):
    async with Client(server_params) as client:
        result = await client.call_tool(
            "flag_for_human_review",
            {"employee_id": "E1004", "request": "I need a combat drone",
             "reason": "Not a catalog item", "reason_code": "OFF_CATALOG"},
        )  # fmt: skip

    ticket = _payload(result)
    assert re.fullmatch(r"ESC-\d{8}-0001", ticket["ticket_id"])
    assert ticket["status"] == "open"

    lines = (data_dir / "escalations.jsonl").read_text().splitlines()
    assert [json.loads(line)["ticket_id"] for line in lines] == [ticket["ticket_id"]]


async def test_flag_for_human_review_error_survives_protocol(server_params, data_dir):
    async with Client(server_params) as client:
        result = await client.call_tool(
            "flag_for_human_review",
            {"employee_id": "E1004", "request": "x", "reason": "y", "reason_code": "BOGUS"},
        )

    assert _payload(result)["error"] == "invalid_reason_code"
    assert not (data_dir / "escalations.jsonl").exists()


async def test_record_decision_is_registered(server_params):
    async with Client(server_params) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    tool = tools["record_decision"]
    assert tool.input_schema["required"] == [
        "employee_id", "item", "decision", "request_type", "reason",
    ]  # fmt: skip
    assert all(word in tool.description for word in (*DECISIONS, *REQUEST_TYPES, *CATALOG))
    assert "\n    " not in tool.description


async def test_record_decision_deny_writes_to_the_data_dir(server_params, data_dir):
    async with Client(server_params) as client:
        result = await client.call_tool(
            "record_decision",
            {"employee_id": "E1003", "item": "cyberdeck", "decision": "deny",
             "request_type": "additional", "reason": "Corpo staff are not entitled"},
        )  # fmt: skip

    decision = _payload(result)
    assert re.fullmatch(r"DEC-\d{8}-0001", decision["decision_id"])
    lines = (data_dir / "decisions.jsonl").read_text().splitlines()
    assert [json.loads(line)["decision_id"] for line in lines] == [decision["decision_id"]]


async def test_record_decision_guardrail_survives_protocol(server_params, data_dir):
    # Demo 2 approved anyway: the server refuses, whatever the agent concluded.
    async with Client(server_params) as client:
        result = await client.call_tool(
            "record_decision",
            {"employee_id": "E1003", "item": "cyberdeck", "decision": "approve",
             "request_type": "additional", "reason": "Wants one"},
        )  # fmt: skip

    payload = _payload(result)
    assert (payload["error"], payload["status"]) == ("approval_not_permitted", "not_entitled")
    assert not (data_dir / "decisions.jsonl").exists()
