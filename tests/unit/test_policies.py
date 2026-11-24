import json

import pytest

from militech_equipment.catalog import CATALOG
from militech_equipment.policies import get_policy_limits, known_roles
from militech_equipment.repository import EmployeeRepository, JsonlError, PolicyRepository

# policies.jsonl is the source of truth for roles and limits. These tests check the
# invariants any valid policy file must satisfy, plus the few values the demos rely on,
# rather than a hand-copied copy of the whole table.


@pytest.fixture
def repo(data_dir):
    return PolicyRepository.from_dir(data_dir)


def _write_policies(directory, rows):
    (directory / "policies.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))


def _seed_rows(data_dir):
    lines = (data_dir / "policies.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _full_role(role, max_quantity=1, refresh_months=12):
    return [
        {"role": role, "item": item, "max_quantity": max_quantity,
         "refresh_months": refresh_months}
        for item in CATALOG
    ]  # fmt: skip


def test_catalog_matches_requirements():
    assert CATALOG == (
        "terminal", "holo_display", "haptic_keyboard",
        "gesture_band", "neural_headset", "cyberdeck",
    )  # fmt: skip


# --- invariants over the seed data ---


def test_every_role_lists_every_catalog_item_in_order(repo):
    assert repo.roles()
    for role in repo.roles():
        assert list(get_policy_limits(role, repo=repo)["limits"]) == list(CATALOG), role


def test_entitled_flag_and_refresh_are_consistent(repo):
    for role in repo.roles():
        for item, rule in get_policy_limits(role, repo=repo)["limits"].items():
            assert rule["entitled"] is (rule["max_quantity"] > 0), f"{role}.{item}"
            if rule["entitled"]:
                assert rule["refresh_months"] > 0, f"{role}.{item}"


def test_seed_employee_roles_have_policies(data_dir, repo):
    """Every seeded role is covered, except E1008's deliberately unknown 'fixer'."""
    employee_roles = {e["role"] for e in EmployeeRepository.from_dir(data_dir).employees.values()}

    assert employee_roles - set(repo.roles()) == {"fixer"}


# --- values the demos depend on (requirements §9) ---


def test_only_netrunners_get_cyberdecks(repo):
    for role in repo.roles():
        cyberdeck = get_policy_limits(role, repo=repo)["limits"]["cyberdeck"]
        assert cyberdeck["entitled"] is (role == "netrunner"), role
    assert get_policy_limits("netrunner", repo=repo)["limits"]["cyberdeck"] == {
        "max_quantity": 1,
        "refresh_months": 18,
        "entitled": True,
    }


def test_cyberware_tech_may_hold_two_holo_displays(repo):  # demo 1
    assert (
        get_policy_limits("cyberware_tech", repo=repo)["limits"]["holo_display"]["max_quantity"]
        == 2
    )


def test_executive_terminal_refreshes_every_18_months(repo):  # demo 6
    assert get_policy_limits("executive", repo=repo)["limits"]["terminal"]["refresh_months"] == 18


# --- lookup behaviour ---


@pytest.mark.parametrize("raw", ["Netrunner", " NETRUNNER ", "netrunner\n"])
def test_role_lookup_is_case_and_whitespace_insensitive(repo, raw):
    assert get_policy_limits(raw, repo=repo)["role"] == "netrunner"


@pytest.mark.parametrize("raw", ["fixer", "manager", "net runner"])
def test_unknown_role_lists_the_known_roles(repo, raw):
    assert get_policy_limits(raw, repo=repo) == {
        "error": "unknown_role",
        "role": raw,
        "known_roles": repo.roles(),
    }


@pytest.mark.parametrize("raw", ["", "   ", None, 7])
def test_non_string_or_blank_role_is_rejected(repo, raw):
    result = get_policy_limits(raw, repo=repo)

    assert result["error"] == "unknown_role"
    assert result["role"] == raw


def test_invalid_input_does_not_load_policies(monkeypatch, tmp_path):
    monkeypatch.setenv("MILITECH_DATA_DIR", str(tmp_path))  # no policies.jsonl here

    assert get_policy_limits(None)["error"] == "unknown_role"


def test_roles_are_inferred_from_the_data(tmp_path):
    _write_policies(tmp_path, _full_role("fixer"))
    repo = PolicyRepository.from_dir(tmp_path)

    assert repo.roles() == ["fixer"]
    assert get_policy_limits("fixer", repo=repo)["limits"]["terminal"]["max_quantity"] == 1
    assert get_policy_limits("netrunner", repo=repo)["known_roles"] == ["fixer"]


def test_defaults_to_seed_data(monkeypatch, data_dir):
    monkeypatch.setenv("MILITECH_DATA_DIR", str(data_dir))

    assert get_policy_limits("executive")["limits"]["terminal"]["refresh_months"] == 18
    assert "netrunner" in known_roles()


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


def test_policy_repository_rejects_missing_rules(tmp_path, data_dir):
    rows = [r for r in _seed_rows(data_dir) if (r["role"], r["item"]) != ("netrunner", "cyberdeck")]
    _write_policies(tmp_path, rows)

    with pytest.raises(JsonlError, match="missing rules for netrunner/cyberdeck"):
        PolicyRepository.from_dir(tmp_path)


def test_policy_repository_rejects_a_new_role_with_partial_rules(tmp_path, data_dir):
    rows = _seed_rows(data_dir) + _full_role("fixer")[:2]
    _write_policies(tmp_path, rows)

    with pytest.raises(JsonlError, match="missing rules for fixer/haptic_keyboard"):
        PolicyRepository.from_dir(tmp_path)


@pytest.mark.parametrize("bad_role", ["Netrunner", "net runner", " netrunner", "", 7, None])
def test_policy_repository_rejects_malformed_role_names(tmp_path, bad_role):
    row = {"role": bad_role, "item": "terminal", "max_quantity": 1, "refresh_months": 24}
    _write_policies(tmp_path, [row])

    with pytest.raises(JsonlError, match="invalid role name"):
        PolicyRepository.from_dir(tmp_path)


def test_policy_repository_rejects_missing_file(tmp_path):
    with pytest.raises(JsonlError, match="policies.jsonl not found"):
        PolicyRepository.from_dir(tmp_path)


def test_policy_repository_rejects_empty_file(tmp_path):
    (tmp_path / "policies.jsonl").write_text("\n")

    with pytest.raises(JsonlError, match="no policy rules"):
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
