"""Militech equipment request MCP server (stdio).

Each tool is a thin wrapper over a plain, unit-tested function. Run with:
    uv run python -m militech_equipment.server
    docker run -i militech-mcp server
"""

from typing import Any

from mcp.server.mcpserver import MCPServer

from militech_equipment import decisions, eligibility, employees, escalations, policies
from militech_equipment.catalog import CATALOG

server = MCPServer(
    "militech-equipment",
    instructions=(
        "Militech Asset Control equipment request tools. Look up employees and policies, "
        "check eligibility, and escalate ambiguous requests for human review."
    ),
)


@server.tool(
    description=(
        "Return an employee's name, role, tenure in months, and equipment on file "
        "(asset_id, item, issued_date per unit).\n\n"
        "employee_id: Militech employee ID in the form E#### (e.g. E1004).\n"
        'Returns {"error": "employee_not_found" | "invalid_employee_id", ...} on bad input.'
    )
)
def get_employee_info(employee_id: str) -> dict[str, Any]:
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


@server.tool(
    description=(
        "Check whether an employee may receive one more of a catalog item, as of today. "
        "Read-only: records nothing.\n\n"
        "employee_id: Militech employee ID in the form E#### (e.g. E1004).\n"
        f"item: one of {', '.join(CATALOG)} (case-insensitive). "
        'Map plain words first, e.g. "laptop" -> terminal.\n'
        "status: within_policy, refresh_not_due, not_entitled, off_catalog, "
        "employee_not_found, or data_conflict.\n"
        "Below the cap the request is read as an additional unit; at the cap, as a replacement "
        "of the oldest unit (see request_type_assumed and next_eligible_date).\n"
        'Returns {"error": "invalid_employee_id" | "invalid_item", ...} on malformed input.'
    )
)
def check_request_eligibility(employee_id: str, item: str) -> dict[str, Any]:
    return eligibility.check_request_eligibility(employee_id, item)


@server.tool(
    description=(
        "Escalate a request to a human reviewer instead of deciding it. Opens a ticket and "
        "appends it to escalations.jsonl.\n\n"
        "employee_id: Militech employee ID in the form E#### (e.g. E1004); it need not be "
        "on file.\n"
        "request: the employee's request, as they stated it.\n"
        "reason: why this needs a human, in a sentence.\n"
        f"reason_code: one of {', '.join(escalations.REASON_CODES)}.\n"
        'Returns {"ticket_id": "ESC-YYYYMMDD-NNNN", "status": "open", "created_at"}, or '
        '{"error": "invalid_employee_id" | "missing_field" | "invalid_reason_code", ...} '
        "with nothing written."
    )
)
def flag_for_human_review(
    employee_id: str, request: str, reason: str, reason_code: str
) -> dict[str, Any]:
    return escalations.flag_for_human_review(employee_id, request, reason, reason_code)


@server.tool(
    description=(
        "Record a final approve or deny decision and append it to decisions.jsonl. Use it only "
        "for clear-cut requests; escalate anything ambiguous with flag_for_human_review.\n\n"
        "employee_id: Militech employee ID in the form E#### (e.g. E1004); must be on file.\n"
        f"item: one of {', '.join(CATALOG)}.\n"
        f"decision: {' or '.join(decisions.DECISIONS)}.\n"
        f"request_type: {' or '.join(decisions.REQUEST_TYPES)} (what the employee asked for).\n"
        "reason: the justification, including the deny code (NOT_ENTITLED, REFRESH_NOT_DUE, "
        "QUANTITY_CAP_REACHED) when denying.\n"
        "The server refuses an approve the policy doesn't allow right now: "
        '{"error": "approval_not_permitted", "status": ...}, with nothing written. '
        'Otherwise returns {"decision_id": "DEC-YYYYMMDD-NNNN", "created_at"}.'
    )
)
def record_decision(
    employee_id: str, item: str, decision: str, request_type: str, reason: str
) -> dict[str, Any]:
    return decisions.record_decision(employee_id, item, decision, request_type, reason)


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
