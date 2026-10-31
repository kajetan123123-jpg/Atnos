from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from atnos.core.config import Settings
from channel_ops import ChannelOpsService, ContentStatus, DailyChannelRunner
from content.short_agent import ShortAgentResult


def make_settings(root: Path) -> Settings:
    return Settings(app_env="test", app_root=root, data_dir=root / "data")


def register_channel(service: ChannelOpsService, *, daily_target_count: int = 2, enabled: bool = True):
    return service.register_channel(
        channel_id="local-ai-pl",
        display_name="Lokalne AI PL",
        niche="lokalne AI i automatyzacja",
        language="pl",
        audience="tworcy solo",
        daily_target_count=daily_target_count,
        enabled=enabled,
    )


class FakeShortAgent:
    def __init__(
        self,
        settings: Settings,
        *,
        status: str = "manual_upload_ready",
        qa_ready: bool = True,
        upload_quality_ready: bool = True,
        write_contract: bool = True,
    ) -> None:
        self.settings = settings
        self.status = status
        self.qa_ready = qa_ready
        self.upload_quality_ready = upload_quality_ready
        self.write_contract = write_contract

    def run(self, topic: str, *, force_repair: bool = False) -> ShortAgentResult:
        slug = re.sub(r"[^a-z0-9]+", "-", topic.casefold()).strip("-") or "short"
        output_dir = self.settings.shorts_output_dir / slug[:40]
        output_dir.mkdir(parents=True, exist_ok=True)
        artifacts = {
            "script": str(output_dir / "script.md"),
            "hook_variants": str(output_dir / "hook_variants.json"),
            "short_asset_plan": str(output_dir / "short_asset_plan.json"),
            "visual_direction": str(output_dir / "visual_direction.md"),
            "pre_render_quality_report": str(output_dir / "quality_report.md"),
            "narration": str(output_dir / "narration.wav"),
            "subtitles": str(output_dir / "subtitles.srt"),
            "thumbnail": str(output_dir / "thumbnail.png"),
            "visual_storyboard": str(output_dir / "visual_storyboard.json"),
            "visual_quality_report": str(output_dir / "visual_quality_report.json"),
            "render_qa": str(output_dir / "render_qa.json"),
            "production_report": str(output_dir / "production_report.md"),
        }
        contract_artifacts = {"hook_variants", "short_asset_plan", "visual_direction", "pre_render_quality_report"}
        for name, path in artifacts.items():
            if name in contract_artifacts and not self.write_contract:
                continue
            Path(path).write_text("test", encoding="utf-8")
        if self.write_contract:
            _write_pre_render_contract(output_dir)
        if self.status == "NEEDS_HUMAN_REVIEW":
            return ShortAgentResult(
                status=self.status,
                topic=topic,
                output_dir=output_dir,
                final_mp4=None,
                quality_report={"overall_score": 4.0, "passed": False},
                artifacts=artifacts,
                repair_attempts=3,
            )

        final_mp4 = output_dir / "final.mp4"
        final_mp4.write_bytes(b"mp4")
        artifacts["final_mp4"] = str(final_mp4)
        if not self.qa_ready:
            qa_payload = {"has_audio": False, "width": 1920, "height": 1080, "duration_seconds": 4}
        elif not self.upload_quality_ready:
            qa_payload = {
                "ok": False,
                "youtube_shorts_ready": True,
                "quality_score": 91,
                "warnings": ["Render exceeded the 420s watchdog but a valid output was recovered."],
            }
        else:
            qa_payload = {"ok": True, "youtube_shorts_ready": True, "quality_score": 91}
        (output_dir / "render_qa.json").write_text(json.dumps(qa_payload), encoding="utf-8")
        (output_dir / "visual_storyboard.json").write_text(
            json.dumps({"engine": "test", "scenes": ["Hook", "Body", "Body", "CTA"], "frames": []}),
            encoding="utf-8",
        )
        (output_dir / "visual_quality_report.json").write_text(
            json.dumps({"passed": self.upload_quality_ready, "overall_score": 8.0 if self.upload_quality_ready else 4.0, "issues": [] if self.upload_quality_ready else ["visual issue"]}),
            encoding="utf-8",
        )
        (output_dir / "youtube_upload_package.json").write_text(
            json.dumps(
                {
                    "status": "manual_upload_ready" if self.upload_quality_ready else "needs_review_before_upload",
                    "checks": {
                        "script_quality_score": 8.0,
                        "script_quality_passed": True,
                        "render_ok": self.upload_quality_ready,
                        "youtube_shorts_ready": self.qa_ready,
                        "visual_quality_passed": self.upload_quality_ready,
                        "visual_quality_score": 8.0 if self.upload_quality_ready else 4.0,
                    },
                }
            ),
            encoding="utf-8",
        )
        return ShortAgentResult(
            status=self.status,
            topic=topic,
            output_dir=output_dir,
            final_mp4=final_mp4,
            quality_report={"overall_score": 8.0, "passed": True},
            artifacts=artifacts,
            repair_attempts=0,
        )


