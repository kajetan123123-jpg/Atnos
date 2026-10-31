from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import os
import sys
import unittest
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.core.config import Settings
from tools.ubuntu_control import (
    UbuntuControl,
    UbuntuControlApprovalRequired,
    UbuntuControlBlockedCommand,
    UbuntuControlConfigError,
    bootstrap_workspace,
    create_system_report,
    install_package,
    run_project_command,
    stop_remote_atnos_processes,
)

_UBUNTU_ENV_KEYS = (
    "ATNOS_UBUNTU_HOST",
    "ATNOS_UBUNTU_USER",
    "ATNOS_UBUNTU_KEY",
    "ATNOS_UBUNTU_WORKDIR",
    "ATNOS_UBUNTU_ALLOW_SUDO",
    "ATNOS_UBUNTU_REQUIRE_APPROVAL_FOR_SUDO",
)


class UbuntuControlTest(unittest.TestCase):
    def test_missing_key_raises_clear_error(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env_path = root / ".env"
            env_path.write_text(
                "\n".join(
                    (
                        "ATNOS_UBUNTU_HOST=198.51.100.216",
                        "ATNOS_UBUNTU_USER=atnos",
                        f"ATNOS_UBUNTU_KEY={root / 'missing_key'}",
                        "ATNOS_UBUNTU_WORKDIR=/home/atnos/atnos",
                    )
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {key: "" for key in _UBUNTU_ENV_KEYS}, clear=False):
                for key in _UBUNTU_ENV_KEYS:
                    os.environ.pop(key, None)
                settings = Settings(app_root=root, data_dir=root / "data", _env_file=env_path)

                with self.assertRaises(UbuntuControlConfigError):
                    UbuntuControl(settings=settings)

    def test_sudo_policy_can_require_approval(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            key_path = root / "atnos_lab"
            key_path.write_text("fake-key", encoding="utf-8")
            env_path = root / ".env"
            env_path.write_text(
                "\n".join(
                    (
                        "ATNOS_UBUNTU_HOST=198.51.100.216",
                        "ATNOS_UBUNTU_USER=atnos",
                        f"ATNOS_UBUNTU_KEY={key_path}",
                        "ATNOS_UBUNTU_WORKDIR=/home/atnos/atnos",
                        "ATNOS_UBUNTU_ALLOW_SUDO=true",
                        "ATNOS_UBUNTU_REQUIRE_APPROVAL_FOR_SUDO=true",
                    )
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {key: "" for key in _UBUNTU_ENV_KEYS}, clear=False):
                for key in _UBUNTU_ENV_KEYS:
                    os.environ.pop(key, None)
                settings = Settings(app_root=root, data_dir=root / "data", _env_file=env_path)
                control = UbuntuControl(settings=settings)

                with self.assertRaises(UbuntuControlApprovalRequired):
                    control._enforce_sudo_policy("sudo whoami")

    def test_create_system_report_returns_report_path(self) -> None:
        with patch(
            "tools.ubuntu_control.UbuntuControl.run",
            return_value=type(
                "Result",
                (),
                {
                    "to_dict": lambda self: {
                        "command": "echo ok",
                        "exit_code": 0,
                        "stdout": "",
                        "stderr": "",
                    }
                },
            )(),
        ):
            payload = create_system_report(settings=self._settings_with_fake_key())

        self.assertEqual(payload["report_path"], "/home/atnos/atnos/logs/system_report.md")

    def test_bootstrap_workspace_reports_expected_directories(self) -> None:
        with patch(
            "tools.ubuntu_control.UbuntuControl.run",
            return_value=type(
                "Result",
                (),
                {
                    "to_dict": lambda self: {
                        "command": "mkdir -p",
                        "exit_code": 0,
                        "stdout": "",
                        "stderr": "",
                    }
                },
            )(),
        ):
            payload = bootstrap_workspace(settings=self._settings_with_fake_key())

        self.assertIn("/home/atnos/atnos/renders", payload["directories"])
        self.assertEqual(len(payload["directories"]), 9)

    def test_stop_remote_processes_uses_sudo_path(self) -> None:
        with patch(
            "tools.ubuntu_control.UbuntuControl.run",
            return_value=type(
                "Result",
                (),
                {
                    "to_dict": lambda self: {
                        "command": "sudo pkill -u atnos || true",
                        "exit_code": 0,
                        "stdout": "",
                        "stderr": "",
                    }
                },
            )(),
        ) as run_mock:
            payload = stop_remote_atnos_processes(settings=self._settings_with_fake_key())

        run_mock.assert_called_once()
        self.assertEqual(payload["stopped_user"], "atnos")

    def test_install_package_builds_apt_install_command(self) -> None:
        with patch(
            "tools.ubuntu_control.UbuntuControl.run",
            return_value=type(
                "Result",
                (),
                {
                    "to_dict": lambda self: {
                        "command": "sudo apt-get install -y ffmpeg",
                        "exit_code": 0,
                        "stdout": "",
                        "stderr": "",
                    }
                },
            )(),
        ) as run_mock:
            payload = install_package("ffmpeg", settings=self._settings_with_fake_key())

        run_mock.assert_called_once()
        self.assertEqual(run_mock.call_args.args[0], "sudo apt-get install -y ffmpeg")
        self.assertEqual(payload["package_name"], "ffmpeg")

    def test_run_project_command_returns_workdir(self) -> None:
        with patch(
            "tools.ubuntu_control.UbuntuControl.run",
            return_value=type(
                "Result",
                (),
                {
                    "to_dict": lambda self: {
                        "command": "python -m pytest",
                        "exit_code": 0,
                        "stdout": "",
                        "stderr": "",
                    }
                },
            )(),
        ):
            payload = run_project_command("python -m pytest", settings=self._settings_with_fake_key())

        self.assertEqual(payload["workdir"], "/home/atnos/atnos")

    def test_blocked_commands_cannot_remove_atnos_or_disable_ssh(self) -> None:
        control = UbuntuControl(settings=self._settings_with_fake_key())

        with self.assertRaises(UbuntuControlBlockedCommand):
            control._enforce_hard_safety_blocks("sudo deluser atnos sudo")
        with self.assertRaises(UbuntuControlBlockedCommand):
            control._enforce_hard_safety_blocks("sudo systemctl stop ssh")

    def _settings_with_fake_key(self) -> Settings:
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        key_path = root / "atnos_lab"
        key_path.write_text("fake-key", encoding="utf-8")
        env_path = root / ".env"
        env_path.write_text(
            "\n".join(
                (
                    "ATNOS_UBUNTU_HOST=198.51.100.216",
                    "ATNOS_UBUNTU_USER=atnos",
                    f"ATNOS_UBUNTU_KEY={key_path}",
                    "ATNOS_UBUNTU_WORKDIR=/home/atnos/atnos",
                    "ATNOS_UBUNTU_ALLOW_SUDO=true",
                    "ATNOS_UBUNTU_REQUIRE_APPROVAL_FOR_SUDO=false",
                )
            ),
            encoding="utf-8",
        )
        with patch.dict(os.environ, {key: "" for key in _UBUNTU_ENV_KEYS}, clear=False):
            for key in _UBUNTU_ENV_KEYS:
                os.environ.pop(key, None)
            return Settings(app_root=root, data_dir=root / "data", _env_file=env_path)


if __name__ == "__main__":
    unittest.main()
