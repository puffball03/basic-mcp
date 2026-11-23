"""Smoke test: both packages import and expose a version."""

import militech_agent
import militech_equipment


def test_server_package_has_version():
    assert militech_equipment.__version__ == "0.1.0"


def test_agent_package_has_version():
    assert militech_agent.__version__ == "0.1.0"
