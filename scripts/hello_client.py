"""Minimal MCP client (M1): launch hello_server over stdio, list its tools, call ping.

Run:  uv run python scripts/hello_client.py
      docker compose run --rm test python scripts/hello_client.py
"""

import asyncio
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters

HELLO_SERVER = Path(__file__).resolve().parent / "hello_server.py"


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=[str(HELLO_SERVER)])
    async with Client(params) as client:
        print(f"Connected to server: {HELLO_SERVER.name}")

        tools = await client.list_tools()
        print(f"Registered tools: {[tool.name for tool in tools.tools]}")

        result = await client.call_tool("ping", {"name": "Militech"})
        print(f"call_tool('ping', name='Militech') -> {result.content[0].text}")


if __name__ == "__main__":
    asyncio.run(main())
