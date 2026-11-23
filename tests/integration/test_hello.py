"""M1: prove the MCP plumbing — a real client talks to the hello server over stdio."""

import sys
from pathlib import Path

from mcp import Client, StdioServerParameters

HELLO_SERVER = Path(__file__).resolve().parents[2] / "scripts" / "hello_server.py"


def _server_params() -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=[str(HELLO_SERVER)])


async def test_hello_server_registers_ping_tool():
    async with Client(_server_params()) as client:
        result = await client.list_tools()

    assert [tool.name for tool in result.tools] == ["ping"]
    assert "name" in result.tools[0].input_schema["properties"]


async def test_ping_returns_pong():
    async with Client(_server_params()) as client:
        result = await client.call_tool("ping", {"name": "Militech"})

    assert result.is_error is False
    assert result.content[0].text == "pong, Militech"
