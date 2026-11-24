"""get_policy_limits: what a role may hold, and how often it refreshes (requirements §4.1)."""

from functools import cache
from pathlib import Path
from typing import Any

from militech_equipment.catalog import ROLES, normalize_name
from militech_equipment.repository import PolicyRepository, data_dir


@cache
def _load_repo(directory: Path) -> PolicyRepository:
    """Policies are static seed data: load and validate once per data directory."""
    return PolicyRepository.from_dir(directory)


def get_policy_limits(role: Any, *, repo: PolicyRepository | None = None) -> dict[str, Any]:
    """Return {role, limits{item: {max_quantity, refresh_months, entitled}}} for every item.

    Unknown roles come back as {"error": "unknown_role", ...}, never raised.
    """
    normalized = normalize_name(role)
    limits = None
    if normalized in ROLES:  # check input before touching the data file
        limits = (repo or _load_repo(data_dir())).limits_for(normalized)
    if limits is None:
        return {"error": "unknown_role", "role": role, "known_roles": list(ROLES)}

    return {
        "role": normalized,
        "limits": {
            item: {**rule, "entitled": rule["max_quantity"] > 0} for item, rule in limits.items()
        },
    }
