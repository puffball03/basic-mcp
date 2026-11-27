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


# --- the whole surface, as a client sees it (plan M7) ---

# Requirements §7: every tool, its parameters in order, all plain strings.
TOOL_PARAMETERS = {
    "get_employee_info": ["employee_id"],
    "get_policy_limits": ["role"],
    "check_request_eligibility": ["employee_id", "item"],
    "flag_for_human_review": ["employee_id", "request", "reason", "reason_code"],
    "record_decision": ["employee_id", "item", "decision", "request_type", "reason"],
}


async def test_exactly_the_five_tools_with_their_schemas(server_params):
    async with Client(server_params) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    assert sorted(tools) == sorted(TOOL_PARAMETERS)
    for name, parameters in TOOL_PARAMETERS.items():
        schema = tools[name].input_schema
        assert schema["required"] == parameters, name
        assert list(schema["properties"]) == parameters, name
        assert all(prop["type"] == "string" for prop in schema["properties"].values()), name
        assert tools[name].description, name


async def test_check_request_eligibility_input_error_survives_protocol(server_params):
    async with Client(server_params) as client:
        result = await client.call_tool(
            "check_request_eligibility", {"employee_id": "E1001", "item": "   "}
        )

    assert _payload(result)["error"] == "invalid_item"


async def test_missing_argument_is_a_tool_error_not_a_crash(server_params):
    async with Client(server_params) as client:
        broken = await client.call_tool("get_employee_info", {})
        after = await client.call_tool("get_employee_info", {"employee_id": "E1001"})

    assert broken.is_error is True  # schema validation, reported to the caller
    assert _payload(after)["name"] == "Reyes Okafor"  # the session survives


# --- runtime records outlive the server process ---


async def test_decisions_persist_across_server_restarts(server_params):
    async with Client(server_params) as client:
        await client.call_tool(
            "record_decision",
            {"employee_id": "E1001", "item": "holo_display", "decision": "approve",
             "request_type": "additional", "reason": "Second display for firmware work"},
        )  # fmt: skip

    async with Client(server_params) as client:  # a fresh server process, same data dir
        result = await client.call_tool(
            "check_request_eligibility", {"employee_id": "E1001", "item": "holo_display"}
        )

    eligibility = _payload(result)
    assert eligibility["held"] == 2  # the approval from the first process counts
    assert eligibility["request_type_assumed"] == "replacement"


async def test_ticket_numbers_continue_across_server_restarts(server_params):
    args = {"employee_id": "E1004", "request": "A combat drone", "reason": "Not in catalog",
            "reason_code": "OFF_CATALOG"}  # fmt: skip
    async with Client(server_params) as client:
        first = _payload(await client.call_tool("flag_for_human_review", args))["ticket_id"]
    async with Client(server_params) as client:
        second = _payload(await client.call_tool("flag_for_human_review", args))["ticket_id"]

    if first[:12] == second[:12]:  # same UTC day (the clock isn't pinned over stdio)
        assert (first[-4:], second[-4:]) == ("0001", "0002")
    else:
        assert second.endswith("-0001")


# --- each decision rule lives on the tool where it is applied (plan M9) ---
# The agent's system prompt only says how to work; these descriptions carry the rules, so any
# MCP client learns them from the tool listing.


async def _descriptions(server_params) -> dict[str, str]:
    async with Client(server_params) as client:
        return {tool.name: tool.description for tool in (await client.list_tools()).tools}


async def test_check_request_eligibility_explains_item_names_and_readings(server_params):
    description = (await _descriptions(server_params))["check_request_eligibility"]

    assert '"laptop"' in description  # map plain words to catalog items first
    assert "additional" in description and "replacement" in description
    assert "request_type_assumed" in description


async def test_flag_for_human_review_explains_each_reason_code(server_params):
    description = (await _descriptions(server_params))["flag_for_human_review"]

    for trigger in ("damaged", "lost", "stolen", "medical", "on behalf of", "not on file",
                    "not one of the catalog", "data_conflict"):  # fmt: skip
        assert trigger in description, trigger


async def test_record_decision_explains_when_to_approve_or_deny(server_params):
    description = (await _descriptions(server_params))["record_decision"]

    for code in ("NOT_ENTITLED", "REFRESH_NOT_DUE", "QUANTITY_CAP_REACHED"):
        assert code in description, code
    assert "slow" in description and "not an exception" in description
    assert "flag_for_human_review" in description  # exception reasons go there instead
    assert "another unit" in description and "at the cap" in description
    assert "below the cap" in description


async def test_flag_for_human_review_says_to_check_the_employee_first(server_params):
    description = (await _descriptions(server_params))["flag_for_human_review"]

    assert "Check the employee first" in description
    assert "get_employee_info" in description and "check_request_eligibility" in description
