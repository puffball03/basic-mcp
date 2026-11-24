"""The real Militech MCP server, driven over stdio by a real MCP client."""

import json
import os
import sys

import pytest
from mcp import Client, StdioServerParameters


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


async def test_get_policy_limits_is_registered(server_params):
    async with Client(server_params) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    assert tools["get_policy_limits"].input_schema["required"] == ["role"]


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
