from __future__ import annotations

from pathlib import Path
import re
import unicodedata


MIN_RENDER_BYTES = 4096


class RenderQaService:
    def build(
        self,
        *,
        final_mp4: Path,
        render_metadata: dict[str, object],
        subtitles_path: Path,
        audio_path: Path,
        expected_text: str | None = None,
    ) -> dict[str, object]:
        qa = dict(render_metadata.get("qa") or {}) if isinstance(render_metadata.get("qa"), dict) else {}
        warnings = list(qa.get("warnings") or [])
        if not final_mp4.exists() or final_mp4.stat().st_size <= 0:
            warnings.append("final.mp4 missing or empty")
        elif final_mp4.stat().st_size < MIN_RENDER_BYTES:
            warnings.append(
                f"final.mp4 is not a valid render candidate: only {final_mp4.stat().st_size} bytes"
            )
        if not audio_path.exists() or audio_path.stat().st_size <= 0:
            warnings.append("narration.wav missing or empty")
        if not subtitles_path.exists() or subtitles_path.stat().st_size <= 0:
            warnings.append("subtitles.srt missing or empty")
        elif expected_text:
            subtitle_quality = _subtitle_quality(subtitles_path=subtitles_path, expected_text=expected_text)
            if subtitle_quality["overlap_ratio"] < 0.85:
                warnings.append(
                    "subtitles.srt has low text overlap with narration "
                    f"({subtitle_quality['overlap_ratio']:.2f}); review transcription quality"
                )
            if subtitle_quality["suspicious_token_count"] > 0:
                warnings.append(
                    "subtitles.srt contains suspicious transcription tokens: "
                    + ", ".join(subtitle_quality["suspicious_tokens"][:6])
                )
        youtube_short_checks = _youtube_short_checks(qa)
        if youtube_short_checks:
            if youtube_short_checks["has_audio"] is False:
                warnings.append("YouTube Short is missing an audio track")
            if youtube_short_checks["vertical_or_square"] is False:
                warnings.append("YouTube Short must be vertical or square")
            if youtube_short_checks["duration_seconds_ok"] is False:
                warnings.append("YouTube Short duration must be greater than 0 and no longer than 180 seconds")
            if youtube_short_checks["preferred_minimum_duration"] is False:
                warnings.append("Short is unusually brief; review retention and pacing before upload")
        return {
            "ok": not warnings,
            "final_mp4": str(final_mp4),
            "size_bytes": final_mp4.stat().st_size if final_mp4.exists() else 0,
            "render": render_metadata,
            "warnings": warnings,
            "youtube_shorts_ready": (
                bool(youtube_short_checks["ready"]) if youtube_short_checks else None
            ),
            "youtube_short_checks": youtube_short_checks,
        }


def _subtitle_quality(*, subtitles_path: Path, expected_text: str) -> dict[str, object]:
    subtitle_text = _extract_srt_visible_text(subtitles_path.read_text(encoding="utf-8", errors="replace"))
    expected_tokens = _tokens(expected_text)
    subtitle_tokens = _tokens(subtitle_text)
    if not expected_tokens or not subtitle_tokens:
        overlap_ratio = 0.0
    else:
        overlap_ratio = len(expected_tokens & subtitle_tokens) / len(expected_tokens)
    suspicious_tokens = tuple(
        sorted(
            token
            for token in subtitle_tokens - expected_tokens
            if len(token) >= 7 and _looks_like_transcription_artifact(token)
        )
    )
    return {
        "overlap_ratio": round(overlap_ratio, 3),
        "subtitle_token_count": len(subtitle_tokens),
        "expected_token_count": len(expected_tokens),
        "suspicious_token_count": len(suspicious_tokens),
        "suspicious_tokens": list(suspicious_tokens),
    }


def _extract_srt_visible_text(raw: str) -> str:
    lines: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.isdigit() or "-->" in stripped:
            continue
        lines.append(stripped)
    return " ".join(lines)


def _tokens(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", text.casefold())
    folded = "".join(char for char in normalized if not unicodedata.combining(char))
    return {token for token in re.findall(r"\w+", folded, flags=re.UNICODE) if len(token) > 3}


def _looks_like_transcription_artifact(token: str) -> bool:
    if re.search(r"(.)\1\1", token):
        return True
    common_fragments = ("fliny", "pener", "pomocz", "lokana", "analizat")
    return any(fragment in token for fragment in common_fragments)


def _youtube_short_checks(qa: dict[str, object]) -> dict[str, object]:
    keys = {"width", "height", "duration_sec", "has_audio"}
    if not any(key in qa for key in keys):
        return {}
    width = _as_float(qa.get("width"))
    height = _as_float(qa.get("height"))
    duration = _as_float(qa.get("duration_sec"))
    has_audio_raw = qa.get("has_audio")
    has_audio = bool(has_audio_raw) if has_audio_raw is not None else None
    vertical_or_square = None
    preferred_resolution = None
    if width is not None and height is not None:
        vertical_or_square = height >= width
        preferred_resolution = int(width) == 1080 and int(height) == 1920
    duration_seconds_ok = None if duration is None else 0 < duration <= 180
    preferred_minimum_duration = None if duration is None else duration >= 8
    ready_parts = [
        value
        for value in (vertical_or_square, duration_seconds_ok, has_audio)
        if value is not None
    ]
    return {
        "width": int(width) if width is not None else None,
        "height": int(height) if height is not None else None,
        "duration_sec": duration,
        "has_audio": has_audio,
        "vertical_or_square": vertical_or_square,
        "preferred_resolution_1080x1920": preferred_resolution,
        "duration_seconds_ok": duration_seconds_ok,
        "preferred_minimum_duration": preferred_minimum_duration,
        "ready": all(ready_parts) and preferred_minimum_duration is not False,
    }


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None
