from __future__ import annotations

from content.subtitles import _fallback_reason, _subtitle_chunks, _subtitle_quality


def test_script_aligned_subtitle_chunks_are_short_for_mobile_safe_zone() -> None:
    chunks = _subtitle_chunks(
        "Nie wysylaj tego do chmury, jesli laptop moze zrobic pierwszy szkic lokalnie. "
        "Atnos uklada script, narration, subtitles i thumbnail bez dotykania publicznego konta."
    )

    assert chunks
    assert max(len(chunk.split()) for chunk in chunks) <= 4


def test_subtitle_quality_rejects_mojibake_whisper_output() -> None:
    quality = _subtitle_quality(
        srt_text="1\n00:00:00,000 --> 00:00:02,000\nCeszĹ‚a i szord z kotem.\n",
        expected_text="Czeska i short z kotem.",
    )

    assert quality["mojibake_detected"] is True
    assert _fallback_reason(quality) == "whisper_text_contains_mojibake"


def test_subtitle_quality_rejects_long_mobile_lines() -> None:
    quality = _subtitle_quality(
        srt_text=(
            "1\n00:00:00,000 --> 00:00:03,000\n"
            "Nie wysylaj tego do chmury jesli laptop moze zrobic pierwszy szkic lokalnie.\n"
        ),
        expected_text="Nie wysylaj tego do chmury jesli laptop moze zrobic pierwszy szkic lokalnie.",
    )

    assert quality["max_words_per_line"] > 4
    assert _fallback_reason(quality) == "whisper_subtitle_lines_too_long_for_mobile"
