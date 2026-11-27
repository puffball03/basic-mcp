"""flag_for_human_review: hand an ambiguous request to a human (requirements §6, §7).

Each call appends one open ticket to escalations.jsonl. Ticket IDs are ESC-YYYYMMDD-NNNN,
numbered per UTC day. The employee does not have to exist: EMPLOYEE_NOT_FOUND must be
escalatable.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from militech_equipment import dates
from militech_equipment.catalog import clean_text
from militech_equipment.employees import invalid_employee_id_error, normalize_employee_id
from militech_equipment.repository import append_jsonl, data_dir, next_daily_id

REASON_CODES: tuple[str, ...] = (
    "OFF_CATALOG",
    "EXCEPTION_CLAIMED",
    "UNCLEAR_REQUEST",
    "EMPLOYEE_NOT_FOUND",
    "THIRD_PARTY_REQUEST",
    "DATA_CONFLICT",
)

ESCALATIONS_FILE = "escalations.jsonl"

# When each code applies, in the order to check them; the escalation tool's description is
# built from this, so the rules reach every MCP client with the tool listing.
REASON_CODE_MEANINGS: dict[str, str] = {
    "EMPLOYEE_NOT_FOUND": "the employee_id is not on file.",
    "THIRD_PARTY_REQUEST": "the request is on behalf of someone else.",
    "OFF_CATALOG": "the item is not one of the catalog items.",
    "UNCLEAR_REQUEST": "you can't tell which item is wanted, or several are bundled together.",
    "DATA_CONFLICT": "eligibility reports data_conflict (the records are inconsistent).",
    "EXCEPTION_CLAIMED": "policy says no, but the unit is damaged, lost or stolen, or there is "
    "a medical or accessibility need.",
}


def flag_for_human_review(
    employee_id: Any,
    request: Any,
    reason: Any,
    reason_code: Any,
    *,
    directory: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Open an escalation ticket. Bad input comes back as {"error": ...} and writes nothing."""
    normalized_id = normalize_employee_id(employee_id)
    if normalized_id is None:
        return invalid_employee_id_error(employee_id)

    text = {"request": clean_text(request), "reason": clean_text(reason)}
    for field, value in text.items():
        if value is None:
            return {
                "error": "missing_field",
                "field": field,
                "message": f"{field} must be a non-empty string",
            }

    code = reason_code.strip().upper() if isinstance(reason_code, str) else None
    if code not in REASON_CODES:
        return {
            "error": "invalid_reason_code",
            "reason_code": reason_code,
            "valid_codes": list(REASON_CODES),
        }

    created = (now or dates.now()).astimezone(UTC)
    path = Path(directory or data_dir()) / ESCALATIONS_FILE
    ticket = {
        "ticket_id": next_daily_id(path, "ESC", "ticket_id", created),
        "employee_id": normalized_id,
        "request": text["request"],
        "reason": text["reason"],
        "reason_code": code,
        "created_at": created.isoformat(timespec="seconds"),
        "status": "open",
    }
    append_jsonl(path, ticket)
    return {key: ticket[key] for key in ("ticket_id", "status", "created_at")}
