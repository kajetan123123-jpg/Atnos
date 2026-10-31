from __future__ import annotations

import pytest

from atnos.core.config import Settings
from tools.ubuntu_control import (
    UbuntuControlAuthenticationError,
    UbuntuControlConfigError,
    UbuntuControlConnectionError,
    UbuntuControlTimeoutError,
    check_ubuntu_connection,
)


def test_ubuntu_skill_checks_connection_or_skips_without_env() -> None:
    settings = Settings()
    if not settings.atnos_ubuntu_host:
        pytest.skip("ATNOS_UBUNTU_HOST is not configured; skipping live Ubuntu smoke.")

    try:
        payload = check_ubuntu_connection(settings=settings)
    except UbuntuControlConfigError as exc:
        pytest.skip(f"Ubuntu SSH config incomplete: {exc}")
    except (UbuntuControlConnectionError, UbuntuControlTimeoutError, UbuntuControlAuthenticationError) as exc:
        pytest.skip(f"Ubuntu smoke target not reachable in this environment: {exc}")

    assert payload["ok"] is True
    assert payload["host"] == settings.atnos_ubuntu_host
    assert payload["checks"]["whoami"]["exit_code"] == 0
    assert payload["checks"]["hostname"]["exit_code"] == 0
