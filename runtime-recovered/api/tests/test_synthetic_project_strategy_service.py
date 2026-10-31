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
from atnos.services.synthetic_project_strategy_service import SyntheticProjectStrategyService


class SyntheticProjectStrategyServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = SyntheticProjectStrategyService()

    def test_build_profile_returns_neutral_defaults_without_history(self) -> None:
        profile = self.service.build_profile(
            project_name="ATNOS Synthetic Creator",
            calibration=SyntheticAnalyticsCalibration.empty(),
        )

        self.assertEqual(profile["derived_sample_count"], 0)
        self.assertEqual(profile["initial_hard_controls_by_reel"], {})
        self.assertIsNone(profile["hook_template"])
        self.assertIsNone(profile["next_generation_brief"])

    def test_build_profile_derives_project_templates_and_reel_defaults(self) -> None:
        calibration = SyntheticAnalyticsCalibration(
            sample_count=4,
            baseline_average_view_percentage=72.0,
            baseline_views=920.0,
            style_feedback={"cozy_girlfriend": 2.8},
            style_sample_counts={"cozy_girlfriend": 4},
            reel_feedback={"micro_story": 2.1, "talking_hook": -0.5},
            reel_sample_counts={"micro_story": 3, "talking_hook": 1},
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

        profile = self.service.build_profile(
            project_name="ATNOS Synthetic Creator",
            calibration=calibration,
        )

        self.assertEqual(profile["derived_sample_count"], 4)
        self.assertEqual(dict(profile["hook_template"])["template_key"], "direct_payoff")
        self.assertEqual(dict(profile["title_policy"])["template_key"], "hook_first")
        self.assertIn("Priorytetem kolejnego batcha", str(profile["next_generation_brief"]))
        self.assertIn("cozy_girlfriend", str(profile["next_generation_brief"]))
        talking_hook_controls = dict(dict(profile["initial_hard_controls_by_reel"])["talking_hook"])
        self.assertLessEqual(int(talking_hook_controls["max_hook_words"]), 7)
        self.assertIn("Najmocniejszy styl", " ".join(profile["summary"]))


if __name__ == "__main__":
    unittest.main()
