from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from atnos.core.config import Settings
from channel_ops import AnalyticsFeedbackService, ChannelOpsService
from channel_ops.content_strategy import extract_format_signature, topic_risk_level


def make_settings(root: Path, *, obsidian: bool = False) -> Settings:
    return Settings(
        app_env="test",
        app_root=root,
        data_dir=root / "data",
        obsidian_vault_path=(root / "vault") if obsidian else None,
        atnos_write_obsidian_logs=obsidian,
        atnos_obsidian_shorts_dir="Atnos/Shorts",
    )


def register_channel(service: ChannelOpsService):
    return service.register_channel(
        channel_id="local-ai-pl",
        display_name="Lokalne AI PL",
        niche="lokalne AI i automatyzacja",
        language="pl",
        audience="tworcy solo",
        daily_target_count=1,
    )


class AnalyticsFeedbackTest(unittest.TestCase):
    def test_score_video_performance_rewards_retention_and_real_engagement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            feedback = AnalyticsFeedbackService(make_settings(Path(tmp)))

            strong = feedback.score_video_performance(
                {
                    "views": 1200,
                    "averageViewDuration": 34,
                    "estimatedMinutesWatched": 700,
                    "likes": 80,
                    "shares": 22,
                    "subscribersGained": 18,
                    "subscribersLost": 1,
                }
            )
            weak = feedback.score_video_performance(
                {
                    "views": 1200,
                    "averageViewDuration": 5,
                    "estimatedMinutesWatched": 110,
                    "likes": 2,
                    "shares": 0,
                    "subscribersGained": 0,
                    "subscribersLost": 5,
                }
            )

            self.assertGreater(strong.overall_score, 8)
            self.assertLess(weak.overall_score, strong.overall_score)
            self.assertEqual(weak.quality_warning, "subscriber_loss")

    def test_ingest_metrics_persists_jsonl_and_updates_content_item_analytics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            channel_ops = ChannelOpsService(settings)
            register_channel(channel_ops)
            item = channel_ops.enqueue_topic("local-ai-pl", "Dlaczego lokalne AI ma sens")
            uploaded = channel_ops.mark_uploaded_private(item.item_id, "video-1")
            feedback = AnalyticsFeedbackService(settings, channel_ops=channel_ops)

            record = feedback.ingest_video_metrics(
                "local-ai-pl",
                uploaded.youtube_video_id or "video-1",
                {
                    "views": 500,
                    "averageViewDuration": 20,
                    "estimatedMinutesWatched": 160,
                    "likes": 25,
                    "shares": 6,
                    "subscribersGained": 4,
                    "subscribersLost": 0,
                },
            )

            metrics_path = settings.data_dir / "channel_ops" / "analytics" / "local-ai-pl" / "video_metrics.jsonl"
            self.assertTrue(metrics_path.exists())
            self.assertEqual(record["topic"], "Dlaczego lokalne AI ma sens")
            updated = channel_ops.calendar.find_item(item.item_id)
            self.assertEqual(updated.analytics_summary["video_id"], "video-1")
            self.assertIn("score", updated.analytics_summary)

    def test_identifies_winning_weak_repeated_and_risky_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            channel_ops = ChannelOpsService(settings)
            register_channel(channel_ops)
            feedback = AnalyticsFeedbackService(settings, channel_ops=channel_ops)
            samples = [
                ("v1", "Dlaczego lokalne AI oszczedza czas", 900, 35, 70, 20, 8, 2, 0),
                ("v2", "Dlaczego Ollama ma sens na laptopie", 850, 32, 60, 18, 7, 2, 0),
                ("v3", "Jak zaczac z lokalnym AI bez chaosu", 720, 28, 48, 22, 10, 2, 0),
                ("v4", "AI sekret ktorego nikt ci nie powie", 500, 6, 40, 2, 0, 0, 8),
                ("v5", "Lista 5 narzedzi AI", 110, 7, 12, 1, 0, 0, 0),
                ("v6", "Lista 3 aplikacji AI", 90, 6, 10, 1, 0, 0, 0),
                ("v7", "Lista top AI", 80, 5, 8, 1, 0, 0, 0),
                ("v8", "Lista prostych automatyzacji", 75, 5, 7, 0, 0, 0, 0),
            ]
            for video_id, topic, views, avd, minutes, likes, shares, gained, lost in samples:
                feedback.ingest_video_metrics(
                    "local-ai-pl",
                    video_id,
                    {
                        "topic": topic,
                        "views": views,
                        "averageViewDuration": avd,
                        "estimatedMinutesWatched": minutes,
                        "likes": likes,
                        "shares": shares,
                        "subscribersGained": gained,
                        "subscribersLost": lost,
                    },
                )

            winners = feedback.identify_winning_patterns("local-ai-pl")
            weak = feedback.identify_weak_patterns("local-ai-pl")
            report = feedback.identify_weak_patterns_report("local-ai-pl")

            self.assertIn("why_explainer", winners)
            self.assertIn("listicle", weak)
            self.assertIn("listicle", report.repeated_formats)
            self.assertIn("AI sekret ktorego nikt ci nie powie", report.risky_topics)

    def test_recommend_next_topics_avoids_clickbait_and_uses_winning_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = make_settings(Path(tmp))
            channel_ops = ChannelOpsService(settings)
            register_channel(channel_ops)
            feedback = AnalyticsFeedbackService(settings, channel_ops=channel_ops)
            feedback.ingest_video_metrics(
                "local-ai-pl",
                "video-why",
                {
                    "topic": "Dlaczego lokalne AI oszczedza czas",
                    "views": 1400,
                    "averageViewDuration": 36,
                    "estimatedMinutesWatched": 820,
                    "likes": 91,
                    "shares": 20,
                    "subscribersGained": 15,
                    "subscribersLost": 0,
                },
            )

            topics = feedback.recommend_next_topics("local-ai-pl")

            self.assertTrue(topics)
            self.assertTrue(any("Dlaczego" in item.topic for item in topics))
            self.assertTrue(all(item.risk_level in {"low", "medium", "high"} for item in topics))
            self.assertFalse(any("nie uwierzysz" in item.topic.lower() for item in topics))

    def test_update_channel_style_guide_writes_local_and_obsidian_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = make_settings(root, obsidian=True)
            channel_ops = ChannelOpsService(settings)
            register_channel(channel_ops)
            feedback = AnalyticsFeedbackService(settings, channel_ops=channel_ops)
            feedback.ingest_video_metrics(
                "local-ai-pl",
                "video-1",
                {
                    "topic": "Jak zaczac z lokalnym AI bez chaosu",
                    "views": 600,
                    "averageViewDuration": 30,
                    "estimatedMinutesWatched": 330,
                    "likes": 45,
                    "shares": 12,
                    "subscribersGained": 6,
                    "subscribersLost": 0,
                },
            )

            result = feedback.update_channel_style_guide("local-ai-pl")

            style_path = Path(result["style_guide_path"])
            self.assertTrue(style_path.exists())
            self.assertIn("Channel Style Guide", style_path.read_text(encoding="utf-8"))
            self.assertTrue((settings.data_dir / "channel_ops" / "analytics" / "local-ai-pl" / "learning_report.md").exists())
            obsidian_notes = list((root / "vault" / "Atnos" / "Shorts").glob("channel-learning-*.md"))
            self.assertEqual(len(obsidian_notes), 1)

    def test_strategy_helpers_detect_formats_and_risk(self) -> None:
        self.assertEqual(extract_format_signature("Jak zaczac z AI"), "how_to")
        self.assertEqual(extract_format_signature("Dlaczego lokalne AI ma sens"), "why_explainer")
        self.assertEqual(topic_risk_level("Gwarantowany zysk bez ryzyka dzieki AI"), "high")


if __name__ == "__main__":
    unittest.main()
