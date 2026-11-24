from datetime import date

import pytest

from militech_equipment.dates import add_months, months_between, today


def test_today_is_the_real_date():
    assert today() == date.today()


@pytest.mark.parametrize(
    ("start", "months", "expected"),
    [
        (date(2024, 11, 23), 24, date(2026, 11, 23)),  # plain
        (date(2025, 9, 15), 0, date(2025, 9, 15)),  # zero
        (date(2025, 11, 30), 3, date(2026, 2, 28)),  # day clamps to month end
        (date(2026, 1, 31), 1, date(2026, 2, 28)),  # Jan 31 -> Feb 28 (non-leap)
        (date(2028, 1, 31), 1, date(2028, 2, 29)),  # Jan 31 -> Feb 29 (leap)
        (date(2024, 2, 29), 12, date(2025, 2, 28)),  # leap day -> non-leap year
        (date(2025, 12, 15), 1, date(2026, 1, 15)),  # year rollover
    ],
)
def test_add_months(start, months, expected):
    assert add_months(start, months) == expected


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (date(2024, 11, 23), date(2026, 11, 23), 24),  # exact anniversary counts
        (date(2024, 11, 24), date(2026, 11, 23), 23),  # one day short
        (date(2025, 9, 15), date(2026, 11, 23), 14),  # demo 3 terminal age
        (date(2026, 1, 31), date(2026, 2, 28), 1),  # consistent with add_months clamping
        (date(2026, 11, 23), date(2026, 11, 23), 0),  # same day
        (date(2026, 11, 23), date(2026, 11, 1), 0),  # end before start never goes negative
    ],
)
def test_months_between(start, end, expected):
    assert months_between(start, end) == expected
