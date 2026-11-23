"""Minimal one-tool MCP server (M1): proves registration and stdio transport work.

Run directly:  uv run python scripts/hello_server.py   (speaks MCP on stdin/stdout)
"""

from mcp.server.mcpserver import MCPServer

server = MCPServer("militech-hello")


@server.tool()
def ping(name: str) -> str:
    """Reply with a pong addressed to `name`."""
    return f"pong, {name}"


if __name__ == "__main__":
    server.run()  # stdio transport by default
