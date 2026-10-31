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

from atnos.services.synthetic_analytics_learning_service import SyntheticAnalyticsCalibration
from atnos.services.synthetic_variant_planning_service import SyntheticVariantPlanningService


class SyntheticVariantPlanningServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = SyntheticVariantPlanningService()
        self.styles = [
            {"key": "editorial_premium", "label": "Editorial Premium"},
            {"key": "cozy_girlfriend", "label": "Cozy Girlfriend"},
        ]
        self.reels = [
            {"key": "talking_hook", "label": "Talking Hook"},
            {"key": "micro_story", "label": "Micro Story"},
        ]

    def test_build_plan_preserves_input_order_without_history(self) -> None:
        plan = self.service.build_plan(
            project_name="ATNOS Synthetic Creator",
            goal=None,
            styles=self.styles,
            reels=self.reels,
            max_items=3,
        )

        self.assertEqual(plan.sample_count, 0)
        self.assertEqual(
            [(item.style_key, item.reel_key) for item in plan.recommendations],
            [
                ("editorial_premium", "talking_hook"),
                ("editorial_premium", "micro_story"),
                ("cozy_girlfriend", "talking_hook"),
            ],
        )
        self.assertIn("domyslny porzadek", plan.strategy_summary[0].lower())
        self.assertTrue(all("Validate synthetic creator preview variant" in item.goal for item in plan.recommendations))
        self.assertTrue(all(item.initial_hard_controls is None for item in plan.recommendations))

    def test_build_plan_prioritizes_historical_winner_and_preserves_diversity(self) -> None:
        calibration = SyntheticAnalyticsCalibration(
            sample_count=5,
            baseline_average_view_percentage=73.0,
            baseline_views=950.0,
            style_feedback={"editorial_premium": -1.2, "cozy_girlfriend": 3.4},
            style_sample_counts={"editorial_premium": 2, "cozy_girlfriend": 3},
            reel_feedback={"talking_hook": -0.8, "micro_story": 2.6},
            reel_sample_counts={"talking_hook": 2, "micro_story": 3},
            strongest_style_key="cozy_girlfriend",
            strongest_reel_key="micro_story",
            hook_strategy_counts={"faster_hook": 3},
            pacing_strategy_counts={"tighter_intro": 2},
            cutaway_strategy_counts={"add_midbeat_cutaway": 2},
            title_strategy_counts={"hook_first_title": 2},
            strongest_hook_strategy="faster_hook",
            strongest_pacing_strategy="tighter_intro",
            strongest_cutaway_strategy="add_midbeat_cutaway",
            strongest_title_strategy="hook_first_title",
            insights=("history",),
        )

        plan = self.service.build_plan(
            project_name="ATNOS Synthetic Creator",
            goal="Prepare winner-biased batch.",
            styles=self.styles,
            reels=self.reels,
            max_items=3,
            calibration=calibration,
        )

        self.assertEqual(plan.sample_count, 5)
        self.assertEqual(plan.recommendations[0].style_key, "cozy_girlfriend")
        self.assertEqual(plan.recommendations[0].reel_key, "micro_story")
        self.assertEqual(plan.recommendations[0].candidate_kind, "history_winner")
        self.assertGreater(plan.recommendations[0].priority_score, 0.0)
        self.assertIn("lepsze retention", " ".join(plan.recommendations[0].rationale).lower())
        self.assertIn("dowozil lepsze retention", plan.recommendations[0].goal.lower())
        self.assertIn("wejdz szybciej w hook", plan.recommendations[0].goal.lower())
        self.assertTrue(any("hook_first_title" in item for item in plan.strategy_summary))
        self.assertIsNotNone(plan.recommendations[0].initial_hard_controls)
        assert plan.recommendations[0].initial_hard_controls is not None
        self.assertLessEqual(plan.recommendations[0].initial_hard_controls.max_body_lines or 99, 2)
        self.assertLessEqual(plan.recommendations[0].initial_hard_controls.max_hook_words or 99, 8)
        self.assertGreaterEqual(len({item.style_key for item in plan.recommendations}), 2)

    def test_build_plan_uses_project_strategy_brief_when_goal_is_missing(self) -> None:
        plan = self.service.build_plan(
            project_name="ATNOS Synthetic Creator",
            goal=None,
            project_strategy_profile={
                "next_generation_brief": (
                    "Priorytetem kolejnego batcha jest styl cozy_girlfriend i typ rolki micro_story. "
                    "Wejdz szybciej w hook i nie rozgrzewaj intro."
                )
            },
            styles=self.styles,
            reels=self.reels,
            max_items=1,
        )

        self.assertIn("Priorytetem kolejnego batcha", plan.recommendations[0].goal)
        self.assertIn("Use variant Editorial Premium / Talking Hook", plan.recommendations[0].goal)


if __name__ == "__main__":
    unittest.main()
