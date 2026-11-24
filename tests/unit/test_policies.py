import json

import pytest

from militech_equipment.catalog import CATALOG, ROLES
from militech_equipment.policies import get_policy_limits
from militech_equipment.repository import JsonlError, PolicyRepository

# Requirements §4.1, copied by hand so the seed data can't drift from the spec unnoticed.
# Each cell: (max_quantity, refresh_months); max 0 = never entitled.
SPEC = {
    "corpo_staff": {
        "terminal": (1, 36), "holo_display": (1, 36), "haptic_keyboard": (1, 24),
        "gesture_band": (1, 24), "neural_headset": (1, 24), "cyberdeck": (0, 0),
    },
    "cyberware_tech": {
        "terminal": (1, 24), "holo_display": (2, 36), "haptic_keyboard": (1, 12),
        "gesture_band": (1, 12), "neural_headset": (1, 24), "cyberdeck": (0, 0),
    },
    "executive": {
        "terminal": (1, 18), "holo_display": (2, 36), "haptic_keyboard": (1, 12),
        "gesture_band": (1, 12), "neural_headset": (1, 12), "cyberdeck": (0, 0),
    },
    "netrunner": {
        "terminal": (1, 24), "holo_display": (3, 24), "haptic_keyboard": (2, 12),
        "gesture_band": (1, 12), "neural_headset": (1, 12), "cyberdeck": (1, 18),
    },
}  # fmt: skip


@pytest.fixture
def repo(data_dir):
    return PolicyRepository.from_dir(data_dir)


def test_catalog_and_roles_match_requirements():
    assert CATALOG == (
        "terminal", "holo_display", "haptic_keyboard",
        "gesture_band", "neural_headset", "cyberdeck",
    )  # fmt: skip
    assert ROLES == tuple(SPEC)


@pytest.mark.parametrize("role", list(SPEC))
def test_every_role_matches_the_policy_table(repo, role):
    result = get_policy_limits(role, repo=repo)

    assert result["role"] == role
    assert list(result["limits"]) == list(CATALOG)  # all 6 items, catalog order
    for item, (max_quantity, refresh_months) in SPEC[role].items():
        assert result["limits"][item] == {
            "max_quantity": max_quantity,
            "refresh_months": refresh_months,
            "entitled": max_quantity > 0,
        }, f"{role}.{item}"


def test_only_netrunners_get_cyberdecks(repo):
    assert get_policy_limits("netrunner", repo=repo)["limits"]["cyberdeck"] == {
        "max_quantity": 1,
        "refresh_months": 18,
        "entitled": True,
    }
    corpo = get_policy_limits("corpo_staff", repo=repo)["limits"]["cyberdeck"]
    assert corpo["max_quantity"] == 0
    assert corpo["entitled"] is False


@pytest.mark.parametrize("raw", ["Netrunner", " NETRUNNER ", "netrunner\n"])
def test_role_lookup_is_case_and_whitespace_insensitive(repo, raw):
    assert get_policy_limits(raw, repo=repo)["role"] == "netrunner"


@pytest.mark.parametrize("raw", ["fixer", "manager", "", "net runner", None, 7])
def test_unknown_role(repo, raw):
    assert get_policy_limits(raw, repo=repo) == {
        "error": "unknown_role",
        "role": raw,
        "known_roles": list(ROLES),
    }


def test_defaults_to_seed_data(monkeypatch, data_dir):
    monkeypatch.setenv("MILITECH_DATA_DIR", str(data_dir))

    assert get_policy_limits("executive")["limits"]["terminal"]["refresh_months"] == 18


# --- repository guards: a bad policy file fails loudly instead of silently mis-deciding ---


def test_policy_repository_rejects_duplicate_rules(tmp_path):
    row = '{"role": "netrunner", "item": "terminal", "max_quantity": 1, "refresh_months": 24}\n'
    (tmp_path / "policies.jsonl").write_text(row * 2)

    with pytest.raises(JsonlError, match="duplicate rule netrunner/terminal"):
        PolicyRepository.from_dir(tmp_path)


def test_policy_repository_rejects_items_outside_the_catalog(tmp_path):
    (tmp_path / "policies.jsonl").write_text(
        '{"role": "netrunner", "item": "combat_drone", "max_quantity": 1, "refresh_months": 6}\n'
    )

    with pytest.raises(JsonlError, match="unknown item combat_drone"):
        PolicyRepository.from_dir(tmp_path)


def test_policy_repository_rejects_negative_numbers(tmp_path):
    (tmp_path / "policies.jsonl").write_text(
        '{"role": "netrunner", "item": "terminal", "max_quantity": -1, "refresh_months": 24}\n'
    )

    with pytest.raises(JsonlError, match="netrunner/terminal"):
        PolicyRepository.from_dir(tmp_path)


def _write_policies(directory, rows):
    (directory / "policies.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))


def _seed_rows(data_dir):
    lines = (data_dir / "policies.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_policy_repository_rejects_missing_rules(tmp_path, data_dir):
    rows = [r for r in _seed_rows(data_dir) if (r["role"], r["item"]) != ("netrunner", "cyberdeck")]
    _write_policies(tmp_path, rows)

    with pytest.raises(JsonlError, match="missing rules for netrunner/cyberdeck"):
        PolicyRepository.from_dir(tmp_path)


@pytest.mark.parametrize("bad_role", ["Netrunner", "net_runner", "fixer"])
def test_policy_repository_rejects_unknown_roles(tmp_path, bad_role):
    row = {"role": bad_role, "item": "terminal", "max_quantity": 1, "refresh_months": 24}
    _write_policies(tmp_path, [row])

    with pytest.raises(JsonlError, match=f"unknown role {bad_role}"):
        PolicyRepository.from_dir(tmp_path)


def test_policy_repository_rejects_missing_file(tmp_path):
    with pytest.raises(JsonlError, match="policies.jsonl not found"):
        PolicyRepository.from_dir(tmp_path)


@pytest.mark.parametrize("limits", [(True, 24), (1, False), (1.0, 24)])
def test_policy_repository_rejects_non_integer_limits(tmp_path, limits):
    max_quantity, refresh_months = limits
    row = {"role": "netrunner", "item": "terminal",
           "max_quantity": max_quantity, "refresh_months": refresh_months}  # fmt: skip
    _write_policies(tmp_path, [row])

    with pytest.raises(JsonlError, match="non-negative integers"):
        PolicyRepository.from_dir(tmp_path)


def test_policy_repository_rejects_entitled_item_without_refresh(tmp_path):
    row = {"role": "netrunner", "item": "terminal", "max_quantity": 2, "refresh_months": 0}
    _write_policies(tmp_path, [row])

    with pytest.raises(JsonlError, match="refresh_months > 0"):
        PolicyRepository.from_dir(tmp_path)


def test_policy_repository_rejects_missing_fields(tmp_path):
    _write_policies(tmp_path, [{"role": "netrunner", "item": "terminal", "max_quantity": 1}])

    with pytest.raises(JsonlError, match="missing refresh_months"):
        PolicyRepository.from_dir(tmp_path)


def test_invalid_input_does_not_load_policies(monkeypatch, tmp_path):
    monkeypatch.setenv("MILITECH_DATA_DIR", str(tmp_path))  # no policies.jsonl here

    assert get_policy_limits(None)["error"] == "unknown_role"
