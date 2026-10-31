from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from atnos.core.config import Settings
from content.format_recreation_loop import FormatRecreationLoopService
from content.format_recreation_repair import FormatRecreationRepairResult
from content.format_recreation_review import FormatRecreationReview, FormatSignalScore


def test_format_recreation_loop_approves_without_repair_when_initial_review_passes() -> None:
    with TemporaryDirectory(prefix="atnos-format-loop-pass-") as tmpdir:
        root = Path(tmpdir)
        source = root / "source"
        source.mkdir()
        (source / "final.mp4").write_bytes(b"fake-mp4")
        reference = root / "reference.json"
        reference.write_text("{}", encoding="utf-8")

        result = FormatRecreationLoopService(
            Settings(app_env="test", app_root=root, data_dir=root / "data"),
            review_service=FakeReviewService(decision="approve_mechanics", score=8.8),
            repair_service=FakeRepairService(),
        ).run(
            source_short_dir=source,
            reference_observation_json=reference,
            output_dir=root / "loop",
        )

        assert result.status == "approved_without_repair"
        assert result.repair_applied is False
        assert result.final_video_path == str(source / "final.mp4")
        assert (root / "loop" / "format_recreation_loop_result.json").exists()
        assert (root / "loop" / "format_recreation_loop_report.md").exists()


def test_format_recreation_loop_repairs_and_uses_followup_review_score() -> None:
    with TemporaryDirectory(prefix="atnos-format-loop-repair-") as tmpdir:
        root = Path(tmpdir)
        source = root / "source"
        source.mkdir()
        (source / "final.mp4").write_bytes(b"fake-mp4")
        reference = root / "reference.json"
        reference.write_text("{}", encoding="utf-8")

        result = FormatRecreationLoopService(
            Settings(app_env="test", app_root=root, data_dir=root / "data"),
            review_service=FakeReviewService(decision="repair", score=6.2),
            repair_service=FakeRepairService(followup_decision="approve_mechanics", followup_score=9.4),
        ).run(
            source_short_dir=source,
            reference_observation_json=reference,
            output_dir=root / "loop",
        )

        assert result.status == "repaired_approved"
        assert result.repair_applied is True
        assert result.final_decision == "approve_mechanics"
        assert result.final_score == 9.4
        assert result.repaired_mp4 == str(root / "loop" / "repair_pass" / "final.mp4")


def test_format_recreation_loop_reports_missing_candidate_video() -> None:
    with TemporaryDirectory(prefix="atnos-format-loop-missing-") as tmpdir:
        root = Path(tmpdir)
        source = root / "source"
        source.mkdir()
        reference = root / "reference.json"
        reference.write_text("{}", encoding="utf-8")

        result = FormatRecreationLoopService(
            Settings(app_env="test", app_root=root, data_dir=root / "data"),
            review_service=FakeReviewService(),
            repair_service=FakeRepairService(),
        ).run(
            source_short_dir=source,
            reference_observation_json=reference,
            output_dir=root / "loop",
        )

        assert result.status == "missing_candidate_video"
        assert result.initial_review_path is None
        assert "Missing candidate video" in result.notes[0]


class FakeReviewService:
    def __init__(self, *, decision: str = "approve_mechanics", score: float = 8.0) -> None:
        self.decision = decision
        self.score = score

    def review_from_paths(
        self,
        *,
        reference_observation_json,
        candidate_video_path,
        output_dir,
        sample_times_sec=(0.5, 2.0, 5.0, 10.0, 20.0, 35.0),
    ) -> FormatRecreationReview:
        review = FormatRecreationReview(
            review_id="fake-review",
            reviewed_at="2026-05-29T00:00:00+00:00",
            reference_source_ref="ref",
            reference_title="Reference",
            candidate_source_ref="candidate",
            candidate_video_path=str(candidate_video_path),
            output_dir=str(output_dir),
            signal_scores=(FormatSignalScore(name="mock", score=self.score, evidence="mock"),),
            overall_score=self.score,
            decision=self.decision,
            repair_instructions=("repair mock",) if self.decision == "repair" else (),
            safety_notes=("format only",),
            reference_summary={},
            candidate_summary={},
        )
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        (output_path / "format_recreation_review.json").write_text(
            json.dumps(review.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return review


class FakeRepairService:
    def __init__(
        self,
        *,
        status: str = "repaired",
        followup_decision: str | None = "approve_mechanics",
        followup_score: float = 9.0,
    ) -> None:
        self.status = status
        self.followup_decision = followup_decision
        self.followup_score = followup_score

    def run(
        self,
        *,
        source_short_dir,
        review_json_path,
        output_dir,
        reference_observation_json=None,
    ) -> FormatRecreationRepairResult:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        final_mp4 = output_path / "final.mp4"
        final_mp4.write_bytes(b"fake-repaired-mp4")
        followup_path = output_path / "followup_format_review" / "format_recreation_review.json"
        followup_path.parent.mkdir(parents=True, exist_ok=True)
        followup_path.write_text(
            json.dumps(
                {"decision": self.followup_decision, "overall_score": self.followup_score},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        result = FormatRecreationRepairResult(
            status=self.status,
            source_short_dir=str(source_short_dir),
            output_dir=str(output_path),
            repaired_mp4=str(final_mp4),
            repair_plan_path=str(output_path / "format_recreation_repair_plan.json"),
            render_qa_path=str(output_path / "render_qa.json"),
            visual_quality_report_path=str(output_path / "visual_quality_report.json"),
            followup_review_path=str(followup_path),
            followup_decision=self.followup_decision,
            notes=("mock",),
        )
        (output_path / "format_recreation_repair_result.json").write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return result
