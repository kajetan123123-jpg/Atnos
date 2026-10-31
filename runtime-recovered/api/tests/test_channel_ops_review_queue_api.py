from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from channel_ops import ChannelOpsService, ContentStatus
from test_support import create_test_app, make_temp_dir


class ChannelOpsReviewQueueApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_review_queue_endpoint_returns_ready_and_repair_items(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        service = ChannelOpsService(app.state.settings)
        service.register_channel(
            channel_id="faceless-ai-pl",
            display_name="Faceless AI PL",
            niche="AI shorty",
            language="pl",
            audience="tworcy solo",
        )

        ready = service.enqueue_topic("faceless-ai-pl", "Gotowy short do review")
        ready_dir = self.root / "ready-short"
        ready_dir.mkdir()
        (ready_dir / "final.mp4").write_bytes(b"mp4")
        (ready_dir / "render_qa.json").write_text(
            json.dumps({"ok": True, "youtube_shorts_ready": True, "quality_score": 92}),
            encoding="utf-8",
        )
        (ready_dir / "visual_quality_report.json").write_text(
            json.dumps({"passed": True, "overall_score": 9.2, "issues": []}),
            encoding="utf-8",
        )
        (ready_dir / "youtube_upload_package.json").write_text(
            json.dumps(
                {
                    "status": "manual_upload_ready",
                    "checks": {
                        "script_quality_score": 8.6,
                        "script_quality_passed": True,
                        "render_ok": True,
                        "youtube_shorts_ready": True,
                        "visual_quality_passed": True,
                        "visual_quality_score": 9.2,
                    },
                }
            ),
            encoding="utf-8",
        )
        ready_item = service.mark_render_ready(ready.item_id, ready_dir)
        service.calendar.update_item(ready_item.with_update(status=ContentStatus.MANUAL_UPLOAD_READY))

        failed = service.enqueue_topic("faceless-ai-pl", "Short do naprawy")
        service.calendar.update_item(failed.with_update(status=ContentStatus.QA_FAILED))

        with TestClient(app) as client:
            response = client.get("/channel-ops/review-queue")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["item_count"], 2)
        self.assertEqual(payload["ready_count"], 1)
        self.assertEqual(payload["repair_count"], 1)
        self.assertTrue(Path(payload["json_path"]).exists())
        self.assertTrue(Path(payload["markdown_path"]).exists())
        statuses = {item["topic"]: item["status"] for item in payload["items"]}
        self.assertEqual(statuses["Gotowy short do review"], "manual_upload_ready")
        self.assertEqual(statuses["Short do naprawy"], "qa_failed")


if __name__ == "__main__":
    unittest.main()
