"""The agent's side of MCP (plan M8): start the server, offer its tools to Claude, call them.

The agent reaches the Militech tools only through this session, exactly as an external MCP
client would. Every call returns a plain dict (the observation the model reads); failures
come back as {"error": ...} observations rather than exceptions, so one bad call can't end
an agent run.
"""

import json
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import mcp.types as mcp_types
from mcp import Client, StdioServerParameters
from mcp.shared.exceptions import MCPError


def mcp_tool_to_claude(tool: mcp_types.Tool) -> dict[str, Any]:
    """An MCP tool listing as a Claude tool definition (name, description, input_schema)."""
    schema = dict(tool.input_schema or {})
    schema.setdefault("type", "object")
    return {"name": tool.name, "description": tool.description or "", "input_schema": schema}


def _text_of(result: mcp_types.CallToolResult) -> str:
    return "\n".join(block.text for block in result.content if block.type == "text")


class McpSession:
    """A connected MCP client with the server's tools converted for Claude."""

    def __init__(self, client: Client, tools: list[dict[str, Any]]) -> None:
        self._client = client
        self.tools = tools

    @classmethod
    @asynccontextmanager
    async def stdio(cls, *, data_dir: Path | None = None) -> AsyncIterator["McpSession"]:
        """Start `militech_equipment.server` over stdio, optionally on another data dir."""
        env = dict(os.environ)
        if data_dir is not None:
            env["MILITECH_DATA_DIR"] = str(data_dir)
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "militech_equipment.server"], env=env
        )
        async with Client(params) as client:
            listing = await client.list_tools()
            yield cls(client, [mcp_tool_to_claude(tool) for tool in listing.tools])

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a tool and decode its JSON result; any failure becomes an error observation."""
        try:
            result = await self._client.call_tool(name, arguments)
        except MCPError as exc:
            return {"error": "tool_error", "tool": name, "message": str(exc)}
        text = _text_of(result)
        if result.is_error:
            return {"error": "tool_error", "tool": name, "message": text or "tool call failed"}
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            return {"result": text}
        return decoded if isinstance(decoded, dict) else {"result": decoded}
