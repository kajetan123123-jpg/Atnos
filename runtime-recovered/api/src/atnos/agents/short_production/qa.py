from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from atnos.agents.short_production.models import (
    ProductionFiles,
    QualityChecks,
    QualityReview,
    QualityScores,
    ShortAssetPlan,
)
from atnos.core.config import Settings


@dataclass(frozen=True, slots=True)
class MediaProbe:
    exists: bool
    duration_s: float | None = None
    has_audio: bool = False
    has_video: bool = False
    width: int | None = None
    height: int | None = None
    error: str | None = None


class QualityReviewService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def review(self, plan: ShortAssetPlan, files: ProductionFiles) -> QualityReview:
        short_path = Path(files.short_file_path)
        narration_path = Path(files.narration_file_path)
        thumbnail_path = Path(files.thumbnail_file_path)

        video_probe = self._probe_media(short_path)
        narration_probe = self._probe_media(narration_path)
        issues: list[str] = []
        fix_instructions: list[str] = []

        duration = video_probe.duration_s
        duration_ok = duration is not None and 30 <= duration <= 60
        if not duration_ok:
            issues.append("Video duration is outside the 30-60s Shorts target.")
            fix_instructions.append("Trim or extend the edit to 30-60 seconds.")

        audio_clear = narration_path.exists() and narration_path.stat().st_size > 0
        if video_probe.exists and video_probe.has_audio:
            audio_clear = True
        if not audio_clear:
            issues.append("Narration/audio file is missing or empty.")
            fix_instructions.append("Regenerate narration and re-export with an audio track.")

        hook_present = bool(plan.script.hook.strip()) and duration is not None and duration > 3
        hook_on_screen = bool(plan.script.hook.strip()) and any(
            plan.script.hook.casefold()[:20] in segment.prompt.casefold()
            or segment.segment_id == 1
            for segment in plan.visuals[:1]
        )
        if not hook_present:
            issues.append("Hook is not reliably present in the first 3 seconds.")
            fix_instructions.append("Place the approved hook as the first spoken line.")
        if not hook_on_screen:
            issues.append("Hook is not planned as visible on-screen text.")
            fix_instructions.append("Add the hook as large text in the first visual segment.")

        subtitles_synced = plan.subtitles.auto_generate and audio_clear and duration_ok
        if not subtitles_synced:
            issues.append("Subtitle sync cannot be verified from the current assets.")
            fix_instructions.append("Generate subtitles from final narration and review timing.")

        no_black_frames = video_probe.exists and video_probe.has_video and "black" not in short_path.name.casefold()
        if not no_black_frames:
            issues.append("Video stream is missing or may contain black-frame issues.")
            fix_instructions.append("Check the exported video timeline for missing clips or black gaps.")

        thumbnail_readable = (
            thumbnail_path.exists()
            and thumbnail_path.stat().st_size > 0
            and len(plan.thumbnail.headline.split()) <= 3
        )
        if not thumbnail_readable:
            issues.append("Thumbnail is missing, empty, or headline is too long.")
            fix_instructions.append("Create a thumbnail with max 3 high-contrast words.")

        cta_present = bool(plan.script.cta.strip()) and len(plan.script.cta.split()) >= 4
        if not cta_present:
            issues.append("CTA is missing or too weak.")
            fix_instructions.append("Add a short, natural CTA at the end.")

        brand_consistent = bool(plan.thumbnail.color_palette) and bool(plan.visuals)
        if not brand_consistent:
            issues.append("Visual plan lacks channel style consistency.")
            fix_instructions.append("Apply the channel style guide to visuals, captions, and thumbnail.")

        no_copyrighted_audio = "copyright" not in narration_path.name.casefold()
        if not no_copyrighted_audio:
            issues.append("Audio filename suggests copyrighted material risk.")
            fix_instructions.append("Use licensed or generated narration/music only.")

        checks = QualityChecks(
            hook_present=hook_present,
            hook_on_screen=hook_on_screen,
            duration_ok=duration_ok,
            audio_clear=audio_clear,
            subtitles_synced=subtitles_synced,
            no_black_frames=no_black_frames,
            thumbnail_readable=thumbnail_readable,
            cta_present=cta_present,
            brand_consistent=brand_consistent,
            no_copyrighted_audio=no_copyrighted_audio,
        )

        scores = self._scores(plan, video_probe=video_probe, checks=checks)
        recommendation = self._recommendation(checks=checks, overall=scores.overall)
        if recommendation == "approve":
            issues = []
            fix_instructions = []

        return QualityReview(
            plan_id=plan.plan_id,
            checks=checks,
            scores=scores,
            issues=issues,
            recommendation=recommendation,
            fix_instructions=_dedupe(fix_instructions),
        )

    def _scores(self, plan: ShortAssetPlan, *, video_probe: MediaProbe, checks: QualityChecks) -> QualityScores:
        hook_strength = _clamp(
            0.55
            + (0.15 if plan.script.hook.endswith("?") else 0.0)
            + (0.15 if len(plan.script.hook.split()) <= 12 else -0.1)
            + (0.1 if plan.script.opening_line else 0.0)
        )
        duration = video_probe.duration_s or plan.script.estimated_duration_s
        pacing = _clamp(1.0 - abs(45 - duration) / 45)
        visual_quality = _clamp(
            0.35
            + (0.2 if video_probe.has_video else 0.0)
            + (0.2 if video_probe.width and video_probe.height and video_probe.height >= video_probe.width else 0.0)
            + (0.15 if checks.no_black_frames else 0.0)
            + (0.1 if checks.thumbnail_readable else 0.0)
        )
        check_score = 1.0 - (checks.failed_count / 10)
        overall = _clamp((hook_strength * 0.25) + (pacing * 0.25) + (visual_quality * 0.25) + (check_score * 0.25))
        return QualityScores(
            hook_strength=round(hook_strength, 3),
            pacing=round(pacing, 3),
            visual_quality=round(visual_quality, 3),
            overall=round(overall, 3),
        )

    def _recommendation(self, *, checks: QualityChecks, overall: float) -> str:
        if overall >= 0.80 and checks.all_pass:
            return "approve"
        if overall >= 0.60 and checks.failed_count <= 2:
            return "fix"
        return "reject"

    def _probe_media(self, path: Path) -> MediaProbe:
        if not path.exists():
            return MediaProbe(exists=False, error="missing_file")
        if path.stat().st_size <= 0:
            return MediaProbe(exists=True, error="empty_file")

        ffprobe = self.settings.ffmpeg_exe or "ffprobe"
        if str(ffprobe).lower().endswith("ffmpeg.exe") or str(ffprobe).lower().endswith("ffmpeg"):
            ffprobe = str(Path(str(ffprobe)).with_name("ffprobe.exe" if str(ffprobe).lower().endswith(".exe") else "ffprobe"))

        try:
            result = subprocess.run(
                [
                    str(ffprobe),
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-show_streams",
                    "-of",
                    "json",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return MediaProbe(exists=True, error=type(exc).__name__)

        if result.returncode != 0:
            return MediaProbe(exists=True, error=result.stderr.strip()[:300])

        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return MediaProbe(exists=True, error="invalid_ffprobe_json")

        return self._probe_from_payload(payload)

    def _probe_from_payload(self, payload: dict[str, Any]) -> MediaProbe:
        streams = payload.get("streams") if isinstance(payload.get("streams"), list) else []
        duration_raw = (payload.get("format") or {}).get("duration") if isinstance(payload.get("format"), dict) else None
        duration_s = None
        try:
            duration_s = float(duration_raw) if duration_raw is not None else None
        except (TypeError, ValueError):
            duration_s = None
        video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
        audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
        return MediaProbe(
            exists=True,
            duration_s=duration_s,
            has_audio=audio_stream is not None,
            has_video=video_stream is not None,
            width=_int_or_none(video_stream.get("width")) if isinstance(video_stream, dict) else None,
            height=_int_or_none(video_stream.get("height")) if isinstance(video_stream, dict) else None,
        )


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
