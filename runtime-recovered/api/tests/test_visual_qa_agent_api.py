from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.services.screen_capture_service import ScreenArtifact
from atnos.services.visual_qa_service import VisualQAResult
from test_support import create_test_app, make_temp_dir


class VisualQAAgentApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.app = create_test_app(self.root)
        self.addCleanup(self.app.state.database.dispose)

    def test_capture_screenshot_endpoint_returns_artifact(self) -> None:
        screenshot_path = self.root / "data" / "visual-qa" / "captures" / "screen.png"
        metadata_path = screenshot_path.with_suffix(".png.json")
        artifact = ScreenArtifact(
            kind="screenshot",
            path=screenshot_path,
            metadata_path=metadata_path,
            label="ui-check",
            created_at="2026-05-18T00:00:00",
        )

        with patch("atnos.api.routes.agent.ScreenCaptureService") as service_cls:
            service_cls.return_value.capture_screenshot.return_value = artifact
            with TestClient(self.app) as client:
                response = client.post("/agent/visual-qa/screenshot", json={"label": "ui-check"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["artifact"]["kind"], "screenshot")
        self.assertEqual(payload["artifact"]["path"], str(screenshot_path))
        service_cls.return_value.capture_screenshot.assert_called_once_with(label="ui-check")

    def test_review_endpoint_returns_codex_handoff_package(self) -> None:
        result = VisualQAResult(
            status="package_ready",
            goal="popraw chat",
            screenshot_path=self.root / "data" / "visual-qa" / "captures" / "screen.png",
            recording_path=None,
            review_provider_key=None,
            review_model_key=None,
            review_text=None,
            prompt_path=self.root / "data" / "visual-qa" / "reviews" / "prompt.md",
            report_path=self.root / "data" / "visual-qa" / "reviews" / "report.json",
            codex_handoff_path=self.root / "data" / "visual-qa" / "codex-handoff" / "handoff.md",
            message="Prepared Visual QA package for Codex handoff.",
        )

        with patch("atnos.api.routes.agent.VisualQAService") as service_cls:
            service_cls.return_value.run_review.return_value = result
            with TestClient(self.app) as client:
                response = client.post(
                    "/agent/visual-qa/review",
                    json={
                        "goal": "popraw chat",
                        "area": "Chat",
                        "capture_screenshot": False,
                        "allow_cloud_review": False,
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()["visual_qa"]
        self.assertEqual(payload["status"], "package_ready")
        self.assertTrue(payload["codex_handoff_path"].endswith("handoff.md"))
        service_cls.return_value.run_review.assert_called_once()
        self.assertFalse(service_cls.return_value.run_review.call_args.kwargs["allow_cloud_review"])


if __name__ == "__main__":
    unittest.main()
