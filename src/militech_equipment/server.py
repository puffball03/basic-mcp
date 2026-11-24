"""Militech equipment request MCP server (stdio).

Each tool is a thin wrapper over a plain, unit-tested function. Run with:
    uv run python -m militech_equipment.server
    docker run -i militech-mcp server
"""

from typing import Any

from mcp.server.mcpserver import MCPServer

from militech_equipment import eligibility, employees, policies

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


@server.tool(
    description=(
        "Return what a role may hold: per catalog item, max_quantity, refresh_months, entitled.\n\n"
        # Roles come from policies.jsonl, loaded (and validated) when the server starts.
        f"role: one of {', '.join(policies.known_roles())} (case-insensitive).\n"
        "max_quantity 0 / entitled false means the role is never entitled to that item.\n"
        'Returns {"error": "unknown_role", "known_roles": [...]} for any other role.'
    )
)
def get_policy_limits(role: str) -> dict[str, Any]:
    return policies.get_policy_limits(role)


@server.tool()
def check_request_eligibility(employee_id: str, item: str) -> dict[str, Any]:
    """Check whether an employee may receive one more of a catalog item, as of today.

    employee_id: E#### ID. item: an exact catalog name (terminal, holo_display, haptic_keyboard,
    gesture_band, neural_headset, cyberdeck); map plain words like "laptop" first.
    status is one of within_policy, refresh_not_due, not_entitled, off_catalog,
    employee_not_found, data_conflict. Below the cap the request is read as an additional
    unit; at the cap, as a replacement of the oldest unit (see request_type_assumed and
    next_eligible_date). This tool only checks; it records nothing.
    """
    return eligibility.check_request_eligibility(employee_id, item)


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
