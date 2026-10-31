from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from atnos.core.config import Settings
from content.format_recreation_review import (
    FormatRecreationReviewService,
    load_reference_observation,
)


def test_format_recreation_review_approves_close_vertical_mechanics() -> None:
    with TemporaryDirectory(prefix="atnos-format-review-") as tmpdir:
        root = Path(tmpdir)
        service = FormatRecreationReviewService(Settings(app_root=root, data_dir=root / "data"))

        review = service.review(
            reference_observation=_package(
                kind="public_player_sample_analysis_only",
                frames=[
                    _frame(width=1280, height=720, brightness=0.20, contrast=0.50, saturation=0.20),
                    _frame(width=1280, height=720, brightness=0.30, contrast=0.62, saturation=0.30),
                    _frame(width=1280, height=720, brightness=0.42, contrast=0.58, saturation=0.26),
                ],
            ),
            candidate_observation=_package(
                kind="local_video",
                frames=[
                    _frame(width=1080, height=1920, brightness=0.24, contrast=0.51, saturation=0.22),
                    _frame(width=1080, height=1920, brightness=0.34, contrast=0.64, saturation=0.31),
                    _frame(width=1080, height=1920, brightness=0.48, contrast=0.60, saturation=0.28),
                ],
            ),
            candidate_video_path=root / "candidate.mp4",
            output_dir=root / "review",
        )

        assert review.decision == "approve_mechanics"
        assert review.overall_score >= 7.5
        assert (root / "review" / "format_recreation_review.json").exists()
        assert (root / "review" / "format_recreation_review.md").exists()


def test_format_recreation_review_requires_repair_for_flat_candidate() -> None:
    with TemporaryDirectory(prefix="atnos-format-review-flat-") as tmpdir:
        root = Path(tmpdir)
        service = FormatRecreationReviewService(Settings(app_root=root, data_dir=root / "data"))

        review = service.review(
            reference_observation=_package(
                kind="public_player_sample_analysis_only",
                frames=[
                    _frame(width=1280, height=720, brightness=0.18, contrast=0.62, saturation=0.32),
                    _frame(width=1280, height=720, brightness=0.42, contrast=0.70, saturation=0.45),
                    _frame(width=1280, height=720, brightness=0.55, contrast=0.66, saturation=0.40),
                ],
            ),
            candidate_observation=_package(
                kind="local_video",
                frames=[
                    _frame(width=1080, height=1920, brightness=0.80, contrast=0.12, saturation=0.05),
                    _frame(width=1080, height=1920, brightness=0.81, contrast=0.13, saturation=0.05),
                    _frame(width=1080, height=1920, brightness=0.82, contrast=0.12, saturation=0.06),
                ],
            ),
            output_dir=root / "review",
        )

        assert review.decision in {"repair", "reject"}
        assert any("contrast" in item.casefold() for item in review.repair_instructions)
        assert any("beat" in item.casefold() for item in review.repair_instructions)


def test_format_recreation_review_uses_metadata_fallback_when_reference_frames_missing() -> None:
    with TemporaryDirectory(prefix="atnos-format-review-missing-ref-") as tmpdir:
        root = Path(tmpdir)
        service = FormatRecreationReviewService(Settings(app_root=root, data_dir=root / "data"))

        review = service.review(
            reference_observation={
                "source_ref": "missing-ref",
                "source_title": "Unavailable public Short",
                "source_kind": "missing_reference",
                "status": "missing_reference",
                "frame_count": 0,
                "frames": [],
            },
            candidate_observation=_package(
                kind="local_video",
                frames=[
                    _frame(width=1080, height=1920, brightness=0.20, contrast=0.50, saturation=0.22),
                    _frame(width=1080, height=1920, brightness=0.36, contrast=0.62, saturation=0.31),
                    _frame(width=1080, height=1920, brightness=0.50, contrast=0.63, saturation=0.36),
                ],
            ),
            output_dir=root / "review",
        )

        assert review.decision == "approve_mechanics"
        assert review.overall_score >= 7.5
        assert any("metadata-only fallback" in item.evidence for item in review.signal_scores)


def test_load_reference_observation_accepts_public_summary_list() -> None:
    with TemporaryDirectory(prefix="atnos-format-review-load-") as tmpdir:
        root = Path(tmpdir)
        summary = root / "summary.json"
        observation = _package(kind="public_player_sample_analysis_only", frames=[_frame()])
        summary.write_text(
            json.dumps([{"status": "observed", "observation_package": observation}], ensure_ascii=False),
            encoding="utf-8",
        )

        loaded = load_reference_observation(summary)

        assert loaded["source_kind"] == "public_player_sample_analysis_only"
        assert loaded["frame_count"] == 1


def _package(*, kind: str, frames: list[dict[str, object]]) -> dict[str, object]:
    return {
        "source_ref": "ref-1",
        "source_title": "Reference Short",
        "source_channel": "Reference Channel",
        "source_kind": kind,
        "status": "observed",
        "frame_count": len(frames),
        "frames": frames,
        "aggregate_notes": ["mock"],
        "motion_pacing_hypothesis": "mock",
        "safe_learning_summary": "Do not copy source pixels.",
    }


def _frame(
    *,
    width: int = 1080,
    height: int = 1920,
    brightness: float = 0.30,
    contrast: float = 0.50,
    saturation: float = 0.25,
) -> dict[str, object]:
    return {
        "frame_id": "frame",
        "source_path": "frame.jpg",
        "timestamp_sec": 1.0,
        "width": width,
        "height": height,
        "aspect_ratio": round(width / height, 3),
        "brightness_score": brightness,
        "contrast_score": contrast,
        "saturation_score": saturation,
        "dominant_palette": ["#111111", "#ffffff"],
        "composition_notes": ["mock"],
        "status": "observed",
    }
