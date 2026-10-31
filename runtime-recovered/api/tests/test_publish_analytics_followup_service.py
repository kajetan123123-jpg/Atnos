from __future__ import annotations

from pathlib import Path
import sys
import unittest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
API_ROOT = TESTS_DIR.parent
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.services.publish_analytics_followup_service import PublishAnalyticsFollowupService


class PublishAnalyticsFollowupServiceTest(unittest.TestCase):
    def test_build_strategy_snapshot_prefers_stronger_controls_for_weak_retention(self) -> None:
        snapshot = PublishAnalyticsFollowupService._build_strategy_snapshot(
            analytics_summary={
                "status": "ready",
                "average_view_percentage": 58.0,
                "views": 420,
            },
            review_window_hours=48,
        )

        self.assertEqual(snapshot["hook_strategy"], "sharper_hook")
        self.assertEqual(snapshot["pacing_strategy"], "compress_body")
        self.assertEqual(snapshot["cutaway_strategy"], "stronger_cutaway_contrast")
        self.assertEqual(snapshot["title_strategy"], "hook_first_title")

    def test_build_strategy_snapshot_waits_when_metrics_are_pending(self) -> None:
        snapshot = PublishAnalyticsFollowupService._build_strategy_snapshot(
            analytics_summary={"status": "pending"},
            review_window_hours=24,
        )

        self.assertEqual(snapshot["hook_strategy"], "wait_for_more_data")
        self.assertIn("24h", snapshot["next_batch_brief"])


if __name__ == "__main__":
    unittest.main()
