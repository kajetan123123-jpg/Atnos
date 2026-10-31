from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from atnos.production.audio_pipeline import (
    AudioPipelineRequest,
    OfflineToneSynthesizer,
    WealthBlueprintAudioPipeline,
    force_align_words,
    split_sentences,
    validate_audio_alignment,
)
from atnos.production.audio_pipeline.alignment import write_silence_wav


def test_split_sentences_and_word_shape():
    sentences = split_sentences("Wealth compounds over time. Start early.")
    assert sentences == ["Wealth compounds over time.", "Start early."]
    words, segments = force_align_words(
        words=["Wealth", "compounds", "over", "time.", "Start", "early."],
        duration_seconds=3.0,
        segment_spans=[
            ("s01", ["Wealth", "compounds", "over", "time."]),
            ("s02", ["Start", "early."]),
        ],
    )
    assert words[0].word == "Wealth"
    assert words[0].start == 0.0
    assert words[0].end > words[0].start
    assert segments[0].id == "s01"
    assert segments[1].start >= segments[0].start


def test_validation_detects_large_gap_and_non_monotonic(tmp_path: Path):
    audio = tmp_path / "narration.wav"
    write_silence_wav(audio, duration_seconds=3.0)
    from atnos.production.audio_pipeline.models import SentenceSegment, WordTimestamp

    words = [
        WordTimestamp(word="Wealth", start=0.0, end=0.4, segment_id="s01"),
        WordTimestamp(word="compounds", start=2.0, end=2.4, segment_id="s01"),
    ]
    segments = [
        SentenceSegment(id="s01", text="Wealth compounds", start=0.0, end=2.4, word_count=2),
    ]
    report = validate_audio_alignment(audio_path=audio, words=words, segments=segments, large_gap_seconds=1.0)
    assert report.audio_exists is True
    assert report.large_gaps
    assert report.ok is True  # gaps are warnings

    bad_words = [
        WordTimestamp(word="Wealth", start=0.5, end=0.8, segment_id="s01"),
        WordTimestamp(word="compounds", start=0.1, end=0.3, segment_id="s01"),
    ]
    bad = validate_audio_alignment(audio_path=audio, words=bad_words, segments=segments)
    assert bad.ok is False
    assert bad.monotonic_timestamps is False


def test_e2e_short_sentence_with_cache(tmp_path: Path):
    cache_root = tmp_path / "cache"
    work_a = tmp_path / "job_a"
    work_b = tmp_path / "job_b"
    pipeline = WealthBlueprintAudioPipeline(
        synthesizer=OfflineToneSynthesizer(seconds_per_word=0.4),
        cache_root=cache_root,
        prefer_whisper=False,
    )
    script = "Wealth compounds over time."

    async def _run() -> None:
        first = await pipeline.run(script, work_dir=work_a)
        assert first.cache_hit is False
        assert Path(first.audio_file).is_file()
        assert Path(first.word_timestamps_path).is_file()
        assert first.words[0].word == "Wealth"
        assert first.words[0].start == 0.0
        assert first.words[-1].end == pytest.approx(first.duration_seconds, abs=0.02)
        assert first.segments[0].text == script
        assert first.validation.ok is True
        assert first.timing_source == "proportional_script"

        payload = json.loads(Path(first.word_timestamps_path).read_text(encoding="utf-8"))
        assert payload["words"][0]["word"] == "Wealth"
        assert "start" in payload["words"][0]
        assert "end" in payload["words"][0]

        second = await pipeline.run(AudioPipelineRequest(script=script), work_dir=work_b)
        assert second.cache_hit is True
        assert second.fingerprint == first.fingerprint
        assert Path(second.audio_file).is_file()
        assert second.words[0].word == "Wealth"

        third = await pipeline.run(
            AudioPipelineRequest(script=script, force_regenerate=True),
            work_dir=tmp_path / "job_c",
        )
        assert third.cache_hit is False

    asyncio.run(_run())


def test_pipeline_run_is_async():
    import inspect

    assert inspect.iscoroutinefunction(WealthBlueprintAudioPipeline.run)
