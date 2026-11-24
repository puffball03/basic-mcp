"""The real Militech MCP server, driven over stdio by a real MCP client."""

import json
import os
import sys

import pytest
from mcp import Client, StdioServerParameters

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

    assert "get_employee_info" in tools
    assert tools["get_employee_info"].input_schema["required"] == ["employee_id"]


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

    assert tools["check_request_eligibility"].input_schema["required"] == ["employee_id", "item"]


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