class FakeReadinessService:
    def __init__(self, status: str) -> None:
        self.status = status

    def check(self, *, run_probe: bool = False):
        return SimpleNamespace(status=self.status)


def _write_pre_render_contract(output_dir: Path) -> None:
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


class DailyChannelRunnerTest(unittest.TestCase):
    def test_runner_creates_approval_pending_items_for_ready_youtube_oauth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            service = ChannelOpsService(settings)
            register_channel(service, daily_target_count=2)
            service.enqueue_topic("local-ai-pl", "Pierwszy temat")

            result = DailyChannelRunner(
                settings,
                channel_ops=service,
                short_agent_factory=lambda short_settings: FakeShortAgent(short_settings),
                youtube_readiness_factory=lambda _: FakeReadinessService("configured"),
            ).run(max_per_channel=2)

            self.assertEqual(result.processed_items, 2)
            self.assertEqual([item.status for item in result.results], ["approval_pending", "approval_pending"])
            self.assertEqual(len(result.ready_for_review), 2)
            self.assertTrue(Path(result.daily_log_path).exists())
            self.assertIsNotNone(result.daily_review_path)
            self.assertTrue(Path(str(result.daily_review_path)).exists())
            self.assertIsNotNone(result.review_queue_path)
            self.assertIsNotNone(result.review_queue_markdown_path)
            self.assertTrue(Path(str(result.review_queue_path)).exists())
            self.assertTrue(Path(str(result.review_queue_markdown_path)).exists())
            self.assertIn("Daily Channel Ops Review", Path(str(result.daily_review_path)).read_text(encoding="utf-8"))
            self.assertIn("Channel Review Queue", Path(str(result.review_queue_markdown_path)).read_text(encoding="utf-8"))
            queue = service.list_queue("local-ai-pl")
            self.assertTrue(all(ContentStatus(item.status) == ContentStatus.APPROVAL_PENDING for item in queue))
            self.assertTrue(all(item.upload_approval_id for item in queue))
            self.assertTrue(all("channel_ops\\channels\\local-ai-pl\\renders" in str(item.final_mp4) for item in queue))
            for item in queue:
                review = (
                    settings.data_dir
                    / "channel_ops"
                    / "channels"
                    / "local-ai-pl"
                    / "items"
                    / item.item_id
                    / "review.md"
                )
                self.assertTrue(review.exists())
                self.assertIn("Manual Review Checklist", review.read_text(encoding="utf-8"))

    def test_runner_ends_manual_upload_ready_when_youtube_oauth_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            service = ChannelOpsService(settings)
            register_channel(service, daily_target_count=1)

            result = DailyChannelRunner(
                settings,
                channel_ops=service,
                short_agent_factory=lambda short_settings: FakeShortAgent(short_settings),
                youtube_readiness_factory=lambda _: FakeReadinessService("missing_client_secret"),
            ).run()

            self.assertEqual(result.results[0].status, "manual_upload_ready")
            self.assertEqual(result.results[0].youtube_readiness_status, "missing_client_secret")
            self.assertEqual(ContentStatus(service.list_queue("local-ai-pl")[0].status), ContentStatus.MANUAL_UPLOAD_READY)

    def test_runner_marks_qa_failed_when_short_or_render_quality_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            service = ChannelOpsService(settings)
            register_channel(service, daily_target_count=1)

            result = DailyChannelRunner(
                settings,
                channel_ops=service,
                short_agent_factory=lambda short_settings: FakeShortAgent(short_settings, qa_ready=False),
                youtube_readiness_factory=lambda _: FakeReadinessService("configured"),
            ).run()

            self.assertEqual(result.results[0].status, "qa_failed")
            self.assertEqual(result.results[0].error, "render_qa_failed")
            self.assertEqual(ContentStatus(service.list_queue("local-ai-pl")[0].status), ContentStatus.QA_FAILED)

    def test_runner_blocks_manual_upload_ready_when_upload_quality_gate_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            service = ChannelOpsService(settings)
            register_channel(service, daily_target_count=1)

            result = DailyChannelRunner(
                settings,
                channel_ops=service,
                short_agent_factory=lambda short_settings: FakeShortAgent(
                    short_settings,
                    upload_quality_ready=False,
                ),
                youtube_readiness_factory=lambda _: FakeReadinessService("missing_client_secret"),
            ).run()

            self.assertEqual(result.results[0].status, "qa_failed")
            self.assertIn("upload_quality_gate_failed", result.results[0].error or "")
            self.assertIn("render has warnings", result.results[0].error or "")
            self.assertEqual(ContentStatus(service.list_queue("local-ai-pl")[0].status), ContentStatus.QA_FAILED)
            self.assertEqual(result.ready_for_review, [])

    def test_runner_blocks_manual_upload_ready_when_pre_render_contract_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            service = ChannelOpsService(settings)
            register_channel(service, daily_target_count=1)

            result = DailyChannelRunner(
                settings,
                channel_ops=service,
                short_agent_factory=lambda short_settings: FakeShortAgent(
                    short_settings,
                    write_contract=False,
                ),
                youtube_readiness_factory=lambda _: FakeReadinessService("missing_client_secret"),
            ).run()

            self.assertEqual(result.results[0].status, "qa_failed")
            self.assertIn("pre-render contract", result.results[0].error or "")
            self.assertEqual(ContentStatus(service.list_queue("local-ai-pl")[0].status), ContentStatus.QA_FAILED)
            self.assertEqual(result.ready_for_review, [])

    def test_runner_respects_max_per_channel_and_disabled_channels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            service = ChannelOpsService(settings)
            register_channel(service, daily_target_count=3)
            service.register_channel(
                channel_id="disabled-pl",
                display_name="Disabled",
                niche="test",
                language="pl",
                audience="test",
                daily_target_count=3,
                enabled=False,
            )

            result = DailyChannelRunner(
                settings,
                channel_ops=service,
                short_agent_factory=lambda short_settings: FakeShortAgent(short_settings),
                youtube_readiness_factory=lambda _: FakeReadinessService("configured"),
            ).run(max_per_channel=1)

            self.assertEqual(result.processed_channels, 1)
            self.assertEqual(result.processed_items, 1)
            self.assertEqual(len(service.list_queue("disabled-pl")), 0)

    def test_runner_can_filter_to_one_channel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            service = ChannelOpsService(settings)
            register_channel(service, daily_target_count=1)
            service.register_channel(
                channel_id="finance-pl",
                display_name="Finance PL",
                niche="finanse",
                language="pl",
                audience="ambitni widzowie",
                daily_target_count=1,
                enabled=True,
            )

            result = DailyChannelRunner(
                settings,
                channel_ops=service,
                short_agent_factory=lambda short_settings: FakeShortAgent(short_settings),
                youtube_readiness_factory=lambda _: FakeReadinessService("configured"),
            ).run(max_per_channel=1, channel_ids=("finance-pl",))

            self.assertEqual(result.processed_channels, 1)
            self.assertEqual(result.processed_items, 1)
            self.assertEqual(result.results[0].channel_id, "finance-pl")
            self.assertEqual(len(service.list_queue("local-ai-pl")), 0)
            self.assertEqual(len(service.list_queue("finance-pl")), 1)

    def test_runner_blocks_duplicate_topics_in_channel_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            service = ChannelOpsService(settings)
            register_channel(service, daily_target_count=1)
            original = service.enqueue_topic("local-ai-pl", "Ten sam temat")
            output_dir = settings.data_dir / "rendered"
            output_dir.mkdir(parents=True)
            (output_dir / "render_qa.json").write_text(json.dumps({"youtube_shorts_ready": True}), encoding="utf-8")
            service.mark_render_ready(original.item_id, output_dir)
            duplicate = service.enqueue_topic("local-ai-pl", "Ten sam temat")

            result = DailyChannelRunner(
                settings,
                channel_ops=service,
                short_agent_factory=lambda short_settings: FakeShortAgent(short_settings),
                youtube_readiness_factory=lambda _: FakeReadinessService("configured"),
            ).run(max_per_channel=1)

            self.assertEqual(result.results[0].item_id, duplicate.item_id)
            self.assertEqual(result.results[0].status, "blocked")
            self.assertEqual(result.results[0].error, "duplicate_topic")
            manifest = settings.data_dir / "channel_ops" / "channels" / "local-ai-pl" / "items" / duplicate.item_id / "manifest.json"
            self.assertTrue(manifest.exists())
            review = manifest.with_name("review.md")
            self.assertTrue(review.exists())
            self.assertIn("duplicate_topic", review.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
