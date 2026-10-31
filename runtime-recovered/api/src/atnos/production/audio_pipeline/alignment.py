from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
from pathlib import Path

from atnos.production.audio_pipeline.models import (
    AudioPipelineRequest,
    AudioValidationReport,
    SentenceSegment,
    ValidationIssue,
    WordTimestamp,
)


def split_sentences(script: str) -> list[str]:
    """Split narration into sentence-like segments for renderer sync."""
    parts = re.split(r"(?<=[.!?])\s+", script.strip())
    return [part.strip() for part in parts if part.strip()]


def tokenize_words(text: str) -> list[str]:
    return [match.group(0) for match in re.finditer(r"\S+", text.strip())]


def wav_duration_seconds(path: Path) -> float:
    import wave

    with wave.open(str(path), "rb") as wav:
        frames = wav.getnframes()
        rate = wav.getframerate()
        if rate <= 0:
            raise ValueError("Invalid WAV sample rate")
        return frames / float(rate)


def write_silence_wav(path: Path, *, duration_seconds: float, sample_rate: int = 24_000) -> Path:
    """Deterministic placeholder WAV for offline tests (not silent-zero production fallback)."""
    import array
    import math as _math
    import wave

    path.parent.mkdir(parents=True, exist_ok=True)
    n_frames = max(1, int(duration_seconds * sample_rate))
    samples = array.array("h")
    for index in range(n_frames):
        # Soft tone so duration/energy checks are non-trivial.
        value = int(6000 * _math.sin(2 * _math.pi * 220 * (index / sample_rate)))
        samples.append(value)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(samples.tobytes())
    return path


def force_align_words(
    *,
    words: list[str],
    duration_seconds: float,
    segment_spans: list[tuple[str, list[str]]] | None = None,
) -> tuple[list[WordTimestamp], list[SentenceSegment]]:
    """Distribute script words across audio duration (character-weighted)."""
    if duration_seconds <= 0 or not math.isfinite(duration_seconds):
        raise ValueError("Invalid audio duration for alignment")
    if not words:
        raise ValueError("No words to align")

    weights = [max(1, len(re.sub(r"[^\w]", "", word) or word)) for word in words]
    total = float(sum(weights))
    cursor = 0.0
    aligned: list[WordTimestamp] = []
    for word, weight in zip(words, weights, strict=True):
        span = duration_seconds * (weight / total)
        end = min(duration_seconds, cursor + span)
        if end <= cursor:
            end = min(duration_seconds, cursor + 0.04)
        aligned.append(WordTimestamp(word=word, start=round(cursor, 3), end=round(end, 3)))
        cursor = end
    if aligned:
        aligned[-1] = WordTimestamp(
            word=aligned[-1].word,
            start=aligned[-1].start,
            end=round(duration_seconds, 3),
            segment_id=aligned[-1].segment_id,
        )

    spans = segment_spans or [("s01", words)]
    segments: list[SentenceSegment] = []
    offset = 0
    for seg_id, seg_words in spans:
        if not seg_words:
            raise ValueError(f"Empty sentence segment {seg_id}")
        count = len(seg_words)
        chunk = aligned[offset : offset + count]
        for item in chunk:
            item.segment_id = seg_id
        segments.append(
            SentenceSegment(
                id=seg_id,
                text=" ".join(seg_words),
                start=chunk[0].start,
                end=chunk[-1].end,
                word_count=count,
            )
        )
        offset += count
    if offset != len(aligned):
        raise ValueError("Segment word counts do not cover the full script")
    return aligned, segments


def parse_srt_cues(srt_text: str) -> list[dict[str, float | str]]:
    """Parse whisper.cpp SRT into timed cues."""
    blocks = re.split(r"\n\s*\n", srt_text.strip(), flags=re.MULTILINE)
    cues: list[dict[str, float | str]] = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        timing_line = lines[1] if "-->" in lines[1] else lines[0]
        if "-->" not in timing_line:
            continue
        left, right = [part.strip() for part in timing_line.split("-->")]
        text_lines = lines[2:] if "-->" in lines[1] else lines[1:]
        text = " ".join(text_lines).strip()
        if not text:
            continue
        cues.append({"start": _srt_clock(left), "end": _srt_clock(right), "text": text})
    return cues


