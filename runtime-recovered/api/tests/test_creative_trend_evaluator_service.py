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

from atnos.services.creative_trend_evaluator_service import CreativeTrendEvaluatorService


class CreativeTrendEvaluatorServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = CreativeTrendEvaluatorService()

    def test_evaluate_rewards_keyword_and_reel_match(self) -> None:
        preview = {
            "goal": "late night story drop",
            "style": {"label": "Editorial Premium"},
            "reel_type": {"key": "micro_story", "label": "Micro Story"},
            "script_bundle": {
                "title": "Late night story",
                "hook": "Late night story viral drop tonight.",
                "body": ["Members get the full story.", "Public sees only the hook."],
            },
        }
        trend_snapshot = {
            "summary": {
                "top_keywords": ["late", "night", "story", "viral"],
                "top_hashtags": ["#latenight"],
                "recommended_reel_types": ["micro_story", "talking_hook"],
            }
        }

        result = self.service.evaluate(preview=preview, trend_snapshot=trend_snapshot)

        self.assertGreater(result["score"], 0.0)
        self.assertTrue(result["recommended_reel_match"])
        self.assertIn("story", result["matched_keywords"])
        self.assertGreaterEqual(len(result["strengths"]), 1)

    def test_evaluate_reports_missing_snapshot_as_risk(self) -> None:
        preview = {
            "goal": "generic teaser",
            "reel_type": {"key": "talking_hook", "label": "Talking Hook"},
            "script_bundle": {"hook": "Quick teaser.", "body": ["Line one."]},
        }

        result = self.service.evaluate(preview=preview, trend_snapshot=None)

        self.assertEqual(result["score"], 0.0)
        self.assertFalse(result["recommended_reel_match"])
        self.assertIn("Brak swiezego sygnalu trendowego do porownania.", result["risks"])


if __name__ == "__main__":
    unittest.main()
