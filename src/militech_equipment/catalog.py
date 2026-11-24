"""Closed vocabularies from the requirements: the item catalog (§3) and roles (§2)."""

CATALOG: tuple[str, ...] = (
    "terminal",
    "holo_display",
    "haptic_keyboard",
    "gesture_band",
    "neural_headset",
    "cyberdeck",
)

ROLES: tuple[str, ...] = (
    "corpo_staff",
    "cyberware_tech",
    "executive",
    "netrunner",
)


def normalize_name(raw: object) -> str | None:
    """Lowercase/trim a role or item name; None if it isn't a non-empty string."""
    if not isinstance(raw, str):
        return None
    return raw.strip().lower() or None
