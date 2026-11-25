"""The closed item catalog (requirements §3). Roles live in policies.jsonl, not here."""

CATALOG: tuple[str, ...] = (
    "terminal",
    "holo_display",
    "haptic_keyboard",
    "gesture_band",
    "neural_headset",
    "cyberdeck",
)


def normalize_name(raw: object) -> str | None:
    """Lowercase/trim a role or item name; None if it isn't a non-empty string."""
    if not isinstance(raw, str):
        return None
    return raw.strip().lower() or None


def clean_text(raw: object) -> str | None:
    """Trimmed free text, or None if it isn't a non-blank string."""
    if not isinstance(raw, str):
        return None
    return raw.strip() or None
