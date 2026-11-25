"""flag_for_human_review: hand an ambiguous request to a human (requirements §6, §7).

Each call appends one open ticket to escalations.jsonl. Ticket IDs are ESC-YYYYMMDD-NNNN,
numbered per UTC day. The employee does not have to exist: EMPLOYEE_NOT_FOUND must be
escalatable.
"""

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from militech_equipment import dates
from militech_equipment.employees import invalid_employee_id_error, normalize_employee_id
from militech_equipment.repository import append_jsonl, data_dir, load_jsonl

REASON_CODES: tuple[str, ...] = (
    "OFF_CATALOG",
    "EXCEPTION_CLAIMED",
    "UNCLEAR_REQUEST",
    "EMPLOYEE_NOT_FOUND",
    "THIRD_PARTY_REQUEST",
    "DATA_CONFLICT",
)

ESCALATIONS_FILE = "escalations.jsonl"


def _clean_text(value: Any) -> str | None:
    """Trimmed text, or None if `value` isn't a non-blank string."""
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _next_ticket_id(path: Path, created: datetime) -> str:
    """ESC-<UTC date>-<next sequence number for that date in the file>."""
    prefix = f"ESC-{created:%Y%m%d}-"
    pattern = re.compile(re.escape(prefix) + r"(\d{4})")
    taken = [
        int(match.group(1))
        for record in load_jsonl(path)
        if (match := pattern.fullmatch(str(record.get("ticket_id", ""))))
    ]
    return f"{prefix}{max(taken, default=0) + 1:04d}"


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

    text = {"request": _clean_text(request), "reason": _clean_text(reason)}
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
        "ticket_id": _next_ticket_id(path, created),
        "employee_id": normalized_id,
        "request": text["request"],
        "reason": text["reason"],
        "reason_code": code,
        "created_at": created.isoformat(timespec="seconds"),
        "status": "open",
    }
    append_jsonl(path, ticket)
    return {key: ticket[key] for key in ("ticket_id", "status", "created_at")}
