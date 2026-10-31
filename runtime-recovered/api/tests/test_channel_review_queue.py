from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from atnos.core.config import Settings
from channel_ops import ChannelOpsService, ChannelReviewQueue, ContentStatus


def make_settings(root: Path) -> Settings:
    return Settings(app_env="test", app_root=root, data_dir=root / "data")


class ChannelReviewQueueTest(unittest.TestCase):
    def test_review_queue_collects_ready_and_repair_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = make_settings(root)
            service = ChannelOpsService(settings)
            service.register_channel(
                channel_id="local-ai-pl",
                display_name="Lokalne AI PL",
                niche="lokalne AI",
                language="pl",
                audience="tworcy solo",
            )
            ready = service.enqueue_topic("local-ai-pl", "Gotowy short")
            ready_dir = root / "ready"
            ready_dir.mkdir()
            (ready_dir / "final.mp4").write_bytes(b"mp4")
            (ready_dir / "render_qa.json").write_text(
                json.dumps({"ok": True, "youtube_shorts_ready": True, "quality_score": 91}),
                encoding="utf-8",
            )
            (ready_dir / "visual_quality_report.json").write_text(
                json.dumps({"passed": True, "overall_score": 9.0, "issues": []}),
                encoding="utf-8",
            )
            (ready_dir / "youtube_upload_package.json").write_text(
                json.dumps(
                    {
                        "status": "manual_upload_ready",
                        "checks": {
                            "script_quality_score": 8.4,
                            "script_quality_passed": True,
                            "render_ok": True,
                            "youtube_shorts_ready": True,
                            "visual_quality_passed": True,
                            "visual_quality_score": 9.0,
                        },
                    }
                ),
                encoding="utf-8",
            )
            ready_item = service.mark_render_ready(ready.item_id, ready_dir)
            service.calendar.update_item(ready_item.with_update(status=ContentStatus.MANUAL_UPLOAD_READY))

            failed = service.enqueue_topic("local-ai-pl", "Do naprawy")
            service.calendar.update_item(failed.with_update(status=ContentStatus.QA_FAILED))

            result = ChannelReviewQueue(service).write()

            self.assertEqual(result.item_count, 2)
            self.assertEqual(result.ready_count, 1)
            self.assertEqual(result.repair_count, 1)
            payload = json.loads(result.json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["ready_count"], 1)
            self.assertEqual(payload["repair_count"], 1)
            markdown = result.markdown_path.read_text(encoding="utf-8")
            self.assertIn("Gotowy short", markdown)
            self.assertIn("Do naprawy", markdown)
            self.assertIn("visual=9.0", markdown)


if __name__ == "__main__":
    unittest.main()
