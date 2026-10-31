from __future__ import annotations

from pathlib import Path
import unittest


class AuditOrphanServicesTest(unittest.TestCase):
    def test_jarvis_grade_services_have_runtime_callers_and_dedicated_tests(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        services_root = repo_root / "src" / "atnos" / "services"
        tests_root = repo_root / "tests"
        service_expectations = {
            "ambient_context_service.py": [
                ("api/main.py", "AmbientContextService"),
                ("api/routes/ui.py", "ambient_context_service"),
            ],
            "voice_ambient_listener_service.py": [
                ("api/main.py", "VoiceAmbientListenerService"),
                ("api/routes/ui.py", "voice_ambient_listener_service"),
            ],
            "proactive_briefing_service.py": [
                ("api/routes/chat.py", "ProactiveBriefingService"),
                ("api/routes/ui.py", "ProactiveBriefingService"),
                ("services/voice_ambient_listener_service.py", "ProactiveBriefingService"),
            ],
            "decision_log_service.py": [
                ("services/chat_orchestration_service.py", "DecisionLogService"),
                ("services/briefing_service.py", "DecisionLogService"),
                ("services/settings_control_center_service.py", "DecisionLogRepository"),
            ],
            "trend_watch_service.py": [
                ("services/settings_control_center_service.py", "TrendWatchService"),
                ("services/scheduler_runtime_service.py", "TrendWatchService"),
                ("api/routes/ui.py", "trend-watch"),
            ],
            "creative_trend_evaluator_service.py": [
                ("services/synthetic_variant_scoring_service.py", "CreativeTrendEvaluatorService"),
            ],
        }
        expected_tests = {
            "test_ambient_context.py",
            "test_voice_ambient_listener.py",
            "test_proactive_briefing.py",
            "test_decision_log.py",
            "test_persona_jarvis_mode.py",
            "test_hardware_guards.py",
            "test_capability_plugin_permission.py",
            "test_trend_watch_service.py",
            "test_creative_trend_evaluator_service.py",
            "test_trend_watch_api.py",
        }

        for service_name, caller_paths in service_expectations.items():
            service_path = services_root / service_name
            self.assertTrue(service_path.exists(), f"Missing service file: {service_name}")
            for caller_path, expected_snippet in caller_paths:
                caller_file = repo_root / "src" / "atnos" / caller_path
                self.assertTrue(caller_file.exists(), f"Missing caller file: {caller_path}")
                self.assertIn(expected_snippet, caller_file.read_text(encoding="utf-8"))

        for test_name in expected_tests:
            self.assertTrue((tests_root / test_name).exists(), f"Missing test file: {test_name}")
