from __future__ import annotations

import json
from pathlib import Path
import shutil
import re
import unicodedata
import wave
from typing import Protocol

from atnos.core.config import Settings
from atnos.services.whisper_cpp_subtitle_service import WhisperCppSubtitleService


QUALITY_THRESHOLD = 0.85


class SubtitleBackend(Protocol):
    def generate(self, *, audio_path: Path, conversation_id: int | None):
        ...


class ShortSubtitleService:
    def __init__(self, settings: Settings, *, service: SubtitleBackend | None = None) -> None:
        self.service = service or WhisperCppSubtitleService(settings)

    def generate(self, *, audio_path: Path, output_dir: Path, fallback_text: str | None = None) -> Path:
        target = output_dir / "subtitles.srt"
        quality_path = output_dir / "subtitles_quality.json"
        try:
            descriptor = self.service.generate(audio_path=audio_path, conversation_id=None)
            shutil.copyfile(descriptor.absolute_path, target)
            if fallback_text:
                srt_text = target.read_text(encoding="utf-8", errors="replace")
                quality = _subtitle_quality(srt_text=srt_text, expected_text=fallback_text)
                fallback_reason = _fallback_reason(quality)
                if fallback_reason is not None:
                    whisper_copy = output_dir / "subtitles.whisper.srt"
                    shutil.copyfile(target, whisper_copy)
                    target.write_text(
                        _script_aligned_srt(fallback_text, total_duration_sec=_wav_duration_seconds(audio_path)),
                        encoding="utf-8",
                    )
                    _write_quality(
                        quality_path,
                        {
                            "source": "script_aligned_fallback",
                            "reason": fallback_reason,
                            "threshold": QUALITY_THRESHOLD,
                            "whisper_srt": str(whisper_copy),
                            "quality": quality,
                        },
                    )
                    return target
                _write_quality(
                    quality_path,
                    {
                        "source": "whisper_cpp",
                        "reason": "whisper_text_overlap_accepted",
                        "threshold": QUALITY_THRESHOLD,
                        "whisper_srt": None,
                        "quality": quality,
                    },
                )
            return target
        except Exception as exc:
            if fallback_text is None:
                raise
            target.write_text(
                _script_aligned_srt(fallback_text, total_duration_sec=_wav_duration_seconds(audio_path)),
                encoding="utf-8",
            )
            _write_quality(
                quality_path,
                {
                    "source": "script_aligned_runtime_error",
                    "reason": f"{type(exc).__name__}: {exc}",
                    "threshold": QUALITY_THRESHOLD,
                    "whisper_srt": None,
                    "quality": None,
                },
            )
            return target


def _fallback_srt(text: str) -> str:
    return _script_aligned_srt(text, total_duration_sec=None)


def _script_aligned_srt(text: str, *, total_duration_sec: float | None) -> str:
    chunks = _subtitle_chunks(text)
    if not chunks:
        chunks = [text.strip() or "Atnos short"]
    word_counts = [max(1, len(re.findall(r"\w+", chunk, flags=re.UNICODE))) for chunk in chunks]
    total_words = sum(word_counts)
    if total_duration_sec is None or total_duration_sec <= 0:
        total_duration_sec = max(4.0, total_words * 0.42)

    lines: list[str] = []
    current = 0.0
    for index, (chunk, words) in enumerate(zip(chunks, word_counts, strict=True), start=1):
        remaining_chunks = len(chunks) - index
        raw_duration = total_duration_sec * (words / total_words)
        duration = max(1.25, min(5.5, raw_duration))
        end = min(total_duration_sec, current + duration)
        if remaining_chunks == 0:
            end = max(end, total_duration_sec)
        lines.extend(
            [
                str(index),
                f"{_format_srt_time(current)} --> {_format_srt_time(end)}",
                chunk,
                "",
            ]
        )
        current = end
        if current >= total_duration_sec:
            break
    return "\n".join(lines)


def _subtitle_chunks(text: str) -> list[str]:
    chunks = [chunk.strip() for chunk in text.replace("?", ".").replace("!", ".").split(".") if chunk.strip()]
    if not chunks:
        return [text.strip()] if text.strip() else []
    readable: list[str] = []
    for chunk in chunks:
        words = chunk.split()
        if len(words) <= 4:
            readable.append(chunk)
            continue
        for start in range(0, len(words), 4):
            readable.append(" ".join(words[start : start + 4]))
    return readable


def _format_srt_time(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _wav_duration_seconds(path: Path) -> float | None:
    try:
        with wave.open(str(path), "rb") as handle:
            frame_count = handle.getnframes()
            frame_rate = handle.getframerate()
            if frame_rate <= 0:
                return None
            return frame_count / float(frame_rate)
    except Exception:
        return None


def _subtitle_quality(*, srt_text: str, expected_text: str) -> dict[str, object]:
    subtitle_text = _extract_srt_visible_text(srt_text)
    visible_lines = _extract_srt_visible_lines(srt_text)
    expected_tokens = _tokens(expected_text)
    subtitle_tokens = _tokens(subtitle_text)
    overlap_ratio = (len(expected_tokens & subtitle_tokens) / len(expected_tokens)) if expected_tokens and subtitle_tokens else 0.0
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
        "mojibake_detected": _has_mojibake(subtitle_text),
        "max_words_per_line": max((len(line.split()) for line in visible_lines), default=0),
        "max_chars_per_line": max((len(line) for line in visible_lines), default=0),
    }


def _extract_srt_visible_text(raw: str) -> str:
    return " ".join(_extract_srt_visible_lines(raw))


def _extract_srt_visible_lines(raw: str) -> list[str]:
    lines: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.isdigit() or "-->" in stripped:
            continue
        lines.append(stripped)
    return lines


def _fallback_reason(quality: dict[str, object]) -> str | None:
    if bool(quality.get("mojibake_detected")):
        return "whisper_text_contains_mojibake"
    if int(quality.get("suspicious_token_count") or 0) > 0:
        return "whisper_text_contains_suspicious_tokens"
    if int(quality.get("max_words_per_line") or 0) > 4:
        return "whisper_subtitle_lines_too_long_for_mobile"
    if int(quality.get("max_chars_per_line") or 0) > 34:
        return "whisper_subtitle_lines_too_wide_for_mobile"
    if float(quality.get("overlap_ratio") or 0.0) < QUALITY_THRESHOLD:
        return "whisper_text_overlap_below_threshold"
    return None


def _tokens(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", text.casefold())
    folded = "".join(char for char in normalized if not unicodedata.combining(char))
    return {token for token in re.findall(r"\w+", folded, flags=re.UNICODE) if len(token) > 3}


def _looks_like_transcription_artifact(token: str) -> bool:
    if re.search(r"(.)\1\1", token):
        return True
    common_fragments = ("fliny", "pener", "pomocz", "lokana", "analizat")
    return any(fragment in token for fragment in common_fragments)


def _has_mojibake(text: str) -> bool:
    suspicious = ("Ă", "Ĺ", "Ä", "ďż˝", "�")
    return any(token in text for token in suspicious)


def _write_quality(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
