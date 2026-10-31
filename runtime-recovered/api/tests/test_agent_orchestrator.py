from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from agent import AgentOrchestrator
from agent.models import AgentRunStatus, AgentTaskType, RiskLevel
from agent.risk_policy import RiskPolicy
from agent.task_classifier import TaskClassifier
from atnos.core.config import Settings


class AgentOrchestratorTest(unittest.TestCase):
    def test_classifier_detects_ubuntu_and_short_tasks(self) -> None:
        classifier = TaskClassifier()

        ubuntu = classifier.classify("sprawdz laptop Ubuntu")
        short = classifier.classify("zrob shorta o lokalnym AI")

        self.assertEqual(ubuntu.task_type, AgentTaskType.UBUNTU_CONTROL)
        self.assertEqual(short.task_type, AgentTaskType.CONTENT_SHORT)

    def test_risk_policy_marks_sudo_as_high(self) -> None:
        policy = RiskPolicy()

        risk = policy.classify("uruchom sudo apt update na Ubuntu", AgentTaskType.UBUNTU_CONTROL, tuple())

        self.assertEqual(risk, RiskLevel.HIGH)

    def test_orchestrator_runs_ubuntu_check_and_writes_logs(self) -> None:
        settings = self._settings()
        fake_payload = {
            "ok": True,
            "host": "198.51.100.216",
            "user": "atnos",
            "workdir": "/home/atnos/atnos",
            "checks": {"whoami": {"exit_code": 0, "stdout": "atnos\n", "stderr": ""}},
        }
        with patch("agent.tool_executor.check_ubuntu_connection", return_value=fake_payload):
            result = AgentOrchestrator(settings=settings).run("sprawdz laptop Ubuntu")

        self.assertEqual(result.status, AgentRunStatus.COMPLETED)
        self.assertEqual(result.task_type, AgentTaskType.UBUNTU_CONTROL)
        self.assertEqual(result.tool_results[0].tool_name, "ubuntu_check")
        self.assertTrue(settings.agent_decisions_log_path.exists())
        self.assertTrue(settings.agent_tool_calls_log_path.exists())
        self.assertIn("agent_route", settings.agent_decisions_log_path.read_text(encoding="utf-8"))
        self.assertIn("ubuntu_check", settings.agent_tool_calls_log_path.read_text(encoding="utf-8"))

    def test_orchestrator_blocks_main_pc_high_risk_when_not_sandbox(self) -> None:
        settings = self._settings()
        settings.atnos_sandbox_allow_high_risk = False
        settings.atnos_require_approval_for_main_pc_high_risk = True

        result = AgentOrchestrator(settings=settings).run("usun pliki i zrob upload na youtube")

        self.assertEqual(result.status, AgentRunStatus.AWAITING_APPROVAL)
        self.assertTrue(result.approval_required)

    def test_content_short_without_runtime_context_returns_clear_failure(self) -> None:
        settings = self._settings()

        result = AgentOrchestrator(settings=settings).run("zrob shorta o lokalnym AI")

        self.assertEqual(result.task_type, AgentTaskType.CONTENT_SHORT)
        self.assertEqual(result.status, AgentRunStatus.FAILED)
        self.assertEqual(result.tool_results[0].status, "failed")
        self.assertEqual(result.tool_results[0].result["status"], "runtime_context_required")

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
