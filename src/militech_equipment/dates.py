"""Calendar-month arithmetic for refresh intervals and tenure (requirements §4.2).

A unit issued on 2024-11-23 is 24 months old on 2026-11-23. When the target month is
shorter, the day clamps to that month's last day (Jan 31 + 1 month -> Feb 28/29).
"""

import calendar
from datetime import date


def add_months(start: date, months: int) -> date:
    """Return `start` shifted forward by `months` calendar months, clamping the day."""
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def months_between(start: date, end: date) -> int:
    """Whole months elapsed: the largest n with add_months(start, n) <= end (never negative)."""
    if end <= start:
        return 0
    months = (end.year - start.year) * 12 + (end.month - start.month)
    while months > 0 and add_months(start, months) > end:
        months -= 1
    return months
