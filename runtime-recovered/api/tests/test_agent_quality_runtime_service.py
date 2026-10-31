from __future__ import annotations

from pathlib import Path
import sys
import unittest

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.services.agent_quality_runtime_service import AgentQualityRuntimeService, QualityCheck
from test_support import create_test_app, make_temp_dir


class AgentQualityRuntimeServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.app = create_test_app(self.root)
        self.addCleanup(self.app.state.database.dispose)

    def test_records_verified_quality_run_with_json_markdown_and_decision_log(self) -> None:
        service = AgentQualityRuntimeService(self.app.state.settings)

        result = service.record_run(
            task="Sprawdz Chat po zmianie zakladki",
            area="frontend-chat",
            checks=[
                QualityCheck(
                    name="route_visible",
                    status="pass",
                    evidence="Chat header rendered after returning from settings.",
                    score=9,
                ),
                QualityCheck(
                    name="console_clean",
                    status="pass",
                    evidence="No runtime console errors observed.",
                    score=8,
                ),
            ],
            artifacts=["F:/Apps/Atnos/frontend/src/routes/Chat.tsx"],
        )

        self.assertEqual(result["status"], "DONE_VERIFIED")
        self.assertEqual(result["score"], 8.5)
        self.assertTrue(Path(result["json_path"]).is_file())
        self.assertTrue(Path(result["report_path"]).is_file())
        self.assertIn("DONE_VERIFIED", Path(result["report_path"]).read_text(encoding="utf-8"))
        self.assertIn("agent_quality_run_recorded", self.app.state.settings.agent_decisions_log_path.read_text(encoding="utf-8"))

    def test_warn_or_low_score_requires_review(self) -> None:
        service = AgentQualityRuntimeService(self.app.state.settings)
        result = service.record_run(
            task="Wygeneruj shorta",
            area="content-short",
            checks=[
                QualityCheck(name="hook", status="warn", evidence="Hook is generic.", score=6.5),
                QualityCheck(name="render", status="pass", evidence="MP4 exists.", score=8),
            ],
            min_score=7,
        )

        self.assertEqual(result["status"], "DONE_NEEDS_REVIEW")

    def test_failed_required_check_returns_failed_with_reason_and_masks_secrets(self) -> None:
        service = AgentQualityRuntimeService(self.app.state.settings)
        result = service.record_run(
            task="Call provider with api_key=sk-test-secret-value",
            area="provider",
            checks=[
                QualityCheck(
                    name="provider_call",
                    status="fail",
                    evidence="Token sk-test-secret-value failed.",
                    score=0,
                )
            ],
        )

        self.assertEqual(result["status"], "FAILED_WITH_REASON")
        serialized = Path(result["json_path"]).read_text(encoding="utf-8")
        self.assertNotIn("sk-test-secret-value", serialized)
        self.assertIn("[REDACTED]", serialized)

    def test_quality_run_api_and_status_surface_latest_run(self) -> None:
        with TestClient(self.app) as client:
            response = client.post(
                "/agent/quality-runs",
                json={
                    "task": "Smoke route quality gate",
                    "area": "agent-panel",
                    "checks": [
                        {
                            "name": "status_endpoint",
                            "status": "pass",
                            "evidence": "Endpoint returned 200.",
                            "score": 9,
                        }
                    ],
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["quality_run"]["status"], "DONE_VERIFIED")

            status_response = client.get("/agent/status")
            self.assertEqual(status_response.status_code, 200)
            status_payload = status_response.json()
            self.assertIn("quality_runtime", status_payload)
            self.assertEqual(
                status_payload["quality_runtime"]["latest_run"]["status"],
                "DONE_VERIFIED",
            )

            recent_response = client.get("/agent/quality-runs/recent")
            self.assertEqual(recent_response.status_code, 200)
            self.assertEqual(len(recent_response.json()["quality_runs"]), 1)


if __name__ == "__main__":
    unittest.main()
