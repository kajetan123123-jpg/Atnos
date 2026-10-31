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

from atnos.services.synthetic_variant_refinement_service import SyntheticVariantRefinementService


class SyntheticVariantRefinementServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = SyntheticVariantRefinementService()

    def test_build_plan_adds_duration_and_cutaway_directives(self) -> None:
        best_variant = {
            "style": {"key": "editorial_premium", "label": "Editorial Premium"},
            "reel_type": {"key": "micro_story", "label": "Micro Story"},
            "script_bundle": {
                "hook": "Bardzo dlugi hook ktory niepotrzebnie rozciaga pierwsze sekundy tej rolki premium.",
                "body": ["A", "B", "C"],
            },
            "artifacts": {
                "cutaways": {"assets": []},
                "motion_reel": {"qa": {"duration_sec": 27.5}},
            },
        }
        selection = {
            "best_variant_index": 2,
            "ranked_variants": [
                {
                    "variant_index": 2,
                    "score": 81.4,
                    "confidence": "high",
                    "risks": ["Brak cutawayow - rolka za bardzo opiera sie na jednym kadrze."],
                },
            ],
        }

        plan = self.service.build_plan(
            project_name="ATNOS Synthetic Creator",
            best_variant=best_variant,
            selection=selection,
        )

        self.assertEqual(plan.source_variant_index, 2)
        self.assertEqual(plan.style_key, "editorial_premium")
        self.assertEqual(plan.reel_key, "micro_story")
        self.assertTrue(any("pacing" in item.lower() for item in plan.directives))
        self.assertTrue(any("cutaway" in item.lower() for item in plan.directives))
        self.assertIn("Dopracuj zwycieski synthetic creator reel", plan.goal)
        self.assertIsNotNone(plan.hard_controls)
        assert plan.hard_controls is not None
        self.assertEqual(plan.hard_controls.max_body_lines, 2)
        self.assertTrue(plan.hard_controls.drop_outro)
        self.assertLess(plan.hard_controls.target_duration_cap_sec or 999.0, 24.0)

    def test_hard_controls_escalate_tightens_limits(self) -> None:
        plan = self.service.build_plan(
            project_name="ATNOS Synthetic Creator",
            best_variant={
                "style": {"key": "editorial_premium", "label": "Editorial Premium"},
                "reel_type": {"key": "talking_hook", "label": "Talking Hook"},
                "script_bundle": {"hook": "To jest celowo za dlugi hook dla pierwszego passu.", "body": ["A", "B"]},
                "artifacts": {"cutaways": {"assets": [{}, {}]}, "motion_reel": {"qa": {"duration_sec": 21.0}}},
            },
            selection={"best_variant_index": 0, "ranked_variants": [{"variant_index": 0, "score": 80.0, "confidence": "high"}]},
        )

        assert plan.hard_controls is not None
        escalated = plan.hard_controls.escalate()

        self.assertLess(escalated.target_duration_cap_sec or 999.0, plan.hard_controls.target_duration_cap_sec or 0.0)
        self.assertLessEqual(escalated.max_hook_words or 999, plan.hard_controls.max_hook_words or 0)
        self.assertLessEqual(escalated.max_body_lines or 999, plan.hard_controls.max_body_lines or 0)
        self.assertTrue(escalated.drop_outro)

    def test_build_plan_uses_historical_feedback_to_add_directives(self) -> None:
        plan = self.service.build_plan(
            project_name="ATNOS Synthetic Creator",
            best_variant={
                "style": {"key": "cozy_girlfriend", "label": "Cozy Girlfriend"},
                "reel_type": {"key": "talking_hook", "label": "Talking Hook"},
                "script_bundle": {"hook": "To jest celowo za dlugi hook dla pierwszego passu.", "body": ["A", "B"]},
                "artifacts": {"cutaways": {"assets": [{}]}, "motion_reel": {"qa": {"duration_sec": 19.8}}},
            },
            selection={"best_variant_index": 0, "ranked_variants": [{"variant_index": 0, "score": 74.0, "confidence": "medium"}]},
            analytics_learning_profile={
                "sample_count": 4,
                "baseline_average_view_percentage": 70.0,
                "baseline_views": 900.0,
                "style_feedback": {"cozy_girlfriend": -3.1},
                "style_sample_counts": {"cozy_girlfriend": 3},
                "reel_feedback": {"talking_hook": -1.3},
                "reel_sample_counts": {"talking_hook": 4},
                "strongest_style_key": "editorial_premium",
                "strongest_reel_key": "micro_story",
                "insights": [],
            },
        )

        self.assertIsNotNone(plan.historical_feedback_score)
        self.assertLess(plan.historical_feedback_score or 0.0, 0.0)
        self.assertTrue(any("historycznie" in item.lower() for item in plan.directives))
        self.assertTrue(any("slabsze retention" in item.lower() for item in plan.directives))


if __name__ == "__main__":
    unittest.main()