def align_from_whisper_srt(
    *,
    script: str,
    srt_text: str,
    duration_seconds: float,
) -> tuple[list[WordTimestamp], list[SentenceSegment], str]:
    """Map script words into Whisper cue windows; fall back to full-duration force align."""
    sentences = split_sentences(script)
    spans = [(f"s{index:02d}", tokenize_words(sentence)) for index, sentence in enumerate(sentences, start=1)]
    script_words = [word for _, words in spans for word in words]
    cues = parse_srt_cues(srt_text)
    if not cues:
        words, segments = force_align_words(
            words=script_words,
            duration_seconds=duration_seconds,
            segment_spans=spans,
        )
        return words, segments, "proportional_script"

    cue_words = [tokenize_words(str(cue["text"])) for cue in cues]
    flat_cue = [word for group in cue_words for word in group]
    if _normalize_tokens(flat_cue) != _normalize_tokens(script_words):
        # Whisper often drifts; keep script lexicon and place by proportional cue coverage.
        words, segments = force_align_words(
            words=script_words,
            duration_seconds=duration_seconds,
            segment_spans=spans,
        )
        # Soft snap segment boundaries toward cue timeline when cue count matches sentences.
        if len(cues) == len(segments):
            snapped: list[SentenceSegment] = []
            for segment, cue in zip(segments, cues, strict=True):
                snapped.append(
                    SentenceSegment(
                        id=segment.id,
                        text=segment.text,
                        start=float(cue["start"]),
                        end=min(duration_seconds, float(cue["end"])),
                        word_count=segment.word_count,
                    )
                )
            words = _redistribute_words_into_segments(script_words, snapped)
            return words, snapped, "whisper_srt_sentence_snap"
        return words, segments, "proportional_script_whisper_mismatch"

    aligned: list[WordTimestamp] = []
    for cue, group in zip(cues, cue_words, strict=True):
        start = float(cue["start"])
        end = min(duration_seconds, float(cue["end"]))
        local, _ = force_align_words(words=group, duration_seconds=max(0.04, end - start))
        for item in local:
            aligned.append(
                WordTimestamp(
                    word=item.word,
                    start=round(start + item.start, 3),
                    end=round(start + item.end, 3),
                )
            )
    # Re-attach sentence segment ids from script spans
    offset = 0
    segments: list[SentenceSegment] = []
    for seg_id, seg_words in spans:
        count = len(seg_words)
        chunk = aligned[offset : offset + count]
        for item in chunk:
            item.segment_id = seg_id
        segments.append(
            SentenceSegment(
                id=seg_id,
                text=" ".join(seg_words),
                start=chunk[0].start,
                end=chunk[-1].end,
                word_count=count,
            )
        )
        offset += count
    return aligned, segments, "whisper_srt_forced"


def _redistribute_words_into_segments(
    words: list[str],
    segments: list[SentenceSegment],
) -> list[WordTimestamp]:
    aligned: list[WordTimestamp] = []
    offset = 0
    for segment in segments:
        chunk_words = words[offset : offset + segment.word_count]
        local, _ = force_align_words(
            words=chunk_words,
            duration_seconds=max(0.04, segment.end - segment.start),
        )
        for item in local:
            aligned.append(
                WordTimestamp(
                    word=item.word,
                    start=round(segment.start + item.start, 3),
                    end=round(segment.start + item.end, 3),
                    segment_id=segment.id,
                )
            )
        offset += segment.word_count
    return aligned


