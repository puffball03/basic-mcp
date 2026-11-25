"""Fixtures for driving the real server in-process with a pinned clock."""

from datetime import UTC, date, datetime, time

import pytest
from mcp import Client

from militech_equipment import dates
from militech_equipment.server import server


@pytest.fixture
def pin_today(monkeypatch):
    """Pin dates.today() and dates.now() to a given day (noon UTC); returns the setter."""

    def _pin(day: date) -> None:
        monkeypatch.setattr(dates, "today", lambda: day)
        monkeypatch.setattr(dates, "now", lambda: datetime.combine(day, time(12), UTC))

    return _pin


@pytest.fixture(params=["legacy", "auto"])
def connect(request, monkeypatch, data_dir, as_of, pin_today):
    """A factory for a connected client: use it as `async with connect() as client:`.

    Runs each test in both of the SDK's in-process modes. Not an async yield fixture:
    pytest-asyncio can tear those down in a different task than set them up, which breaks
    the SDK's anyio task groups.
    """
    monkeypatch.setenv("MILITECH_DATA_DIR", str(data_dir))
    pin_today(as_of)
    return lambda: Client(server, mode=request.param, raise_exceptions=True)
