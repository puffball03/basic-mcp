"""Militech equipment request MCP server (stdio).

Each tool is a thin wrapper over a plain, unit-tested function. Run with:
    uv run python -m militech_equipment.server
    docker run -i militech-mcp server
"""

from typing import Any

from mcp.server.mcpserver import MCPServer

from militech_equipment import employees, policies

server = MCPServer(
    "militech-equipment",
    instructions=(
        "Militech Asset Control equipment request tools. Look up employees and policies, "
        "check eligibility, and escalate ambiguous requests for human review."
    ),
)


@server.tool()
def get_employee_info(employee_id: str) -> dict[str, Any]:
    """Return an employee's name, role, tenure in months, and equipment on file.

    employee_id: Militech employee ID in the form E#### (e.g. E1004).
    Returns {"error": "employee_not_found" | "invalid_employee_id", ...} on bad input.
    """
    return employees.get_employee_info(employee_id)


@server.tool()
def get_policy_limits(role: str) -> dict[str, Any]:
    """Return what a role may hold: per catalog item, max_quantity, refresh_months, entitled.

    role: one of corpo_staff, cyberware_tech, executive, netrunner (case-insensitive).
    max_quantity 0 / entitled false means the role is never entitled to that item.
    Returns {"error": "unknown_role", "known_roles": [...]} for any other role.
    """
    return policies.get_policy_limits(role)


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
