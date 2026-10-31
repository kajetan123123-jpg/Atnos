from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
import math
from pathlib import Path
import subprocess
from typing import Any, Callable, Iterable, Protocol, Sequence
import wave

from atnos.core.config import Settings
from atnos.services.piper_tts_service import PiperTtsService
from content.faceless_shorts_benchmark import DEFAULT_FACELESS_AI_QUERIES, FacelessShortsBenchmarkService
from content.format_recreation_review import FormatRecreationReview, FormatRecreationReviewService
from content.public_short_frame_observer import PublicShortFrameObservationRunner
from content.short_agent import ShortAgent
from content.subtitles import _script_aligned_srt
from content.visual_asset_pack_repair import VisualAssetPackBrightnessRepairResult, VisualAssetPackBrightnessRepairService
from content.visual_recreation_asset_pack import VisualRecreationAssetPackResult, VisualRecreationAssetPackService


class BenchmarkRunner(Protocol):
    def run(
        self,
        *,
        queries: Iterable[str] = ...,
        per_query_limit: int = ...,
        top_limit: int = ...,
        brief_limit: int = ...,
        allow_duration_anomalies: bool = ...,
    ) -> object:
        ...


class PublicObservationRunner(Protocol):
    def observe_benchmark(
        self,
        *,
        benchmark_json_path: str | Path,
        output_dir: str | Path | None = ...,
        top_limit: int = ...,
        sample_times_sec: Sequence[float] = ...,
        keep_frames: bool = ...,
    ) -> list[object]:
        ...


class ShortRenderer(Protocol):
    def run(self, topic: str, *, force_repair: bool = ...) -> object:
        ...


class VisualAssetPackBuilder(Protocol):
    def build(
        self,
        *,
        visual_plan_json: Path,
        output_dir: Path,
        plan_index: int = ...,
        source_short_dir: Path | None = ...,
        max_scenes: int = ...,
        generate_images: bool = ...,
        render_video: bool = ...,
        width: int = ...,
        height: int = ...,
        steps: int | None = ...,
    ) -> VisualRecreationAssetPackResult:
        ...


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


class BrightnessRepairRunner(Protocol):
    def run(
        self,
        *,
        asset_pack_dir: str | Path,
        output_dir: str | Path,
        brightness_factor: float = ...,
        contrast_factor: float = ...,
        sharpness_factor: float = ...,
        render_video: bool = ...,
    ) -> VisualAssetPackBrightnessRepairResult:
        ...


