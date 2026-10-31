from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from atnos.api.main import create_app
from atnos.core.config import Settings
from atnos.services.n8n_youtube_automation_service import (
    N8nYouTubeAutomationError,
    N8nYouTubeAutomationService,
)


class _FakeExecute:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class _FakeVideos:
    def insert(self, **kwargs):
        self.kwargs = kwargs
        return _FakeExecute({"id": "video123"})


class _FakeThumbnails:
    def set(self, **kwargs):
        self.kwargs = kwargs
        return _FakeExecute({"ok": True})


class _FakeYouTube:
    def __init__(self):
        self._videos = _FakeVideos()
        self._thumbnails = _FakeThumbnails()

    def videos(self):
        return self._videos

    def thumbnails(self):
        return self._thumbnails


class N8nYouTubeAutomationTest(unittest.TestCase):
    def test_scan_create_approve_upload_private_flow(self) -> None:
        settings, run_dir = self._settings_with_short()
        service = N8nYouTubeAutomationService(settings)

        queue = service.scan_ready_shorts()
        self.assertEqual(len(queue), 1)
        self.assertTrue(queue[0].ready_for_upload)

        approval = service.create_upload_approval(video_path=run_dir / "final.mp4")
        approval_id = str(approval["approval_id"])
        self.assertEqual(approval["status"], "pending")
        self.assertEqual(approval["preview"]["metadata"]["privacy"], "private")

        with self.assertRaises(N8nYouTubeAutomationError):
            service.upload_approved(approval_id, youtube_service=_FakeYouTube())

        approved = service.approve_upload(approval_id)
        self.assertEqual(approved["status"], "approved")

        uploaded = service.upload_approved(approval_id, youtube_service=_FakeYouTube())
        self.assertEqual(uploaded["status"], "uploaded_private")
        self.assertEqual(uploaded["result"]["video_id"], "video123")
        self.assertIn("video123", (settings.data_dir / "logs" / "youtube_uploads.jsonl").read_text(encoding="utf-8"))

    def test_route_exposes_n8n_status_and_queue(self) -> None:
        settings, _ = self._settings_with_short()
        app = create_app(settings=settings)
        client = TestClient(app)

        status = client.get("/n8n/status")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["mode"], "approval_required")
        self.assertEqual(status.json()["n8n_docker_status"], "not_checked")
        self.assertIn("fallback_runner_status", status.json())

        queue = client.get("/n8n/youtube/queue")
        self.assertEqual(queue.status_code, 200)
        self.assertEqual(len(queue.json()["items"]), 1)

    def _settings_with_short(self) -> tuple[Settings, Path]:
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        run_dir = root / "data" / "shorts" / "20260520-test-short"
        run_dir.mkdir(parents=True)
        (run_dir / "final.mp4").write_bytes(b"fake-video")
        (run_dir / "thumbnail.png").write_bytes(b"fake-png")
        (run_dir / "subtitles.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nTest\n", encoding="utf-8")
        (run_dir / "render_qa.json").write_text(
            json.dumps({"has_audio": True, "width": 1080, "height": 1920, "duration": 31.5}),
            encoding="utf-8",
        )
        (run_dir / "youtube_upload_package.json").write_text(
            json.dumps(
                {
                    "title": "Test private short",
                    "description": "Private approval test.",
                    "tags": ["Atnos", "Shorts"],
                    "privacy": "private",
                    "requires_upload_approval": True,
                }
            ),
            encoding="utf-8",
        )
        settings = Settings(
            app_env="test",
            app_root=root,
            data_dir=root / "data",
            shorts_output_dir=root / "data" / "shorts",
            n8n_data_dir=root / "data" / "n8n",
            youtube_client_secret_file=root / "client_secret.json",
            youtube_upload_privacy="private",
            allow_public_upload=False,
            require_upload_approval=True,
        )
        settings.ensure_directories()
        return settings, run_dir

    def test_quality_gate_blocks_bad_short_before_approval(self) -> None:
        settings, run_dir = self._settings_with_short()
        (run_dir / "render_qa.json").write_text(
            json.dumps({"has_audio": False, "width": 1920, "height": 1080, "duration": 4.0}),
            encoding="utf-8",
        )
        service = N8nYouTubeAutomationService(settings)

        queue = service.scan_ready_shorts()
        self.assertFalse(queue[0].ready_for_upload)
        self.assertEqual(queue[0].quality_status, "needs_repair")

        with self.assertRaises(N8nYouTubeAutomationError):
            service.create_upload_approval(video_path=run_dir / "final.mp4")

    def test_daily_ops_can_prepare_next_approval(self) -> None:
        settings, _ = self._settings_with_short()
        service = N8nYouTubeAutomationService(settings)

        result = service.run_daily_ops(create_approval=True)

        self.assertEqual(result["ready_candidates"], 1)
        self.assertIsNotNone(result["approval"])
        self.assertEqual(result["recommendation"], "approval_created_review_before_private_upload")

    def test_queue_title_falls_back_to_production_report(self) -> None:
        settings, run_dir = self._settings_with_short()
        (run_dir / "youtube_upload_package.json").unlink()
        (run_dir / "production_report.md").write_text(
            "# Production Report\n\n- Title: Lokalny lab AI dla twórców\n",
            encoding="utf-8",
        )
        service = N8nYouTubeAutomationService(settings)

        queue = service.scan_ready_shorts()

        self.assertEqual(queue[0].title, "Lokalny lab AI dla twórców")


if __name__ == "__main__":
    unittest.main()
