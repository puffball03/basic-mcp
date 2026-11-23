"""Shared fixtures: a pinned 'today' and a throwaway copy of the seed data."""

import shutil
from datetime import date
from pathlib import Path

import pytest

SEED_DIR = Path(__file__).resolve().parents[1] / "data"

# Every date-dependent test pins "today" here (requirements §4.2).
AS_OF = date(2026, 11, 23)


@pytest.fixture
def as_of() -> date:
    return AS_OF


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """A temp copy of the committed seed JSONL, safe for tests that write files."""
    target = tmp_path / "data"
    target.mkdir()
    for seed_file in ("employees.jsonl", "policies.jsonl"):
        shutil.copy(SEED_DIR / seed_file, target / seed_file)
    return target
