from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
import json
from typing import Any, Protocol, Sequence

from atnos.core.config import Settings
from content.format_recreation_repair import FormatRecreationRepairResult, FormatRecreationRepairService
from content.format_recreation_review import FormatRecreationReview, FormatRecreationReviewService


class FormatReviewRunner(Protocol):
    def review_from_paths(
        self,
        *,
        reference_observation_json: str | Path,
        candidate_video_path: str | Path,
        output_dir: str | Path,
        sample_times_sec: Sequence[float] = ...,
    ) -> FormatRecreationReview:
        ...


class FormatRepairRunner(Protocol):
    def run(
        self,
        *,
        source_short_dir: str | Path,
        review_json_path: str | Path,
        output_dir: str | Path,
        reference_observation_json: str | Path | None = ...,
    ) -> FormatRecreationRepairResult:
        ...


@dataclass(frozen=True, slots=True)
class FormatRecreationLoopResult:
    status: str
    source_short_dir: str
    reference_observation_json: str
    output_dir: str
    candidate_video_path: str | None
    initial_review_path: str | None
    initial_decision: str | None
    initial_score: float | None
    repair_applied: bool
    repair_result_path: str | None
    repaired_mp4: str | None
    final_video_path: str | None
    final_review_path: str | None
    final_decision: str | None
    final_score: float | None
    notes: tuple[str, ...]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FormatRecreationLoopService:
    """Runs review -> repair-if-needed -> follow-up review for generated shorts.

    This is the reusable bridge between public Shorts learning and Atnos local
    production. It only compares and repairs format mechanics. It never copies
    source footage, audio, identity, watermark, channel branding or script.
    """

    APPROVED_DECISION = "approve_mechanics"

    def __init__(
        self,
        settings: Settings,
        *,
        review_service: FormatReviewRunner | None = None,
        repair_service: FormatRepairRunner | None = None,
    ) -> None:
        self.settings = settings
        self.review_service = review_service or FormatRecreationReviewService(settings)
        self.repair_service = repair_service or FormatRecreationRepairService(settings)

    def run(
        self,
        *,
        source_short_dir: str | Path,
        reference_observation_json: str | Path,
        output_dir: str | Path,
        candidate_video_path: str | Path | None = None,
        sample_times_sec: Sequence[float] = (0.5, 2.0, 5.0, 10.0, 20.0, 35.0),
    ) -> FormatRecreationLoopResult:
        source_dir = Path(source_short_dir).resolve()
        reference_path = Path(reference_observation_json).resolve()
        output_path = Path(output_dir).resolve()
        output_path.mkdir(parents=True, exist_ok=True)
        candidate = Path(candidate_video_path).resolve() if candidate_video_path else source_dir / "final.mp4"

        if not candidate.exists():
            result = FormatRecreationLoopResult(
                status="missing_candidate_video",
                source_short_dir=str(source_dir),
                reference_observation_json=str(reference_path),
                output_dir=str(output_path),
                candidate_video_path=str(candidate),
                initial_review_path=None,
                initial_decision=None,
                initial_score=None,
                repair_applied=False,
                repair_result_path=None,
                repaired_mp4=None,
                final_video_path=None,
                final_review_path=None,
                final_decision=None,
                final_score=None,
                notes=(f"Missing candidate video: {candidate}",),
                created_at=datetime.now(UTC).isoformat(),
            )
            self._write_result(result, output_path)
            return result

        initial_dir = output_path / "initial_format_review"
        initial = self.review_service.review_from_paths(
            reference_observation_json=reference_path,
            candidate_video_path=candidate,
            output_dir=initial_dir,
            sample_times_sec=tuple(float(item) for item in sample_times_sec),
        )
        initial_review_path = initial_dir / "format_recreation_review.json"
        notes = [
            "Compared generated short against public-player reference observation.",
            "Safety scope: format mechanics only; no copying source content.",
        ]

        if initial.decision == self.APPROVED_DECISION:
            result = FormatRecreationLoopResult(
                status="approved_without_repair",
                source_short_dir=str(source_dir),
                reference_observation_json=str(reference_path),
                output_dir=str(output_path),
                candidate_video_path=str(candidate),
                initial_review_path=str(initial_review_path),
                initial_decision=initial.decision,
                initial_score=initial.overall_score,
                repair_applied=False,
                repair_result_path=None,
                repaired_mp4=None,
                final_video_path=str(candidate),
                final_review_path=str(initial_review_path),
                final_decision=initial.decision,
                final_score=initial.overall_score,
                notes=tuple(notes),
                created_at=datetime.now(UTC).isoformat(),
            )
            self._write_result(result, output_path)
            return result

        if initial.decision != "repair":
            result = FormatRecreationLoopResult(
                status="blocked_by_format_review",
                source_short_dir=str(source_dir),
                reference_observation_json=str(reference_path),
                output_dir=str(output_path),
                candidate_video_path=str(candidate),
                initial_review_path=str(initial_review_path),
                initial_decision=initial.decision,
                initial_score=initial.overall_score,
                repair_applied=False,
                repair_result_path=None,
                repaired_mp4=None,
                final_video_path=str(candidate),
                final_review_path=str(initial_review_path),
                final_decision=initial.decision,
                final_score=initial.overall_score,
                notes=tuple([*notes, "Initial format review rejected the candidate instead of requesting repair."]),
                created_at=datetime.now(UTC).isoformat(),
            )
            self._write_result(result, output_path)
            return result

        repair_dir = output_path / "repair_pass"
        repair = self.repair_service.run(
            source_short_dir=source_dir,
            review_json_path=initial_review_path,
            output_dir=repair_dir,
            reference_observation_json=reference_path,
        )
        repair_result_path = repair_dir / "format_recreation_repair_result.json"
        final_review_path = repair.followup_review_path
        final_decision = repair.followup_decision
        final_score = _load_review_score(final_review_path)
        if repair.status != "repaired":
            status = "repair_failed"
            final_video = None
            notes.append(f"Repair failed: {repair.status}.")
        elif final_decision == self.APPROVED_DECISION:
            status = "repaired_approved"
            final_video = repair.repaired_mp4
        elif final_decision == "repair":
            status = "repaired_still_needs_review"
            final_video = repair.repaired_mp4
            notes.append("Follow-up review still asks for another repair pass.")
        else:
            status = "repaired_needs_human_review"
            final_video = repair.repaired_mp4
            notes.append(f"Follow-up decision: {final_decision or 'not_run'}.")

        result = FormatRecreationLoopResult(
            status=status,
            source_short_dir=str(source_dir),
            reference_observation_json=str(reference_path),
            output_dir=str(output_path),
            candidate_video_path=str(candidate),
            initial_review_path=str(initial_review_path),
            initial_decision=initial.decision,
            initial_score=initial.overall_score,
            repair_applied=True,
            repair_result_path=str(repair_result_path),
            repaired_mp4=repair.repaired_mp4,
            final_video_path=final_video,
            final_review_path=final_review_path,
            final_decision=final_decision,
            final_score=final_score,
            notes=tuple(notes),
            created_at=datetime.now(UTC).isoformat(),
        )
        self._write_result(result, output_path)
        return result

    @staticmethod
    def _write_result(result: FormatRecreationLoopResult, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "format_recreation_loop_result.json").write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output_dir / "format_recreation_loop_report.md").write_text(
            render_format_recreation_loop_markdown(result),
            encoding="utf-8",
        )


