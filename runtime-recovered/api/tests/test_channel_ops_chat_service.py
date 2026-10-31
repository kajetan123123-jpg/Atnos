from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.services.channel_ops_chat_service import ChannelOpsChatService
from atnos.agents.topic_hook import RawTopic, TopicHookAgent, TopicRequest
from channel_ops import (
    AnalyticsFeedbackService,
    ChannelOpsService,
    ContentStatus,
    DailyChannelItemResult,
    DailyChannelRunResult,
)
from test_support import build_test_settings, make_temp_dir


class OfflineTopicHookAgent(TopicHookAgent):
    def _llm_topic_suggestions(self, request: TopicRequest, *, target_count: int) -> list[RawTopic]:
        return []

    def _duckduckgo_topics(self, niche: str, language: str) -> list[RawTopic]:
        return []

    def _youtube_topics(self, niche: str, language: str) -> list[RawTopic]:
        return []


class ChannelOpsChatServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.settings = build_test_settings(self.root)
        self.ops = ChannelOpsService(self.settings)
        self.ops.register_channel(
            channel_id="lokalne-ai",
            display_name="Lokalne AI",
            niche="lokalne AI i automatyzacja",
            language="pl",
            audience="tworcy solo",
            daily_target_count=3,
        )
        self.topic_hook = OfflineTopicHookAgent(self.settings)

    def test_prepare_short_command_runs_daily_channel_runner(self) -> None:
        output_dir = self.settings.data_dir / "channel_ops" / "channels" / "lokalne-ai" / "renders" / "run-1"
        fake_result = DailyChannelRunResult(
            timestamp="2026-05-20T00:00:00+00:00",
            processed_channels=1,
            processed_items=1,
            ready_for_review=[],
            results=[
                DailyChannelItemResult(
                    channel_id="lokalne-ai",
                    item_id="item-1",
                    topic="lokalne AI: odcinek 1",
                    status="manual_upload_ready",
                    output_dir=str(output_dir),
                    final_mp4=str(output_dir / "final.mp4"),
                    quality_score=8.2,
                    youtube_readiness_status="missing_client_secret",
                )
            ],
            daily_log_path=str(self.settings.data_dir / "channel_ops" / "daily_runs" / "fake.json"),
        )

        with patch(
            "atnos.services.channel_ops_chat_service.DailyChannelRunner.run",
            return_value=fake_result,
        ) as run_mock:
            operation = ChannelOpsChatService(self.settings, channel_ops=self.ops).maybe_handle(
                "Przygotuj 1 short na kanal lokalne-ai"
            )

        self.assertTrue(operation.activated)
        self.assertEqual(operation.selected_provider_key, "channel-ops")
        self.assertEqual(operation.selected_model_key, "daily-channel-runner")
        self.assertIn("manual_upload_ready", operation.assistant_content or "")
        self.assertIn("final.mp4", operation.assistant_content or "")
        self.assertEqual(operation.metadata["channel_ops"]["status"], "manual_upload_ready")
        run_mock.assert_called_once_with(max_per_channel=1, run_youtube_probe=False)

    def test_approval_queue_lists_pending_and_manual_upload_items(self) -> None:
        item = self.ops.enqueue_topic("lokalne-ai", "Jak lokalne AI oszczedza czas")
        item = item.with_update(
            status=ContentStatus.MANUAL_UPLOAD_READY,
            final_mp4=str(self.settings.data_dir / "shorts" / "final.mp4"),
            quality_score=8.0,
        )
        self.ops.calendar.update_item(item)

        operation = ChannelOpsChatService(self.settings, channel_ops=self.ops).maybe_handle(
            "Pokaz kolejke upload approval"
        )

        self.assertTrue(operation.activated)
        self.assertEqual(operation.selected_model_key, "approval-queue")
        self.assertIn("Kolejka upload approval", operation.assistant_content or "")
        self.assertIn("final.mp4", operation.assistant_content or "")

    def test_channel_analytics_returns_learning_summary(self) -> None:
        analytics = AnalyticsFeedbackService(self.settings, channel_ops=self.ops)
        analytics.ingest_video_metrics(
            "lokalne-ai",
            "video-1",
            {
                "topic": "Dlaczego lokalne AI oszczedza czas",
                "views": 1500,
                "averageViewDuration": 34,
                "estimatedMinutesWatched": 900,
                "likes": 120,
                "shares": 30,
                "subscribersGained": 9,
                "subscribersLost": 0,
            },
        )

        operation = ChannelOpsChatService(
            self.settings,
            channel_ops=self.ops,
            analytics=analytics,
        ).maybe_handle("Co dziala najlepiej na kanale lokalne-ai?")

        self.assertTrue(operation.activated)
        self.assertEqual(operation.selected_model_key, "analytics-feedback")
        self.assertIn("Co dziala najlepiej", operation.assistant_content or "")
        self.assertIn("Nastepne tematy", operation.assistant_content or "")
        self.assertEqual(operation.metadata["channel_ops"]["status"], "analytics_ready")

    def test_topic_hook_generation_returns_artifact_metadata(self) -> None:
        operation = ChannelOpsChatService(
            self.settings,
            channel_ops=self.ops,
            topic_hook_agent=self.topic_hook,
        ).maybe_handle("Zaproponuj 5 tematow dla kanalu lokalne-ai")

        self.assertTrue(operation.activated)
        self.assertEqual(operation.selected_provider_key, "topic-hook-agent")
        self.assertEqual(operation.selected_model_key, "topic-hook-agent")
        self.assertIn("pending_approval", operation.assistant_content or "")
        self.assertIn("topic_hook", operation.metadata)
        package = operation.metadata["topic_hook"]["package"]
        self.assertGreaterEqual(len(package["topics"]), 5)
        self.assertTrue((self.settings.data_dir / "channels" / "lokalne-ai" / "topic_history.json").exists())

    def test_topic_hook_approval_writes_topic_queue(self) -> None:
        service = ChannelOpsChatService(
            self.settings,
            channel_ops=self.ops,
            topic_hook_agent=self.topic_hook,
        )
        service.maybe_handle("Zaproponuj 5 tematow dla kanalu lokalne-ai")

        operation = service.maybe_handle("Zatwierdz tematy 1, 3 z ostatniej paczki")

        self.assertTrue(operation.activated)
        self.assertEqual(operation.metadata["topic_hook"]["status"], "approved")
        queue_path = self.settings.data_dir / "channels" / "lokalne-ai" / "topic_queue.json"
        self.assertTrue(queue_path.exists())
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
        self.assertEqual(len(queue), 2)


if __name__ == "__main__":
    unittest.main()
