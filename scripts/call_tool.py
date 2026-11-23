"""Call any Militech MCP tool from the command line through a real stdio MCP client.

Usage:
    uv run python scripts/call_tool.py                       # list tools
    uv run python scripts/call_tool.py get_employee_info employee_id=E1004
    docker compose run --rm test python scripts/call_tool.py get_employee_info employee_id=E1004
"""

import asyncio
import json
import sys

from mcp import Client, StdioServerParameters


def parse_args(pairs: list[str]) -> dict[str, str]:
    args = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise SystemExit(f"Expected key=value, got {pair!r}")
        args[key] = value
    return args


async def main(argv: list[str]) -> None:
    params = StdioServerParameters(command=sys.executable, args=["-m", "militech_equipment.server"])
    async with Client(params) as client:
        tools = (await client.list_tools()).tools
        print(f"Connected to militech-equipment. Tools: {[tool.name for tool in tools]}")
        if not argv:
            return

        name, args = argv[0], parse_args(argv[1:])
        print(f"\n-> call_tool({name!r}, {args})")
        result = await client.call_tool(name, args)
        text = result.content[0].text if result.content else ""
        try:
            print(json.dumps(json.loads(text), indent=2))
        except json.JSONDecodeError:
            print(text)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