def render_format_recreation_loop_markdown(result: FormatRecreationLoopResult) -> str:
    notes = "\n".join(f"- {item}" for item in result.notes) or "- none"
    return "\n".join(
        [
            "# Format Recreation Loop",
            "",
            f"- status: {result.status}",
            f"- initial_decision: {result.initial_decision or 'n/a'}",
            f"- initial_score: {result.initial_score if result.initial_score is not None else 'n/a'}",
            f"- repair_applied: {result.repair_applied}",
            f"- final_decision: {result.final_decision or 'n/a'}",
            f"- final_score: {result.final_score if result.final_score is not None else 'n/a'}",
            f"- final_video: {result.final_video_path or 'n/a'}",
            "",
            "## Inputs",
            f"- source_short_dir: {result.source_short_dir}",
            f"- reference_observation_json: {result.reference_observation_json}",
            f"- candidate_video_path: {result.candidate_video_path or 'n/a'}",
            "",
            "## Artifacts",
            f"- initial_review: {result.initial_review_path or 'n/a'}",
            f"- repair_result: {result.repair_result_path or 'n/a'}",
            f"- repaired_mp4: {result.repaired_mp4 or 'n/a'}",
            f"- final_review: {result.final_review_path or 'n/a'}",
            "",
            "## Notes",
            notes,
            "",
            "## Safety",
            "- The loop recreates format mechanics only.",
            "- Do not copy source footage, characters, voice, watermark, branding or script.",
            "",
        ]
    )


def _load_review_score(path: str | Path | None) -> float | None:
    if path is None:
        return None
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return float(payload.get("overall_score"))
    except (AttributeError, TypeError, ValueError):
        return None
