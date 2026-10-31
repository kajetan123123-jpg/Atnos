from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from agent.models import AgentRunStatus
from agent.ubuntu_agent_adapter import UbuntuAgentAdapter
from atnos.core.config import Settings


class UbuntuAgentAdapterTest(unittest.TestCase):
    def test_system_report_maps_ssh_results_to_chat_response_and_logs(self) -> None:
        settings = self._settings()
        fake_check = {
            "ok": True,
            "host": "198.51.100.216",
            "user": "atnos",
            "workdir": "/home/atnos/atnos",
            "checks": {},
        }
        fake_report = {
            "command": "python3 - <<'PY'",
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "report_path": "/home/atnos/atnos/logs/system_report.md",
        }

        with patch("agent.tool_executor.check_ubuntu_connection", return_value=fake_check), patch(
            "agent.tool_executor.create_system_report",
            return_value=fake_report,
        ):
            result = UbuntuAgentAdapter(settings=settings).maybe_run("Sprawdź laptop Ubuntu i zapisz system_report.md")

        assert result is not None
        self.assertEqual(result.status, AgentRunStatus.COMPLETED)
        self.assertIn("system_report.md", result.response)
        self.assertTrue(settings.agent_tool_calls_log_path.exists())
        log_text = settings.agent_tool_calls_log_path.read_text(encoding="utf-8")
        self.assertIn("ubuntu_system_report", log_text)
        self.assertIn("198.51.100.216", log_text)

    def test_ignores_general_test_request_without_ubuntu_context(self) -> None:
        settings = self._settings()

        result = UbuntuAgentAdapter(settings=settings).maybe_run("Uruchom testy")

        self.assertIsNone(result)

    def _settings(self) -> Settings:
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
                    "ATNOS_AGENT_AUTONOMY_LEVEL=full_sandbox",
                    "ATNOS_LOG_ALL_ACTIONS=true",
                    "ATNOS_REQUIRE_APPROVAL_FOR_PUBLICATION=true",
                    "ATNOS_REQUIRE_APPROVAL_FOR_MAIN_PC_HIGH_RISK=true",
                    "ATNOS_SANDBOX_ALLOW_HIGH_RISK=true",
                )
            ),
            encoding="utf-8",
        )
        return Settings(app_root=root, data_dir=root / "data", _env_file=env_path)


if __name__ == "__main__":
    unittest.main()
