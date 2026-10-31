from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from atnos.core.config import Settings
from atnos.services.operational_learning_service import LearningSource, OperationalLearningService


class OperationalLearningServiceTest(unittest.TestCase):
    def test_official_actionable_source_is_usable(self) -> None:
        service = OperationalLearningService(self._settings())
        review = service.evaluate_source(
            LearningSource(
                title="OpenAI evaluation best practices",
                source_type="official_docs",
                url="https://developers.openai.com/api/docs/guides/evaluation-best-practices",
                published_at="2026",
                claims=("Use task-specific evals.", "Log everything."),
                practical_steps=("Collect real failures as eval cases.", "Run continuous evaluation."),
                evidence=("official docs", "production eval workflow"),
            )
        )

        self.assertEqual(review.verdict, "use")
        self.assertGreaterEqual(review.overall_score, 7)
        self.assertIn("official_or_primary_source", review.reasons)

    def test_hype_source_without_steps_is_rejected_or_reviewed(self) -> None:
        service = OperationalLearningService(self._settings())
        review = service.evaluate_source(
            LearningSource(
                title="Viral secret hack for instant success",
                source_type="market_example",
                claims=("This guarantees viral growth and passive income.",),
                practical_steps=(),
                evidence=(),
            )
        )

        self.assertIn(review.verdict, {"review", "reject"})
        self.assertGreaterEqual(review.hype_penalty, 3)
        self.assertTrue(any("hype_markers" in reason for reason in review.reasons))

    def test_research_to_improvement_persists_report_and_log(self) -> None:
        settings = self._settings()
        service = OperationalLearningService(settings)
        run = service.run_research_to_improvement(
            topic="Shorts hook quality",
            area="shorts_content",
            sources=(
                LearningSource(
                    title="TikTok creative best practices",
                    source_type="official_docs",
                    url="https://ads.tiktok.com/help/article/creative-best-practices",
                    published_at="2025",
                    claims=("Use hook, captions, safe zones and CTA.",),
                    practical_steps=("Generate hook variants before render.", "Check caption readability."),
                    evidence=("TikTok Business Help Center",),
                ),
            ),
        )

        self.assertEqual(run.area, "shorts_content")
        self.assertTrue(run.json_path)
        self.assertTrue(run.report_path)
        self.assertTrue(Path(str(run.json_path)).exists())
        self.assertTrue(Path(str(run.report_path)).exists())
        self.assertTrue((settings.data_dir / "logs" / "operational_learning.jsonl").exists())
        self.assertTrue(any(item.title.startswith("Add hook variants") for item in run.recommendations))

    def test_baseline_plan_covers_three_required_areas(self) -> None:
        service = OperationalLearningService(self._settings())
        runs = service.build_baseline_improvement_plan(persist=False)

        self.assertEqual({run.area for run in runs}, {"response_quality", "shorts_content", "agent_workflow"})
        self.assertTrue(all(run.practical_findings for run in runs))
        self.assertTrue(all(run.recommendations for run in runs))

    def _settings(self) -> Settings:
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        return Settings(app_root=root, data_dir=root / "data")


if __name__ == "__main__":
    unittest.main()
