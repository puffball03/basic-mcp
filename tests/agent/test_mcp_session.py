"""The agent's side of MCP: start the server, offer its tools to Claude, call them (plan M8)."""

import mcp.types as mcp_types

from militech_agent.mcp_session import McpSession, mcp_tool_to_claude

SERVER_TOOLS = {
    "check_request_eligibility",
    "flag_for_human_review",
    "get_employee_info",
    "get_policy_limits",
    "record_decision",
}


# --- schema conversion: MCP tool listing -> Claude tool definition ---


def test_converts_name_description_and_schema():
    tool = mcp_types.Tool(
        name="ping",
        description="Ping the server.",
        input_schema={"type": "object", "title": "pingArguments",
                      "properties": {"name": {"type": "string", "title": "Name"}},
                      "required": ["name"]},
    )  # fmt: skip

    assert mcp_tool_to_claude(tool) == {
        "name": "ping",
        "description": "Ping the server.",
        "input_schema": {"type": "object", "title": "pingArguments",
                         "properties": {"name": {"type": "string", "title": "Name"}},
                         "required": ["name"]},
    }  # fmt: skip


def test_missing_description_becomes_empty_text():
    tool = mcp_types.Tool(name="bare", input_schema={"type": "object", "properties": {}})

    assert mcp_tool_to_claude(tool)["description"] == ""


def test_schema_always_has_type_object():
    tool = mcp_types.Tool(name="loose", description="d", input_schema={"properties": {}})

    assert mcp_tool_to_claude(tool)["input_schema"]["type"] == "object"


# --- a real stdio session against the Militech server ---


async def test_session_lists_the_five_server_tools_as_claude_tools(data_dir):
    async with McpSession.stdio(data_dir=data_dir) as session:
        tools = session.tools

    assert {tool["name"] for tool in tools} == SERVER_TOOLS
    for tool in tools:
        assert set(tool) == {"name", "description", "input_schema"}
        assert tool["description"]
        assert tool["input_schema"]["type"] == "object"


async def test_call_tool_returns_the_decoded_observation(data_dir):
    async with McpSession.stdio(data_dir=data_dir) as session:
        observation = await session.call_tool("get_employee_info", {"employee_id": "E1004"})

    assert observation["name"] == "Kira Vasquez"
    assert observation["role"] == "netrunner"


async def test_tool_level_errors_come_back_as_observations(data_dir):
    async with McpSession.stdio(data_dir=data_dir) as session:
        observation = await session.call_tool("get_employee_info", {"employee_id": "E9999"})

    assert observation == {"error": "employee_not_found", "employee_id": "E9999"}


async def test_protocol_errors_become_error_observations_not_exceptions(data_dir):
    async with McpSession.stdio(data_dir=data_dir) as session:
        missing_arg = await session.call_tool("get_employee_info", {})
        unknown_tool = await session.call_tool("launch_missiles", {})
        still_works = await session.call_tool("get_employee_info", {"employee_id": "E1001"})

    assert missing_arg["error"] == "tool_error"
    assert missing_arg["message"]  # the server's explanation, for the model to read
    assert unknown_tool["error"] == "tool_error"
    assert still_works["name"] == "Reyes Okafor"


async def test_session_writes_go_to_the_given_data_dir(data_dir):
    async with McpSession.stdio(data_dir=data_dir) as session:
        await session.call_tool(
            "flag_for_human_review",
            {"employee_id": "E1004", "request": "Combat drone", "reason": "Off catalog",
             "reason_code": "OFF_CATALOG"},
        )  # fmt: skip

    assert (data_dir / "escalations.jsonl").exists()
