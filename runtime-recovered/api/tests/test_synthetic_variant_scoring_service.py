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
from atnos.services.synthetic_variant_scoring_service import SyntheticVariantScoringService


class SyntheticVariantScoringServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = SyntheticVariantScoringService()

    def test_rank_variants_prefers_full_reel_with_cutaways(self) -> None:
        weak = {
            "style": {"key": "editorial_premium"},
            "reel_type": {"key": "talking_hook"},
            "script_bundle": {"hook": "Hej.", "body": ["Jedno zdanie."]},
            "artifacts": {
                "cutaways": {"assets": [], "warnings": []},
                "motion_reel": {
                    "qa": {
                        "duration_sec": 8.5,
                        "width": 720,
                        "height": 1280,
                        "has_audio": True,
                        "warnings": ["Rendered short is unusually short."],
                    }
                },
            },
        }
        strong = {
            "style": {"key": "editorial_premium"},
            "reel_type": {"key": "micro_story"},
            "script_bundle": {
                "hook": "W kilka sekund widac, czy ten format wyglada premium, czy tylko drogo udaje premium.",
                "body": ["Pierwsza linia.", "Druga linia.", "Trzecia linia."],
            },
            "artifacts": {
                "cutaways": {
                    "assets": [{}, {}, {}],
                    "warnings": [],
                },
                "motion_reel": {
                    "qa": {
                        "duration_sec": 22.4,
                        "width": 1080,
                        "height": 1920,
                        "has_audio": True,
                        "warnings": [],
                    }
                },
            },
        }

        result = self.service.rank_variants([weak, strong])

        self.assertEqual(result["best_variant_index"], 1)
        self.assertGreater(result["best_score"], 80)
        self.assertEqual(result["best_confidence"], "high")
        self.assertEqual(result["ranked_variants"][0]["variant_index"], 1)

    def test_rank_variants_uses_historical_feedback_when_variants_are_similar(self) -> None:
        first = {
            "style": {"key": "cozy_girlfriend"},
            "reel_type": {"key": "talking_hook"},
            "script_bundle": {"hook": "To jest szybki hook pod retention.", "body": ["Jedna linia.", "Druga linia."]},
            "artifacts": {
                "cutaways": {"assets": [{}, {}], "warnings": []},
                "motion_reel": {
                    "qa": {
                        "duration_sec": 16.2,
                        "width": 1080,
                        "height": 1920,
                        "has_audio": True,
                        "warnings": [],
                    }
                },
            },
        }
        second = {
            "style": {"key": "editorial_premium"},
            "reel_type": {"key": "micro_story"},
            "script_bundle": {"hook": "To jest szybki hook pod retention.", "body": ["Jedna linia.", "Druga linia."]},
            "artifacts": {
                "cutaways": {"assets": [{}, {}], "warnings": []},
                "motion_reel": {
                    "qa": {
                        "duration_sec": 16.2,
                        "width": 1080,
                        "height": 1920,
                        "has_audio": True,
                        "warnings": [],
                    }
                },
            },
        }
        calibration = SyntheticAnalyticsCalibration(
            sample_count=4,
            baseline_average_view_percentage=72.0,
            baseline_views=900.0,
            style_feedback={"editorial_premium": 3.2, "cozy_girlfriend": -2.4},
            style_sample_counts={"editorial_premium": 2, "cozy_girlfriend": 2},
            reel_feedback={"micro_story": 2.5, "talking_hook": -1.1},
            reel_sample_counts={"micro_story": 3, "talking_hook": 1},
            strongest_style_key="editorial_premium",
            strongest_reel_key="micro_story",
            hook_strategy_counts={},
            pacing_strategy_counts={},
            cutaway_strategy_counts={},
            title_strategy_counts={},
            strongest_hook_strategy=None,
            strongest_pacing_strategy=None,
            strongest_cutaway_strategy=None,
            strongest_title_strategy=None,
            insights=("sample",),
        )

        result = self.service.rank_variants([first, second], calibration=calibration)

        self.assertEqual(result["best_variant_index"], 1)
        self.assertGreater(result["ranked_variants"][0]["breakdown"]["historical_feedback"], 0)
        self.assertLess(result["ranked_variants"][1]["breakdown"]["historical_feedback"], 0)

    def test_rank_variants_exposes_market_alignment_from_trend_snapshot(self) -> None:
        weak = {
            "goal": "generic content",
            "style": {"key": "editorial_premium", "label": "Editorial Premium"},
            "reel_type": {"key": "behind_the_scene", "label": "Behind the Scene"},
            "script_bundle": {
                "title": "Generic drop",
                "hook": "Zobacz nowy klip.",
                "body": ["Linia pierwsza.", "Linia druga."],
            },
            "artifacts": {
                "cutaways": {"assets": [{}, {}], "warnings": []},
                "motion_reel": {
                    "qa": {
                        "duration_sec": 18.0,
                        "width": 1080,
                        "height": 1920,
                        "has_audio": True,
                        "warnings": [],
                    }
                },
            },
        }
        strong = {
            "goal": "late night story hook",
            "style": {"key": "editorial_premium", "label": "Editorial Premium"},
            "reel_type": {"key": "micro_story", "label": "Micro Story"},
            "script_bundle": {
                "title": "Late night story",
                "hook": "Late night story viral hook tonight.",
                "body": ["Story line one.", "Story line two."],
            },
            "artifacts": {
                "cutaways": {"assets": [{}, {}], "warnings": []},
                "motion_reel": {
                    "qa": {
                        "duration_sec": 18.0,
                        "width": 1080,
                        "height": 1920,
                        "has_audio": True,
                        "warnings": [],
                    }
                },
            },
        }
        trend_snapshot = {
            "summary": {
                "top_keywords": ["late", "night", "story", "viral"],
                "top_hashtags": ["#latenight"],
                "recommended_reel_types": ["micro_story"],
            }
        }

        result = self.service.rank_variants([weak, strong], trend_snapshot=trend_snapshot)

        self.assertEqual(result["best_variant_index"], 1)
        self.assertGreater(result["ranked_variants"][0]["breakdown"]["market_alignment"], 0)
        self.assertEqual(result["ranked_variants"][1]["breakdown"]["market_alignment"], 0.0)


if __name__ == "__main__":
    unittest.main()
