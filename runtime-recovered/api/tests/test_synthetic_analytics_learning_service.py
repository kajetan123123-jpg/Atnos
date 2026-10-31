from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
API_ROOT = TESTS_DIR.parent
SRC_ROOT = API_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.services.synthetic_analytics_learning_service import SyntheticAnalyticsLearningService


class SyntheticAnalyticsLearningServiceTest(unittest.TestCase):
    def test_build_calibration_extracts_best_style_and_reel_from_ready_history(self) -> None:
        service = SyntheticAnalyticsLearningService(Mock())
        service.execution_runs.list_runs_for_project = Mock(
            return_value=[
                SimpleNamespace(
                    output_json={
                        "analytics_summary": {
                            "status": "ready",
                            "views": 1800,
                            "average_view_percentage": 88.0,
                        },
                        "analytics_strategy_snapshot": {
                            "hook_strategy": "keep_hook_profile",
                            "pacing_strategy": "maintain_pacing",
                            "cutaway_strategy": "maintain_cutaways",
                            "title_strategy": "keep_title_direction",
                        },
                        "refined_best_variant": {
                            "style": {"key": "editorial_premium"},
                            "reel_type": {"key": "micro_story"},
                        },
                    }
                ),
                SimpleNamespace(
                    output_json={
                        "analytics_summary": {
                            "status": "ready",
                            "views": 450,
                            "average_view_percentage": 59.0,
                        },
                        "analytics_strategy_snapshot": {
                            "hook_strategy": "sharper_hook",
                            "pacing_strategy": "compress_body",
                            "cutaway_strategy": "stronger_cutaway_contrast",
                            "title_strategy": "hook_first_title",
                        },
                        "best_variant": {
                            "style": {"key": "cozy_girlfriend"},
                            "reel_type": {"key": "talking_hook"},
                        },
                    }
                ),
                SimpleNamespace(
                    output_json={
                        "analytics_summary": {
                            "status": "pending",
                        },
                        "best_variant": {
                            "style": {"key": "late_night_egirl"},
                            "reel_type": {"key": "cta_teaser"},
                        },
                    }
                ),
            ]
        )

        calibration = service.build_calibration(project_id=199)

        self.assertEqual(calibration.sample_count, 2)
        self.assertEqual(calibration.strongest_style_key, "editorial_premium")
        self.assertEqual(calibration.strongest_reel_key, "micro_story")
        self.assertGreater(calibration.style_feedback["editorial_premium"], 0)
        self.assertLess(calibration.style_feedback["cozy_girlfriend"], 0)
        self.assertEqual(calibration.reel_sample_counts["micro_story"], 1)
        self.assertEqual(calibration.strongest_hook_strategy, "keep_hook_profile")
        self.assertEqual(calibration.strongest_title_strategy, "keep_title_direction")


if __name__ == "__main__":
    unittest.main()
