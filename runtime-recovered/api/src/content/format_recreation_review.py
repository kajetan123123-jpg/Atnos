from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
import json
from typing import Any, Mapping, Sequence

from atnos.core.config import Settings
from content.video_frame_observation import FrameObservationPackage, VideoFrameObservationService


@dataclass(frozen=True, slots=True)
class FormatSignalScore:
    name: str
    score: float
    evidence: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FormatRecreationReview:
    review_id: str
    reviewed_at: str
    reference_source_ref: str
    reference_title: str
    candidate_source_ref: str
    candidate_video_path: str | None
    output_dir: str
    signal_scores: tuple[FormatSignalScore, ...]
    overall_score: float
    decision: str
    repair_instructions: tuple[str, ...]
    safety_notes: tuple[str, ...]
    reference_summary: dict[str, Any]
    candidate_summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["signal_scores"] = [score.to_dict() for score in self.signal_scores]
        return payload


class FormatRecreationReviewService:
    """Reviews whether a generated short recreates source format mechanics safely.

    This compares format-level signals only: aspect, contrast, brightness,
    saturation and beat-change evidence. It does not approve copying source
    footage, exact characters, audio, watermark, branding or script.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.frame_observer = VideoFrameObservationService(settings)

    def review(
        self,
        *,
        reference_observation: FrameObservationPackage | Mapping[str, Any],
        candidate_observation: FrameObservationPackage | Mapping[str, Any],
        candidate_video_path: str | Path | None = None,
        output_dir: str | Path,
    ) -> FormatRecreationReview:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        reference = _as_observation_dict(reference_observation)
        candidate = _as_observation_dict(candidate_observation)
        reference_metrics = _metrics(reference)
        candidate_metrics = _metrics(candidate)

        signals = (
            _vertical_translation_score(candidate_metrics),
            _contrast_alignment_score(reference_metrics, candidate_metrics),
            _brightness_alignment_score(reference_metrics, candidate_metrics),
            _saturation_alignment_score(reference_metrics, candidate_metrics),
            _pacing_signal_score(candidate_metrics),
            _safety_score(reference, candidate),
        )
        overall = round(
            (
                signals[0].score * 0.20
                + signals[1].score * 0.16
                + signals[2].score * 0.14
                + signals[3].score * 0.12
                + signals[4].score * 0.22
                + signals[5].score * 0.16
            ),
            2,
        )
        repairs = _repair_instructions(
            reference_metrics=reference_metrics,
            candidate_metrics=candidate_metrics,
            signal_scores=signals,
        )
        decision = _decision(overall=overall, repairs=repairs)
        review = FormatRecreationReview(
            review_id=f"format-review-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}",
            reviewed_at=datetime.now(UTC).isoformat(),
            reference_source_ref=str(reference.get("source_ref") or ""),
            reference_title=str(reference.get("source_title") or ""),
            candidate_source_ref=str(candidate.get("source_ref") or ""),
            candidate_video_path=str(candidate_video_path) if candidate_video_path is not None else None,
            output_dir=str(output_path),
            signal_scores=signals,
            overall_score=overall,
            decision=decision,
            repair_instructions=tuple(repairs),
            safety_notes=(
                "Review compares format mechanics only, not exact copyrighted/source content.",
                "Do not copy source frames, character identity, voice, watermark, channel branding or script.",
            ),
            reference_summary=reference_metrics,
            candidate_summary=candidate_metrics,
        )
        self.write_review(review, output_path)
        return review

    def review_from_paths(
        self,
        *,
        reference_observation_json: str | Path,
        candidate_video_path: str | Path,
        output_dir: str | Path,
        sample_times_sec: Sequence[float] = (0.5, 2.0, 5.0, 10.0, 20.0, 35.0),
    ) -> FormatRecreationReview:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        reference = load_reference_observation(reference_observation_json)
        candidate_video = Path(candidate_video_path).resolve()
        candidate_observation = self.frame_observer.observe_local_video(
            video_path=candidate_video,
            output_dir=output_path / "candidate_frame_observation",
            source_ref=candidate_video.stem,
            source_title=candidate_video.name,
            source_channel="Atnos generated short",
            sample_times_sec=sample_times_sec,
        )
        return self.review(
            reference_observation=reference,
            candidate_observation=candidate_observation,
            candidate_video_path=candidate_video,
            output_dir=output_path,
        )

    @staticmethod
    def write_review(review: FormatRecreationReview, output_dir: str | Path) -> None:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        (output_path / "format_recreation_review.json").write_text(
            json.dumps(review.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output_path / "format_recreation_review.md").write_text(
            render_format_recreation_review_markdown(review),
            encoding="utf-8",
        )


def load_reference_observation(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, list):
        if not payload:
            raise ValueError("Reference observation summary is empty.")
        first = payload[0]
        if not isinstance(first, dict):
            raise ValueError("Reference observation summary item must be an object.")
        nested = first.get("observation_package")
        if isinstance(nested, dict):
            return nested
        return first
    if isinstance(payload, dict):
        nested = payload.get("observation_package")
        if isinstance(nested, dict):
            return nested
        return payload
    raise ValueError("Reference observation JSON must contain an object or list.")


def render_format_recreation_review_markdown(review: FormatRecreationReview) -> str:
    scores = "\n".join(
        f"- {item.name}: {item.score}/10 - {item.evidence}" for item in review.signal_scores
    )
    repairs = "\n".join(f"- {item}" for item in review.repair_instructions) or "- Brak wymaganych napraw."
    safety = "\n".join(f"- {item}" for item in review.safety_notes)
    return "\n".join(
        [
            "# Format Recreation Review",
            "",
            f"- decision: {review.decision}",
            f"- overall_score: {review.overall_score}/10",
            f"- reference: {review.reference_title or review.reference_source_ref}",
            f"- candidate_video: {review.candidate_video_path or 'n/a'}",
            "",
            "## Signal Scores",
            scores,
            "",
            "## Repair Instructions",
            repairs,
            "",
            "## Safety Notes",
            safety,
            "",
            "## Reference Summary",
            f"```json\n{json.dumps(review.reference_summary, ensure_ascii=False, indent=2)}\n```",
            "",
            "## Candidate Summary",
            f"```json\n{json.dumps(review.candidate_summary, ensure_ascii=False, indent=2)}\n```",
            "",
        ]
    )


def _as_observation_dict(observation: FrameObservationPackage | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(observation, FrameObservationPackage):
        return observation.to_dict()
    return dict(observation)


def _metrics(observation: Mapping[str, Any]) -> dict[str, Any]:
    frames_raw = observation.get("frames") if isinstance(observation, Mapping) else []
    frames = [frame for frame in frames_raw if isinstance(frame, Mapping)] if isinstance(frames_raw, list) else []
    numeric = {
        "brightness": _avg(frames, "brightness_score"),
        "contrast": _avg(frames, "contrast_score"),
        "saturation": _avg(frames, "saturation_score"),
        "aspect_ratio": _avg(frames, "aspect_ratio"),
    }
    variances = {
        "brightness_delta": _span(frames, "brightness_score"),
        "contrast_delta": _span(frames, "contrast_score"),
        "saturation_delta": _span(frames, "saturation_score"),
    }
    first = frames[0] if frames else {}
    return {
        "source_ref": str(observation.get("source_ref") or ""),
        "source_title": str(observation.get("source_title") or ""),
        "source_kind": str(observation.get("source_kind") or ""),
        "status": str(observation.get("status") or ""),
        "frame_count": int(observation.get("frame_count") or len(frames)),
        "first_width": _optional_int(first.get("width")),
        "first_height": _optional_int(first.get("height")),
        "avg_brightness": numeric["brightness"],
        "avg_contrast": numeric["contrast"],
        "avg_saturation": numeric["saturation"],
        "avg_aspect_ratio": numeric["aspect_ratio"],
        **variances,
    }


def _vertical_translation_score(candidate: Mapping[str, Any]) -> FormatSignalScore:
    ratio = _optional_float(candidate.get("avg_aspect_ratio"))
    width = _optional_int(candidate.get("first_width"))
    height = _optional_int(candidate.get("first_height"))
    if ratio is None or width is None or height is None:
        return FormatSignalScore("vertical_translation", 0.0, "Candidate has no usable aspect ratio evidence.")
    if height > width and 0.50 <= ratio <= 0.60:
        return FormatSignalScore("vertical_translation", 10.0, f"Candidate is vertical 9:16-like ({width}x{height}).")
    if height >= width:
        return FormatSignalScore("vertical_translation", 7.0, f"Candidate is vertical-ish ({width}x{height}), but not ideal 9:16.")
    return FormatSignalScore("vertical_translation", 2.0, f"Candidate is not vertical ({width}x{height}).")


def _contrast_alignment_score(reference: Mapping[str, Any], candidate: Mapping[str, Any]) -> FormatSignalScore:
    return _alignment_score(
        "contrast_alignment",
        reference.get("avg_contrast"),
        candidate.get("avg_contrast"),
        ideal_tolerance=0.18,
        max_delta=0.55,
    )


def _brightness_alignment_score(reference: Mapping[str, Any], candidate: Mapping[str, Any]) -> FormatSignalScore:
    return _alignment_score(
        "brightness_alignment",
        reference.get("avg_brightness"),
        candidate.get("avg_brightness"),
        ideal_tolerance=0.22,
        max_delta=0.65,
    )


def _saturation_alignment_score(reference: Mapping[str, Any], candidate: Mapping[str, Any]) -> FormatSignalScore:
    return _alignment_score(
        "saturation_alignment",
        reference.get("avg_saturation"),
        candidate.get("avg_saturation"),
        ideal_tolerance=0.18,
        max_delta=0.55,
    )


def _alignment_score(
    name: str,
    reference_value: object,
    candidate_value: object,
    *,
    ideal_tolerance: float,
    max_delta: float,
) -> FormatSignalScore:
    reference = _optional_float(reference_value)
    candidate = _optional_float(candidate_value)
    if candidate is None:
        return FormatSignalScore(name, 0.0, "Missing candidate metric.")
    if reference is None:
        return FormatSignalScore(
            name,
            6.5,
            "Reference frame metric is missing; using metadata-only fallback and requiring owner review.",
        )
    delta = abs(reference - candidate)
    if delta <= ideal_tolerance:
        score = 10.0
    else:
        score = max(0.0, 10.0 * (1.0 - ((delta - ideal_tolerance) / max_delta)))
    return FormatSignalScore(name, round(score, 2), f"reference={reference:.3f}, candidate={candidate:.3f}, delta={delta:.3f}.")


def _pacing_signal_score(candidate: Mapping[str, Any]) -> FormatSignalScore:
    frame_count = int(candidate.get("frame_count") or 0)
    if frame_count < 3:
        return FormatSignalScore("pacing_signal", 2.0, f"Only {frame_count} candidate frame(s) observed.")
    delta = sum(
        float(candidate.get(key) or 0.0)
        for key in ("brightness_delta", "contrast_delta", "saturation_delta")
    )
    if delta >= 0.55:
        return FormatSignalScore("pacing_signal", 10.0, f"{frame_count} frames with strong visual beat changes (delta={delta:.3f}).")
    if delta >= 0.25:
        return FormatSignalScore("pacing_signal", 7.0, f"{frame_count} frames with moderate visual beat changes (delta={delta:.3f}).")
    return FormatSignalScore("pacing_signal", 4.0, f"{frame_count} frames, but weak visual variance (delta={delta:.3f}).")


def _safety_score(reference: Mapping[str, Any], candidate: Mapping[str, Any]) -> FormatSignalScore:
    reference_kind = str(reference.get("source_kind") or "")
    candidate_kind = str(candidate.get("source_kind") or "")
    if "public" in candidate_kind:
        return FormatSignalScore("safety", 2.0, f"Candidate source_kind should be generated/local, got {candidate_kind!r}.")
    if "public" in reference_kind or "reference" in reference_kind:
        return FormatSignalScore("safety", 10.0, "Reference is treated as analysis-only and candidate is local/generated.")
    return FormatSignalScore("safety", 8.0, "No public-copy signal detected.")


def _repair_instructions(
    *,
    reference_metrics: Mapping[str, Any],
    candidate_metrics: Mapping[str, Any],
    signal_scores: Sequence[FormatSignalScore],
) -> list[str]:
    repairs: list[str] = []
    score_map = {item.name: item for item in signal_scores}
    if score_map["vertical_translation"].score < 8:
        repairs.append("Re-render candidate as strict 1080x1920 vertical 9:16.")
    if score_map["contrast_alignment"].score < 7:
        if _has_reference_metric(reference_metrics, "avg_contrast"):
            repairs.append("Adjust foreground/background separation to better match the reference contrast mechanics.")
        else:
            repairs.append("Reference contrast metric unavailable; perform manual visual review against source thumbnail/title.")
    if score_map["brightness_alignment"].score < 7:
        if _has_reference_metric(reference_metrics, "avg_brightness"):
            ref_brightness = _optional_float(reference_metrics.get("avg_brightness")) or 0.0
            cand_brightness = _optional_float(candidate_metrics.get("avg_brightness")) or 0.0
            direction = "darken the scene and keep captions bright" if cand_brightness > ref_brightness else "brighten key subjects/captions"
            repairs.append(f"Brightness mismatch: {direction}.")
        else:
            repairs.append("Reference brightness metric unavailable; keep captions bright and verify manually.")
    if score_map["saturation_alignment"].score < 7:
        if _has_reference_metric(reference_metrics, "avg_saturation"):
            repairs.append("Tune color energy closer to the reference while changing exact palette and identity.")
        else:
            repairs.append("Reference saturation metric unavailable; verify color energy manually.")
    if score_map["pacing_signal"].score < 7:
        repairs.append("Increase beat-to-beat visual changes; use distinct scene cards/camera moves every 2-4 seconds.")
    repairs.append("Keep learning format mechanics only; do not copy source footage, characters, audio, watermark or script.")
    return repairs


def _decision(*, overall: float, repairs: Sequence[str]) -> str:
    non_blocking_prefixes = (
        "Keep learning",
        "Reference contrast metric unavailable",
        "Reference brightness metric unavailable",
        "Reference saturation metric unavailable",
    )
    meaningful_repairs = [item for item in repairs if not item.startswith(non_blocking_prefixes)]
    if overall >= 7.5 and not meaningful_repairs:
        return "approve_mechanics"
    if overall >= 5.5:
        return "repair"
    return "reject"


def _avg(frames: Sequence[Mapping[str, Any]], key: str) -> float | None:
    values = [_optional_float(frame.get(key)) for frame in frames]
    present = [value for value in values if value is not None]
    return round(sum(present) / len(present), 3) if present else None


def _span(frames: Sequence[Mapping[str, Any]], key: str) -> float:
    values = [_optional_float(frame.get(key)) for frame in frames]
    present = [value for value in values if value is not None]
    if not present:
        return 0.0
    return round(max(present) - min(present), 3)


def _optional_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _has_reference_metric(metrics: Mapping[str, Any], key: str) -> bool:
    return _optional_float(metrics.get(key)) is not None
