"""get_policy_limits: what a role may hold, and how often it refreshes (requirements §4.1)."""

from typing import Any

from militech_equipment.catalog import ROLES, normalize_name
from militech_equipment.repository import PolicyRepository


def get_policy_limits(role: Any, *, repo: PolicyRepository | None = None) -> dict[str, Any]:
    """Return {role, limits{item: {max_quantity, refresh_months, entitled}}} for every item.

    Unknown roles come back as {"error": "unknown_role", ...}, never raised.
    """
    repo = repo or PolicyRepository.from_dir()
    normalized = normalize_name(role)
    limits = repo.limits_for(normalized) if normalized else None
    if limits is None:
        return {"error": "unknown_role", "role": role, "known_roles": list(ROLES)}

    return {
        "role": normalized,
        "limits": {
            item: {**rule, "entitled": rule["max_quantity"] > 0} for item, rule in limits.items()
        },
    }
