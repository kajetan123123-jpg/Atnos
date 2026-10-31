from __future__ import annotations

from pathlib import Path
import sys
import unittest

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.services.quality_council_service import QualityCouncilService
from test_support import create_test_app, make_temp_dir


class QualityCouncilServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addAsyncCleanup(self._cleanup)
        self.root = Path(self.temp_dir.name)
        self.app = create_test_app(self.root)

    async def _cleanup(self) -> None:
        self.app.state.database.dispose()
        self.temp_dir.cleanup()

    async def test_run_writes_evidence_first_reports_and_blocks_gmail_when_disconnected(self) -> None:
        with self.app.state.database.session_scope() as session:
            result = await QualityCouncilService(session, self.app.state.settings).run(
                audits=("application_ui", "video_quality", "investment_strategy", "security_permissions"),
                include_external_handoffs=False,
            )

        self.assertTrue(Path(result["run_dir"]).is_dir())
        self.assertEqual(result["delivery"]["status"], "blocked")
        self.assertFalse(result["delivery"]["sent"])
        self.assertTrue(Path(result["coordinator"]["markdown_path"]).is_file())
        video_report = next(item for item in result["reports"] if item["key"] == "video_quality")
        self.assertTrue(video_report["not_verified"])
        self.assertIn("output_scan", [item["check"] for item in video_report["evidence"]])
        coordinator = result["coordinator"]
        self.assertIsNone(coordinator["ratings"]["jakość filmów"]["score_0_to_100"])

    async def test_external_handoff_is_not_claimed_as_external_answer(self) -> None:
        with self.app.state.database.session_scope() as session:
            result = await QualityCouncilService(session, self.app.state.settings).run(
                audits=("claude_design", "kimi_models"),
                include_external_handoffs=True,
                attempt_gmail_delivery=False,
            )

        for report in result["reports"]:
            self.assertIn("nie została jeszcze odebrana", " ".join(report["not_verified"]))
            self.assertEqual(report["confidence"], "low")


class QualityCouncilRouteTest(unittest.TestCase):
    def test_status_and_run_routes(self) -> None:
        temp_dir = make_temp_dir()
        self.addCleanup(temp_dir.cleanup)
        app = create_test_app(Path(temp_dir.name))
        self.addCleanup(app.state.database.dispose)
        with TestClient(app) as client:
            status = client.get("/api/quality-council/status")
            self.assertEqual(status.status_code, 200)
            self.assertTrue(status.json()["ready"])
            response = client.post(
                "/api/quality-council/run",
                json={
                    "audits": ["video_quality"],
                    "include_external_handoffs": False,
                    "attempt_gmail_delivery": False,
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["reports"][0]["key"], "video_quality")


if __name__ == "__main__":
    unittest.main()