@dataclass(frozen=True, slots=True)
class FacelessRecreationProductionSessionResult:
    status: str
    session_dir: str
    benchmark_json: str
    visual_plan_json: str
    reference_observation_json: str
    source_short_dir: str
    asset_pack_dir: str | None
    asset_pack_video: str | None
    initial_review_path: str | None
    initial_decision: str | None
    initial_score: float | None
    repair_result_path: str | None
    repaired_video: str | None
    final_review_path: str | None
    final_decision: str | None
    final_score: float | None
    final_video: str | None
    steps: tuple[dict[str, Any], ...]
    safety_notes: tuple[str, ...]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FacelessRecreationProductionSessionService:
    """Runs the full faceless Shorts learning-to-recreation production loop.

    Scope is intentionally format-level: watch public Shorts metadata/frames,
    infer what format mechanics work, produce original Atnos assets, repair
    mechanics if review asks for it, and stop at owner review. No public upload.
    """

    APPROVED_DECISION = "approve_mechanics"

    def __init__(
        self,
        settings: Settings,
        *,
        benchmark_service: BenchmarkRunner | None = None,
        observation_runner: PublicObservationRunner | None = None,
        short_renderer: ShortRenderer | None = None,
        asset_pack_builder: VisualAssetPackBuilder | None = None,
        review_runner: FormatReviewRunner | None = None,
        repair_runner: BrightnessRepairRunner | None = None,
    ) -> None:
        self.settings = settings
        self.benchmark_service = benchmark_service or FacelessShortsBenchmarkService(settings)
        self.observation_runner = observation_runner or PublicShortFrameObservationRunner(settings)
        self.short_renderer = short_renderer or ShortAgent(settings)
        self.asset_pack_builder = asset_pack_builder or VisualRecreationAssetPackService(settings)
        self.review_runner = review_runner or FormatRecreationReviewService(settings)
        self.repair_runner = repair_runner or VisualAssetPackBrightnessRepairService(settings)

    def run(
        self,
        *,
        benchmark_json: str | Path | None = None,
        reference_observation_json: str | Path | None = None,
        source_short_dir: str | Path | None = None,
        visual_plan_json: str | Path | None = None,
        output_dir: str | Path | None = None,
        queries: Iterable[str] = DEFAULT_FACELESS_AI_QUERIES,
        per_query_limit: int = 8,
        top_limit: int = 12,
        brief_limit: int = 1,
        sample_times_sec: Sequence[float] = (0.5, 2.0, 5.0, 10.0, 20.0, 35.0),
        keep_public_frames: bool = False,
        allow_duration_anomalies: bool = False,
        max_scenes: int = 5,
        plan_index: int = 0,
        width: int = 768,
        height: int = 1344,
        steps: int | None = 8,
        generate_images: bool = True,
        render_video: bool = True,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> FacelessRecreationProductionSessionResult:
        created_at = datetime.now(UTC)
        session_dir = Path(output_dir).resolve() if output_dir else self._default_output_dir(created_at)
        session_dir.mkdir(parents=True, exist_ok=True)
        step_log: list[dict[str, Any]] = []

        _emit_progress(
            progress_callback,
            phase="production_start",
            message="Starting one safe recreation production session.",
            session_dir=str(session_dir),
            plan_index=max(0, int(plan_index)),
        )
        benchmark_path = Path(benchmark_json).resolve() if benchmark_json else self._run_benchmark(
            queries=queries,
            per_query_limit=per_query_limit,
            top_limit=top_limit,
            brief_limit=brief_limit,
            allow_duration_anomalies=allow_duration_anomalies,
            steps=step_log,
        )
        benchmark_payload = _load_json_object(benchmark_path)
        plan_path = Path(visual_plan_json).resolve() if visual_plan_json else self._resolve_visual_plan_path(
            benchmark_path=benchmark_path,
            benchmark_payload=benchmark_payload,
        )
        _emit_progress(
            progress_callback,
            phase="production_plan",
            message="Resolved benchmark and visual recreation plan.",
            benchmark_json=str(benchmark_path),
            visual_plan_json=str(plan_path),
        )

        reference_path = (
            Path(reference_observation_json).resolve()
            if reference_observation_json
            else self._observe_reference(
                benchmark_path=benchmark_path,
                output_dir=session_dir / "public_player_observation",
                sample_times_sec=sample_times_sec,
                keep_public_frames=keep_public_frames,
                steps=step_log,
            )
        )
        _emit_progress(
            progress_callback,
            phase="source_short_render",
            message="Rendering local source short for visual recreation input.",
            plan_index=max(0, int(plan_index)),
        )
        source_dir = (
            Path(source_short_dir).resolve()
            if source_short_dir
            else self._build_fast_source_short(
                benchmark_payload=benchmark_payload,
                plan_index=max(0, int(plan_index)),
                output_dir=session_dir / "source_short",
                steps=step_log,
            )
        )

        _emit_progress(
            progress_callback,
            phase="visual_asset_pack",
            message="Generating visual asset pack and final local MP4.",
            visual_plan_json=str(plan_path),
            source_short_dir=str(source_dir),
        )
        asset_pack = self.asset_pack_builder.build(
            visual_plan_json=plan_path,
            output_dir=session_dir / "visual_asset_pack",
            plan_index=max(0, int(plan_index)),
            source_short_dir=source_dir,
            max_scenes=max_scenes,
            generate_images=generate_images,
            render_video=render_video,
            width=width,
            height=height,
            steps=steps,
        )
        asset_pack_video = asset_pack.final_mp4
        step_log.append(
            {
                "step": "visual_asset_pack",
                "status": asset_pack.status,
                "output_dir": asset_pack.output_dir,
                "final_mp4": asset_pack.final_mp4,
                "scene_count": len(asset_pack.scene_assets),
            }
        )
        if not asset_pack_video:
            result = self._result(
                status="asset_pack_needs_human_review",
                session_dir=session_dir,
                benchmark_path=benchmark_path,
                plan_path=plan_path,
                reference_path=reference_path,
                source_dir=source_dir,
                asset_pack=asset_pack,
                initial_review=None,
                repair_result=None,
                final_review=None,
                final_video=None,
                step_log=step_log,
                created_at=created_at,
            )
            self._write_result(result)
            _emit_progress(
                progress_callback,
                phase="production_needs_review",
                message="Production stopped because asset pack did not produce final MP4.",
                status=result.status,
            )
            return result

        _emit_progress(
            progress_callback,
            phase="format_review",
            message="Reviewing recreated output against public reference mechanics.",
            candidate_video_path=asset_pack_video,
        )
        initial_review = self.review_runner.review_from_paths(
            reference_observation_json=reference_path,
            candidate_video_path=asset_pack_video,
            output_dir=session_dir / "initial_format_review",
            sample_times_sec=sample_times_sec,
        )
        initial_review_path = session_dir / "initial_format_review" / "format_recreation_review.json"
        step_log.append(
            {
                "step": "initial_format_review",
                "status": initial_review.decision,
                "score": initial_review.overall_score,
                "review_path": str(initial_review_path),
            }
        )
        if initial_review.decision == self.APPROVED_DECISION:
            result = self._result(
                status="ready_for_owner_review",
                session_dir=session_dir,
                benchmark_path=benchmark_path,
                plan_path=plan_path,
                reference_path=reference_path,
                source_dir=source_dir,
                asset_pack=asset_pack,
                initial_review=initial_review,
                repair_result=None,
                final_review=initial_review,
                final_video=asset_pack_video,
                step_log=step_log,
                created_at=created_at,
            )
            self._write_result(result)
            _emit_progress(
                progress_callback,
                phase="production_completed",
                message="Production session ready for owner review.",
                status=result.status,
                final_video=result.final_video,
                final_score=result.final_score,
            )
            return result

        if initial_review.decision != "repair":
            result = self._result(
                status="blocked_by_format_review",
                session_dir=session_dir,
                benchmark_path=benchmark_path,
                plan_path=plan_path,
                reference_path=reference_path,
                source_dir=source_dir,
                asset_pack=asset_pack,
                initial_review=initial_review,
                repair_result=None,
                final_review=initial_review,
                final_video=asset_pack_video,
                step_log=step_log,
                created_at=created_at,
            )
            self._write_result(result)
            _emit_progress(
                progress_callback,
                phase="production_blocked",
                message="Production session blocked by format review.",
                status=result.status,
                final_video=result.final_video,
                final_score=result.final_score,
            )
            return result

        repair_tuning = _repair_tuning_from_review(initial_review)
        _emit_progress(
            progress_callback,
            phase="brightness_repair",
            message="Repairing brightness/contrast/readability before final review.",
            tuning=repair_tuning,
        )
        repair = self.repair_runner.run(
            asset_pack_dir=asset_pack.output_dir,
            output_dir=session_dir / "brightness_repair",
            brightness_factor=repair_tuning["brightness_factor"],
            contrast_factor=repair_tuning["contrast_factor"],
            sharpness_factor=repair_tuning["sharpness_factor"],
            render_video=render_video,
        )
        repair_result_path = session_dir / "brightness_repair" / "visual_asset_pack_brightness_repair_result.json"
        step_log.append(
            {
                "step": "brightness_repair",
                "status": repair.status,
                "repaired_video": repair.final_mp4,
                "result_path": str(repair_result_path),
            }
        )
        final_review: FormatRecreationReview | None = None
        final_video = repair.final_mp4 or asset_pack_video
        if repair.final_mp4:
            final_review = self.review_runner.review_from_paths(
                reference_observation_json=reference_path,
                candidate_video_path=repair.final_mp4,
                output_dir=session_dir / "final_format_review",
                sample_times_sec=sample_times_sec,
            )
            step_log.append(
                {
                    "step": "final_format_review",
                    "status": final_review.decision,
                    "score": final_review.overall_score,
                    "review_path": str(session_dir / "final_format_review" / "format_recreation_review.json"),
                }
            )
        status = "ready_for_owner_review" if final_review and final_review.decision == self.APPROVED_DECISION else "needs_human_review"
        result = self._result(
            status=status,
            session_dir=session_dir,
            benchmark_path=benchmark_path,
            plan_path=plan_path,
            reference_path=reference_path,
            source_dir=source_dir,
            asset_pack=asset_pack,
            initial_review=initial_review,
            repair_result=repair,
            final_review=final_review,
            final_video=final_video,
            step_log=step_log,
            created_at=created_at,
        )
        self._write_result(result)
        _emit_progress(
            progress_callback,
            phase="production_completed",
            message="Production session completed after repair.",
            status=result.status,
            final_video=result.final_video,
            final_score=result.final_score,
        )
        return result

    def _default_output_dir(self, created_at: datetime) -> Path:
        return (
            self.settings.data_dir
            / "learning"
            / "faceless-ai-shorts"
            / f"{created_at.strftime('%Y%m%dT%H%M%SZ')}_faceless_recreation_production_session"
        )

    def _run_benchmark(
        self,
        *,
        queries: Iterable[str],
        per_query_limit: int,
        top_limit: int,
        brief_limit: int,
        allow_duration_anomalies: bool,
        steps: list[dict[str, Any]],
    ) -> Path:
        run = self.benchmark_service.run(
            queries=queries,
            per_query_limit=max(1, int(per_query_limit)),
            top_limit=max(1, int(top_limit)),
            brief_limit=max(1, int(brief_limit)),
            allow_duration_anomalies=allow_duration_anomalies,
        )
        json_path = Path(str(getattr(run, "json_path"))).resolve()
        steps.append(
            {
                "step": "benchmark",
                "status": "completed",
                "candidate_count": getattr(run, "candidate_count", None),
                "json_path": str(json_path),
                "visual_recreation_plans_path": str(getattr(run, "visual_recreation_plans_path", "")),
            }
        )
        return json_path

    @staticmethod
    def _resolve_visual_plan_path(*, benchmark_path: Path, benchmark_payload: dict[str, Any]) -> Path:
        explicit = str(benchmark_payload.get("visual_recreation_plans_path") or "").strip()
        if explicit and Path(explicit).exists():
            return Path(explicit).resolve()
        candidate = benchmark_path.parent / "visual_recreation_plans.json"
        if candidate.exists():
            return candidate.resolve()
        raise FileNotFoundError(f"Missing visual recreation plans for benchmark: {benchmark_path}")

    def _observe_reference(
        self,
        *,
        benchmark_path: Path,
        output_dir: Path,
        sample_times_sec: Sequence[float],
        keep_public_frames: bool,
        steps: list[dict[str, Any]],
    ) -> Path:
        observations = self.observation_runner.observe_benchmark(
            benchmark_json_path=benchmark_path,
            output_dir=output_dir,
            top_limit=1,
            sample_times_sec=sample_times_sec,
            keep_frames=keep_public_frames,
        )
        summary = output_dir / "public_player_observation_summary.json"
        first = observations[0] if observations else None
        steps.append(
            {
                "step": "public_player_observation",
                "status": getattr(first, "status", "no_observation"),
                "source_video_id": getattr(first, "video_id", None),
                "source_title": getattr(first, "title", None),
                "extracted_frame_count": getattr(first, "extracted_frame_count", 0),
                "summary_path": str(summary),
            }
        )
        return summary.resolve()

    def _render_source_short(
        self,
        *,
        benchmark_payload: dict[str, Any],
        plan_index: int,
        steps: list[dict[str, Any]],
    ) -> Path:
        topic = _topic_from_benchmark(benchmark_payload, plan_index=plan_index)
        short = self.short_renderer.run(topic, force_repair=True)
        output_dir = Path(str(getattr(short, "output_dir"))).resolve()
        final_mp4 = getattr(short, "final_mp4", None)
        status = str(getattr(short, "status", "unknown"))
        steps.append(
            {
                "step": "source_short_render",
                "status": status,
                "topic": topic,
                "output_dir": str(output_dir),
                "final_mp4": str(final_mp4) if final_mp4 else None,
            }
        )
        if status != "manual_upload_ready" or final_mp4 is None:
            raise RuntimeError(f"Source short render did not produce a ready MP4. Status: {status}")
        return output_dir

    def _build_fast_source_short(
        self,
        *,
        benchmark_payload: dict[str, Any],
        plan_index: int,
        output_dir: Path,
        steps: list[dict[str, Any]],
    ) -> Path:
        """Create the minimum source assets needed for recreation without the heavy ShortAgent.

        The full ShortAgent can still be used as a separate quality pass, but this
        production loop must not block on LLM/script/Whisper work before it can
        generate visual recreation packs.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        topic = _topic_from_benchmark(benchmark_payload, plan_index=plan_index)
        source = _source_script_from_benchmark(benchmark_payload, plan_index=plan_index, topic=topic)
        script_path = output_dir / "script.md"
        script_path.write_text(source["markdown"], encoding="utf-8")
        (output_dir / "script.json").write_text(
            json.dumps(source, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        narration = output_dir / "narration.wav"
        tts_status = "piper"
        if self.settings.app_env == "test":
            tts_status = "synthetic_fallback:test"
            _write_synthetic_audio(narration, duration_sec=_estimated_duration(source["voiceover"]))
        else:
            try:
                _write_piper_audio_fast(self.settings, text=source["voiceover"], output_path=narration, timeout_s=45)
            except Exception as exc:
                try:
                    _write_sapi_audio(text=source["voiceover"], output_path=narration, timeout_s=45)
                    tts_status = f"sapi_fallback:{type(exc).__name__}"
                except Exception as sapi_exc:
                    tts_status = f"synthetic_fallback:{type(exc).__name__};sapi:{type(sapi_exc).__name__}"
                    _write_synthetic_audio(narration, duration_sec=_estimated_duration(source["voiceover"]))
        subtitles = output_dir / "subtitles.srt"
        subtitles.write_text(
            _script_aligned_srt(source["voiceover"], total_duration_sec=_wav_duration_seconds(narration)),
            encoding="utf-8",
        )
        (output_dir / "source_short_runtime.json").write_text(
            json.dumps(
                {
                    "status": "ready",
                    "mode": "fast_source_short",
                    "tts_status": tts_status,
                    "topic": topic,
                    "narration": str(narration),
                    "subtitles": str(subtitles),
                    "note": "Fast source assets for visual recreation; no public upload.",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        steps.append(
            {
                "step": "source_short_render",
                "status": "fast_source_ready",
                "topic": topic,
                "output_dir": str(output_dir),
                "narration": str(narration),
                "subtitles": str(subtitles),
                "tts_status": tts_status,
            }
        )
        return output_dir

    def _result(
        self,
        *,
        status: str,
        session_dir: Path,
        benchmark_path: Path,
        plan_path: Path,
        reference_path: Path,
        source_dir: Path,
        asset_pack: VisualRecreationAssetPackResult | None,
        initial_review: FormatRecreationReview | None,
        repair_result: VisualAssetPackBrightnessRepairResult | None,
        final_review: FormatRecreationReview | None,
        final_video: str | None,
        step_log: list[dict[str, Any]],
        created_at: datetime,
    ) -> FacelessRecreationProductionSessionResult:
        return FacelessRecreationProductionSessionResult(
            status=status,
            session_dir=str(session_dir),
            benchmark_json=str(benchmark_path),
            visual_plan_json=str(plan_path),
            reference_observation_json=str(reference_path),
            source_short_dir=str(source_dir),
            asset_pack_dir=asset_pack.output_dir if asset_pack else None,
            asset_pack_video=asset_pack.final_mp4 if asset_pack else None,
            initial_review_path=str(session_dir / "initial_format_review" / "format_recreation_review.json")
            if initial_review
            else None,
            initial_decision=initial_review.decision if initial_review else None,
            initial_score=initial_review.overall_score if initial_review else None,
            repair_result_path=str(session_dir / "brightness_repair" / "visual_asset_pack_brightness_repair_result.json")
            if repair_result
            else None,
            repaired_video=repair_result.final_mp4 if repair_result else None,
            final_review_path=str(session_dir / "final_format_review" / "format_recreation_review.json")
            if final_review and repair_result
            else (str(session_dir / "initial_format_review" / "format_recreation_review.json") if final_review else None),
            final_decision=final_review.decision if final_review else None,
            final_score=final_review.overall_score if final_review else None,
            final_video=final_video,
            steps=tuple(step_log),
            safety_notes=(
                "Learn from public Shorts as format mechanics only.",
                "Do not copy source footage, source audio, source script, watermark, channel branding or exact character identity.",
                "Generated videos stop at owner review/private/manual state; no public upload.",
            ),
            created_at=created_at.isoformat(),
        )

    @staticmethod
    def _write_result(result: FacelessRecreationProductionSessionResult) -> None:
        session_dir = Path(result.session_dir)
        session_dir.mkdir(parents=True, exist_ok=True)
        result_path = session_dir / "faceless_recreation_production_session_result.json"
        report_path = session_dir / "faceless_recreation_production_session_report.md"
        result_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        report_path.write_text(render_faceless_recreation_production_session_report(result), encoding="utf-8")


def render_faceless_recreation_production_session_report(
    result: FacelessRecreationProductionSessionResult,
) -> str:
    steps = "\n".join(
        _render_step_line(item)
        for item in result.steps
    )
    safety = "\n".join(f"- {item}" for item in result.safety_notes)
    return "\n".join(
        [
            "# Faceless Recreation Production Session",
            "",
            f"- status: `{result.status}`",
            f"- final_video: `{result.final_video or 'n/a'}`",
            f"- final_decision: `{result.final_decision or 'n/a'}`",
            f"- final_score: `{result.final_score if result.final_score is not None else 'n/a'}`",
            f"- benchmark_json: `{result.benchmark_json}`",
            f"- reference_observation_json: `{result.reference_observation_json}`",
            f"- source_short_dir: `{result.source_short_dir}`",
            f"- asset_pack_dir: `{result.asset_pack_dir or 'n/a'}`",
            "",
            "## Steps",
            steps or "- none",
            "",
            "## Safety",
            safety,
            "",
        ]
    )


def _render_step_line(item: dict[str, Any]) -> str:
    score = item.get("score")
    score_text = f" ({score})" if score is not None else ""
    return f"- {item.get('step')}: {item.get('status')}{score_text}"


def _load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _emit_progress(callback: Callable[[dict[str, Any]], None] | None, **payload: Any) -> None:
    if callback is None:
        return
    event = {
        "timestamp": datetime.now(UTC).isoformat(),
        **payload,
    }
    try:
        callback(event)
    except Exception:
        return


def _topic_from_benchmark(payload: dict[str, Any], *, plan_index: int) -> str:
    index = max(0, int(plan_index))
    briefs = payload.get("replication_briefs")
    if isinstance(briefs, list) and len(briefs) > index and isinstance(briefs[index], dict):
        topic = str(briefs[index].get("legal_recreation_topic") or "").strip()
        if topic:
            return topic
    candidates = payload.get("top_candidates")
    if isinstance(candidates, list) and len(candidates) > index and isinstance(candidates[index], dict):
        title = str(candidates[index].get("title") or "top faceless AI short").strip()
        return f"Odtworz bezpiecznie mechanike formatu: {title}"
    raise RuntimeError("Benchmark contains no replication brief or top candidate.")


def _source_script_from_benchmark(
    payload: dict[str, Any],
    *,
    plan_index: int,
    topic: str,
) -> dict[str, Any]:
    index = max(0, int(plan_index))
    candidate: dict[str, Any] = {}
    candidates = payload.get("top_candidates")
    if isinstance(candidates, list) and len(candidates) > index and isinstance(candidates[index], dict):
        candidate = candidates[index]
    title = str(candidate.get("title") or topic).strip()
    channel = str(candidate.get("channel") or "public reference").strip()
    pattern = str(candidate.get("pattern") or "faceless AI microstory").strip()
    hook = "Ten format zatrzymuje scroll w pierwszej sekundzie."
    body = [
        f"Zrodlo inspiracji to {pattern}: szybki konflikt, prosty bohater i jasny payoff.",
        "Odtwarzamy mechanike: duzy tekst, zmiany beatow, emocje i pionowy rytm.",
        "Nie kopiujemy kadrow, postaci, glosu, watermarku ani scenariusza.",
    ]
    cta = "Wersja trafia do review, nie do publicznego uploadu."
    voiceover = " ".join([hook, *body, cta])
    markdown = "\n".join(
        [
            "# Fast Source Short",
            "",
            f"- reference_title: {title}",
            f"- reference_channel: {channel}",
            f"- pattern: {pattern}",
            "",
            "## Voiceover",
            voiceover,
            "",
        ]
    )
    return {
        "title": title,
        "channel": channel,
        "pattern": pattern,
        "hook": hook,
        "body": body,
        "cta": cta,
        "voiceover": voiceover,
        "markdown": markdown,
    }


def _estimated_duration(text: str) -> float:
    words = [word for word in text.split() if word.strip()]
    return max(18.0, min(48.0, len(words) * 0.46))


def _write_synthetic_audio(path: Path, *, duration_sec: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 22050
    frames = int(sample_rate * max(1.0, duration_sec))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        for index in range(frames):
            # Low-volume pulse track; used only when local TTS is unavailable.
            value = int(1600 * math.sin(2 * math.pi * 220 * (index / sample_rate)))
            handle.writeframesraw(value.to_bytes(2, byteorder="little", signed=True))


def _write_piper_audio_fast(settings: Settings, *, text: str, output_path: Path, timeout_s: int) -> None:
    service = PiperTtsService(settings)
    runtime = service.runtime_availability()
    if not runtime.available:
        raise RuntimeError(runtime.reason)
    input_path = output_path.with_suffix(".input.txt")
    input_path.write_text(text, encoding="utf-8")
    command = service.build_command(input_path=input_path, output_path=output_path, speed=1.0)
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout_s, check=False)
    finally:
        input_path.unlink(missing_ok=True)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "Piper synthesis failed.")
    if not output_path.exists() or output_path.stat().st_size <= 1024:
        raise RuntimeError("Piper did not produce usable narration.")


def _write_sapi_audio(*, text: str, output_path: Path, timeout_s: int) -> None:
    """Use Windows' local SAPI voice as an offline fallback when Piper stalls."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    input_path = output_path.with_suffix(".sapi-input.txt")
    script_path = output_path.with_suffix(".sapi.ps1")
    input_path.write_text(text, encoding="utf-8")
    script_path.write_text(
        "\n".join(
            [
                "$ErrorActionPreference = 'Stop'",
                "Add-Type -AssemblyName System.Speech",
                f"$text = Get-Content -LiteralPath {_ps_single_quote(input_path)} -Raw -Encoding UTF8",
                "$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer",
                "$voice = $speaker.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -eq 'pl-PL' } | Select-Object -First 1",
                "if ($voice -ne $null) { $speaker.SelectVoice($voice.VoiceInfo.Name) }",
                "$speaker.Rate = 1",
                "$speaker.Volume = 95",
                f"$speaker.SetOutputToWaveFile({_ps_single_quote(output_path)})",
                "$speaker.Speak($text)",
                "$speaker.Dispose()",
            ]
        ),
        encoding="utf-8",
    )
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    finally:
        input_path.unlink(missing_ok=True)
        script_path.unlink(missing_ok=True)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "Windows SAPI synthesis failed.")
    if not output_path.exists() or output_path.stat().st_size <= 1024:
        raise RuntimeError("Windows SAPI did not produce usable narration.")


def _ps_single_quote(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _wav_duration_seconds(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as handle:
            rate = handle.getframerate()
            frames = handle.getnframes()
            if rate <= 0:
                return None
            return frames / float(rate)
    except Exception:
        return None


def _repair_tuning_from_review(review: FormatRecreationReview) -> dict[str, float]:
    reference_brightness = _optional_float((review.reference_summary or {}).get("avg_brightness"))
    candidate_brightness = _optional_float((review.candidate_summary or {}).get("avg_brightness"))
    brightness_factor = 1.28
    if reference_brightness and candidate_brightness and candidate_brightness > 0:
        if candidate_brightness < reference_brightness:
            brightness_factor = max(1.28, min(3.6, (reference_brightness / candidate_brightness) * 0.76))
        elif candidate_brightness > reference_brightness * 1.25:
            brightness_factor = max(0.72, min(1.0, (reference_brightness / candidate_brightness) * 1.08))
    return {
        "brightness_factor": round(brightness_factor, 2),
        "contrast_factor": 1.03 if brightness_factor > 2.0 else 1.08,
        "sharpness_factor": 1.05,
    }


def _optional_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
