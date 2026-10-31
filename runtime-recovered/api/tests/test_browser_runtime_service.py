from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import create_test_app, make_temp_dir
from atnos.services.browser_runtime_service import BrowserRuntimeService
from atnos.services.browser_runtime_service import BrowserRuntimeError
from test_support import build_test_settings
import pytest


def test_browser_runtime_returns_already_live_bridge_without_restart() -> None:
    with make_temp_dir() as temp_dir:
        app = create_test_app(Path(temp_dir))
        service = BrowserRuntimeService(app.state.settings)
        fake_process = SimpleNamespace(cmdline=lambda: [])
        chrome_path = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")

        with patch.object(service, "_list_browser_processes", return_value=[fake_process]), patch.object(
            service,
            "_detect_remote_debugging_port",
            return_value=9222,
        ), patch.object(
            service,
            "_is_devtools_port_live",
            return_value=True,
        ), patch.object(
            service,
            "_resolve_running_browser_executable",
            return_value=chrome_path,
        ):
            result = service.ensure_chrome_devtools_bridge(port=9222, restart_existing=True)

    assert result.ok is True
    assert result.status == "already_live"
    assert result.restarted_existing is False
    assert result.browser_executable == chrome_path


def test_bridge_preserves_personal_brave_and_uses_separate_atnos_profile() -> None:
    with make_temp_dir() as temp_dir:
        settings = build_test_settings(Path(temp_dir))
        service = BrowserRuntimeService(settings)
        personal = SimpleNamespace(cmdline=lambda: ["brave.exe", "--profile-directory=Default"])
        with patch.object(service, "_list_browser_processes", side_effect=lambda name: [personal] if name == "brave.exe" else []), patch.object(service, "_detect_remote_debugging_port", return_value=None), patch.object(service, "resolve_chrome_executable", return_value=Path("E:/Tools/Brave/brave.exe")), patch.object(service, "_launch_detached") as launch, patch.object(service, "_wait_for_devtools_port"):
            result = service.ensure_chrome_devtools_bridge()
        assert not result.restarted_existing
        assert not result.using_default_profile
        assert f"--user-data-dir={settings.browser_profile_dir.resolve().as_posix()}" in result.command
        assert "--remote-debugging-address=127.0.0.1" in result.command
        assert "no access to its signed-in tabs" in " ".join(result.notes)
        launch.assert_called_once()


def test_bridge_does_not_relaunch_a_busy_atnos_profile() -> None:
    with make_temp_dir() as temp_dir:
        settings = build_test_settings(Path(temp_dir))
        service = BrowserRuntimeService(settings)
        busy = SimpleNamespace(cmdline=lambda: [f"--user-data-dir={settings.browser_profile_dir.resolve().as_posix()}"])
        with patch.object(service, "_list_browser_processes", return_value=[busy]), patch.object(service, "_detect_remote_debugging_port", return_value=None), patch.object(service, "resolve_chrome_executable", return_value=Path("E:/Tools/Brave/brave.exe")), patch.object(service, "_launch_detached") as launch:
            with pytest.raises(BrowserRuntimeError, match="profile is already in use"):
                service.ensure_chrome_devtools_bridge()
        launch.assert_not_called()