def validate_audio_alignment(
    *,
    audio_path: Path,
    words: list[WordTimestamp],
    segments: list[SentenceSegment],
    large_gap_seconds: float = 1.25,
) -> AudioValidationReport:
    issues: list[ValidationIssue] = []
    audio_exists = audio_path.is_file() and audio_path.stat().st_size > 44
    duration: float | None = None
    if not audio_exists:
        issues.append(
            ValidationIssue(code="audio_missing", severity="error", message="narration.wav is missing or empty")
        )
    else:
        try:
            duration = wav_duration_seconds(audio_path)
        except Exception as exc:  # noqa: BLE001
            issues.append(
                ValidationIssue(code="audio_unreadable", severity="error", message=f"Cannot read WAV duration: {exc}")
            )
        else:
            if not math.isfinite(duration) or duration <= 0.05:
                issues.append(
                    ValidationIssue(
                        code="audio_too_short",
                        severity="error",
                        message=f"Audio duration is invalid: {duration}",
                    )
                )

    empty_segments = any(not segment.text.strip() or segment.word_count < 1 for segment in segments)
    if empty_segments or not segments:
        issues.append(
            ValidationIssue(code="empty_segments", severity="error", message="One or more sentence segments are empty")
        )

    monotonic = True
    previous_start = -1.0
    previous_end = -1.0
    for word in words:
        if word.end < word.start or word.start < previous_start - 1e-6 or word.start < previous_end - 0.35:
            monotonic = False
            break
        previous_start = word.start
        previous_end = word.end
    if not words:
        monotonic = False
        issues.append(ValidationIssue(code="no_words", severity="error", message="No word timestamps produced"))
    elif not monotonic:
        issues.append(
            ValidationIssue(code="non_monotonic", severity="error", message="Word timestamps are not monotonic")
        )

    large_gaps: list[dict[str, float | str]] = []
    for left, right in zip(words, words[1:], strict=False):
        gap = right.start - left.end
        if gap >= large_gap_seconds:
            large_gaps.append(
                {
                    "after_word": left.word,
                    "before_word": right.word,
                    "gap_seconds": round(gap, 3),
                }
            )
    if large_gaps:
        issues.append(
            ValidationIssue(
                code="large_gaps",
                severity="warning",
                message=f"Detected {len(large_gaps)} large timing gap(s) (>= {large_gap_seconds}s)",
            )
        )

    if duration is not None and words and words[-1].end > duration + 0.15:
        issues.append(
            ValidationIssue(
                code="timestamps_past_audio",
                severity="error",
                message="Word timestamps extend past audio duration",
            )
        )

    errors = [issue for issue in issues if issue.severity == "error"]
    return AudioValidationReport(
        ok=not errors and audio_exists and monotonic and not empty_segments,
        audio_exists=audio_exists,
        duration_seconds=None if duration is None else round(duration, 3),
        empty_segments=empty_segments,
        monotonic_timestamps=monotonic,
        large_gaps=large_gaps,
        issues=issues,
    )


def pipeline_fingerprint(request: AudioPipelineRequest, *, engine: str, voice_identity: dict) -> str:
    payload = {
        "script": request.script,
        "channel": request.channel,
        "engine": engine,
        "voice_identity": voice_identity,
        "sentences": request.sentences,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def cache_dir(root: Path, fingerprint: str) -> Path:
    return root / "cache" / fingerprint[:2] / fingerprint


def write_word_timestamps_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def copy_cached_artifacts(cache_path: Path, work_dir: Path) -> tuple[Path, Path]:
    audio_src = cache_path / "narration.wav"
    json_src = cache_path / "word_timestamps.json"
    if not audio_src.is_file() or not json_src.is_file():
        raise FileNotFoundError("Incomplete audio cache entry")
    work_dir.mkdir(parents=True, exist_ok=True)
    audio_dst = work_dir / "narration.wav"
    json_dst = work_dir / "word_timestamps.json"
    shutil.copy2(audio_src, audio_dst)
    shutil.copy2(json_src, json_dst)
    return audio_dst, json_dst


def store_cache(cache_path: Path, *, audio: Path, timestamps_json: Path) -> None:
    cache_path.mkdir(parents=True, exist_ok=True)
    shutil.copy2(audio, cache_path / "narration.wav")
    shutil.copy2(timestamps_json, cache_path / "word_timestamps.json")
    (cache_path / "meta.json").write_text(
        json.dumps({"audio": "narration.wav", "timestamps": "word_timestamps.json"}, indent=2) + "\n",
        encoding="utf-8",
    )


def _normalize_tokens(tokens: list[str]) -> list[str]:
    return [re.sub(r"[^\w]", "", token.casefold()) for token in tokens if re.sub(r"[^\w]", "", token)]


def _srt_clock(value: str) -> float:
    cleaned = value.replace(",", ".")
    hours, minutes, rest = cleaned.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(rest)
