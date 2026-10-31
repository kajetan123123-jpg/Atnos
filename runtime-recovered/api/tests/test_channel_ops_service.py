from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from atnos.core.config import Settings
from channel_ops import ChannelOpsError, ChannelOpsService, ContentStatus


def make_settings(root: Path) -> Settings:
    return Settings(app_env="test", app_root=root, data_dir=root / "data")


def register_example_channel(service: ChannelOpsService):
    return service.register_channel(
        channel_id="local-ai-pl",
        display_name="Lokalne AI PL",
        niche="lokalne AI i automatyzacja",
        language="pl",
        audience="twórcy solo i mali przedsiębiorcy",
        daily_target_count=1,
    )


def write_pre_render_contract(output_dir: Path) -> None:
    (output_dir / "hook_variants.json").write_text(
        json.dumps(
            {
                "topic": "test",
                "selected_hook_id": "a",
                "hooks": [
                    {
                        "id": item,
                        "text": f"Hook {item}",
                        "visual_text": f"HOOK {item}",
                        "psychographic_target": "curiosity",
                        "score": 8.0,
                        "reason": "test",
                        "selected": item == "a",
                    }
                    for item in ("a", "b", "c", "d", "e")
                ],
            }
        ),
        encoding="utf-8",
    )
    (output_dir / "short_asset_plan.json").write_text(
        json.dumps(
            {
                "topic": "test",
                "selected_hook_id": "a",
                "scenes": [
                    {
                        "scene_id": "scene_01",
                        "start_sec": 0,
                        "duration_sec": 3,
                        "audio": "Hook",
                        "background": "test",
                        "overlay_text": "HOOK",
                        "visual_prompt": "test visual",
                        "caption": "Hook",
                        "sfx": "none",
                        "risk_notes": "none",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (output_dir / "visual_direction.md").write_text("# Visual Direction\n\n- test\n", encoding="utf-8")
    (output_dir / "quality_report.md").write_text("# Pre-render Quality Report\n\n- ready\n", encoding="utf-8")


class ChannelOpsServiceTest(unittest.TestCase):
    def test_register_channel_persists_for_new_service_instance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            first = ChannelOpsService(settings)
            register_example_channel(first)

            second = ChannelOpsService(settings)
            channels = second.list_channels()

            self.assertEqual(len(channels), 1)
            self.assertEqual(channels[0].channel_id, "local-ai-pl")
            self.assertTrue((settings.data_dir / "channel_ops" / "channels.json").exists())

    def test_register_channel_blocks_invalid_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = ChannelOpsService(make_settings(Path(tmp)))

            with self.assertRaises(ValidationError):
                service.register_channel(
                    channel_id="",
                    display_name="Lokalne AI PL",
                    niche="lokalne AI",
                    language="pl",
                    audience="twórcy",
                )

            with self.assertRaises(ValidationError):
                service.register_channel(
                    channel_id="local-ai-pl",
                    display_name="Lokalne AI PL",
                    niche="lokalne AI",
                    language="pl",
                    audience="twórcy",
                    daily_target_count=-1,
                )

            with self.assertRaises(ValidationError):
                service.register_channel(
                    channel_id="bad id",
                    display_name="Lokalne AI PL",
                    niche="lokalne AI",
                    language="pl",
                    audience="twórcy",
                )

    def test_enqueue_topic_creates_idea_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = ChannelOpsService(make_settings(Path(tmp)))
            register_example_channel(service)

            item = service.enqueue_topic("local-ai-pl", "Dlaczego lokalne AI ma sens")

            self.assertEqual(item.status, ContentStatus.IDEA)
            self.assertEqual(service.list_queue("local-ai-pl")[0].topic, "Dlaczego lokalne AI ma sens")

    def test_create_daily_plan_fills_missing_topics_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = ChannelOpsService(make_settings(Path(tmp)))
            register_example_channel(service)
            service.enqueue_topic("local-ai-pl", "Pierwszy gotowy temat")

            plan = service.create_daily_plan("local-ai-pl", 3)

            self.assertEqual(len(plan), 3)
            self.assertEqual(plan[0].topic, "Pierwszy gotowy temat")
            self.assertTrue(plan[1].topic.startswith("Jak "))
            self.assertIn("pierwszy workflow bez chaosu", plan[1].topic)
            self.assertEqual(plan[2].topic, "Lokalnie czy w chmurze: kiedy lokalne AI i automatyzacja ma najwiecej sensu")
            self.assertTrue(
                (service.registry.channel_dir("local-ai-pl") / "topic_bank.json").exists()
            )

    def test_create_daily_plan_uses_custom_channel_topic_bank(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = ChannelOpsService(make_settings(Path(tmp)))
            register_example_channel(service)
            topic_bank = service.registry.channel_dir("local-ai-pl") / "topic_bank.json"
            topic_bank.write_text(
                json.dumps({"templates": ["Custom topic {number} dla {niche}", "Drugi format dla {audience}"]}),
                encoding="utf-8",
            )

            plan = service.create_daily_plan("local-ai-pl", 2)

            self.assertEqual(plan[0].topic, "Custom topic 1 dla lokalne AI i automatyzacja")
            self.assertTrue(plan[1].topic.startswith("Drugi format dla "))

    def test_mark_render_ready_sets_rendered_without_qa(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = ChannelOpsService(make_settings(root))
            register_example_channel(service)
            item = service.enqueue_topic("local-ai-pl", "Short bez QA")
            output_dir = root / "renders" / "short-1"
            output_dir.mkdir(parents=True)

            updated = service.mark_render_ready(item.item_id, output_dir)

            self.assertEqual(updated.status, ContentStatus.RENDERED)
            self.assertEqual(updated.output_dir, str(output_dir.resolve()))
            self.assertEqual(updated.final_mp4, str((output_dir / "final.mp4").resolve()))
            self.assertIsNone(updated.quality_score)

    def test_mark_render_ready_sets_qa_passed_with_good_render_qa(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = ChannelOpsService(make_settings(root))
            register_example_channel(service)
            item = service.enqueue_topic("local-ai-pl", "Short z QA")
            output_dir = root / "renders" / "short-2"
            output_dir.mkdir(parents=True)
            (output_dir / "render_qa.json").write_text(
                json.dumps(
                    {
                        "has_audio": True,
                        "width": 1080,
                        "height": 1920,
                        "duration_seconds": 42,
                        "overall_score": 8.4,
                    }
                ),
                encoding="utf-8",
            )

            updated = service.mark_render_ready(item.item_id, output_dir)

            self.assertEqual(updated.status, ContentStatus.QA_PASSED)
            self.assertEqual(updated.quality_score, 8.4)

    def test_request_upload_approval_blocks_item_without_qa(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = ChannelOpsService(make_settings(root))
            register_example_channel(service)
            item = service.enqueue_topic("local-ai-pl", "Nie gotowy do uploadu")

            with self.assertRaises(ChannelOpsError):
                service.request_upload_approval(item.item_id)

    def test_request_upload_approval_sets_pending_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = ChannelOpsService(make_settings(root))
            register_example_channel(service)
            item = service.enqueue_topic("local-ai-pl", "Gotowy do approval")
            output_dir = root / "renders" / "short-3"
            output_dir.mkdir(parents=True)
            (output_dir / "final.mp4").write_bytes(b"mp4")
            (output_dir / "render_qa.json").write_text(
                json.dumps({"ok": True, "youtube_shorts_ready": True, "quality_score": 91}),
                encoding="utf-8",
            )
            (output_dir / "visual_quality_report.json").write_text(
                json.dumps({"passed": True, "overall_score": 8.5, "issues": []}),
                encoding="utf-8",
            )
            (output_dir / "youtube_upload_package.json").write_text(
                json.dumps(
                    {
                        "status": "manual_upload_ready",
                        "checks": {
                            "script_quality_score": 8.5,
                            "script_quality_passed": True,
                            "render_ok": True,
                            "youtube_shorts_ready": True,
                            "visual_quality_passed": True,
                            "visual_quality_score": 8.5,
                        },
                    }
                ),
                encoding="utf-8",
            )
            write_pre_render_contract(output_dir)
            ready = service.mark_render_ready(item.item_id, output_dir)

            pending = service.request_upload_approval(ready.item_id)

            self.assertEqual(pending.status, ContentStatus.APPROVAL_PENDING)
            self.assertIsNotNone(pending.upload_approval_id)
            self.assertTrue(pending.upload_approval_id.startswith("channelops-"))

    def test_request_upload_approval_blocks_recovered_watchdog_render(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = ChannelOpsService(make_settings(root))
            register_example_channel(service)
            item = service.enqueue_topic("local-ai-pl", "Render z warningiem")
            output_dir = root / "renders" / "short-watchdog"
            output_dir.mkdir(parents=True)
            (output_dir / "final.mp4").write_bytes(b"mp4")
            (output_dir / "render_qa.json").write_text(
                json.dumps(
                    {
                        "ok": False,
                        "youtube_shorts_ready": True,
                        "quality_score": 91,
                        "warnings": [
                            "Render exceeded the 420s watchdog but a valid output was recovered after the worker was stopped."
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (output_dir / "youtube_upload_package.json").write_text(
                json.dumps(
                    {
                        "status": "needs_review_before_upload",
                        "checks": {
                            "script_quality_score": 8.5,
                            "script_quality_passed": True,
                            "render_ok": False,
                            "youtube_shorts_ready": True,
                            "visual_quality_passed": True,
                            "visual_quality_score": 8.5,
                        },
                    }
                ),
                encoding="utf-8",
            )
            write_pre_render_contract(output_dir)
            ready = service.mark_render_ready(item.item_id, output_dir)

            with self.assertRaisesRegex(ChannelOpsError, "Upload approval blocked by quality gate"):
                service.request_upload_approval(ready.item_id)

    def test_validate_upload_quality_blocks_missing_pre_render_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = ChannelOpsService(make_settings(root))
            register_example_channel(service)
            item = service.enqueue_topic("local-ai-pl", "Gotowy technicznie bez kontraktu")
            output_dir = root / "renders" / "short-missing-contract"
            output_dir.mkdir(parents=True)
            (output_dir / "final.mp4").write_bytes(b"mp4")
            (output_dir / "render_qa.json").write_text(
                json.dumps({"ok": True, "youtube_shorts_ready": True, "quality_score": 91}),
                encoding="utf-8",
            )
            (output_dir / "visual_quality_report.json").write_text(
                json.dumps({"passed": True, "overall_score": 8.5, "issues": []}),
                encoding="utf-8",
            )
            (output_dir / "youtube_upload_package.json").write_text(
                json.dumps(
                    {
                        "status": "manual_upload_ready",
                        "checks": {
                            "script_quality_score": 8.5,
                            "script_quality_passed": True,
                            "render_ok": True,
                            "youtube_shorts_ready": True,
                            "visual_quality_passed": True,
                            "visual_quality_score": 8.5,
                        },
                    }
                ),
                encoding="utf-8",
            )
            ready = service.mark_render_ready(item.item_id, output_dir)

            result = service.validate_upload_quality(ready.item_id)

            self.assertFalse(result["ready"])
            self.assertTrue(any("pre-render contract" in issue for issue in result["issues"]))

    def test_mark_uploaded_private_stores_video_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = ChannelOpsService(make_settings(root))
            register_example_channel(service)
            item = service.enqueue_topic("local-ai-pl", "Uploaded private")

            uploaded = service.mark_uploaded_private(item.item_id, "abc123")

            self.assertEqual(uploaded.status, ContentStatus.PRIVATE_UPLOADED)
            self.assertEqual(uploaded.youtube_video_id, "abc123")

    def test_update_analytics_stores_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = ChannelOpsService(make_settings(Path(tmp)))
            register_example_channel(service)
            item = service.enqueue_topic("local-ai-pl", "Analytics")

            updated = service.update_analytics(item.item_id, {"views": 120, "likes": 4})

            self.assertEqual(updated.analytics_summary, {"views": 120, "likes": 4})

    def test_recommend_next_actions_and_jsonl_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = make_settings(root)
            service = ChannelOpsService(settings)
            register_example_channel(service)
            service.enqueue_topic("local-ai-pl", "Do renderu")

            actions = service.recommend_next_actions("local-ai-pl")
            log_text = (settings.data_dir / "logs" / "channel_ops.jsonl").read_text(encoding="utf-8")

            self.assertIn("render_pending_items", actions)
            self.assertIn("channel_registered", log_text)
            self.assertIn("content_topic_enqueued", log_text)
            self.assertIn("next_actions_recommended", log_text)


if __name__ == "__main__":
    unittest.main()
