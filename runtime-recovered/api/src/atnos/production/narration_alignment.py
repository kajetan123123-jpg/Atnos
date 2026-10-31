from __future__ import annotations

import math
import re
from typing import Any


def align_narration(
    segments: list[dict[str, Any]], alignment: dict[str, Any], *, duration: float
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Map provider character times to words and edit boundaries without stretching audio."""
    characters = alignment.get("characters", [])
    starts = alignment.get("character_start_times_seconds", [])
    ends = alignment.get("character_end_times_seconds", [])
    if not characters or len(characters) != len(starts) or len(starts) != len(ends):
        raise ValueError("Incomplete provider alignment")
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Invalid audio duration")
    previous_start = previous_end = 0.0
    for char, start, end in zip(characters, starts, ends, strict=True):
        if not isinstance(char, str) or len(char) != 1:
            raise ValueError("Alignment must contain individual characters")
        if not all(isinstance(t, (int, float)) and math.isfinite(t) for t in (start, end)):
            raise ValueError("Non-finite provider timestamp")
        if start < previous_start or end < previous_end or end < start or end > duration + 0.12:
            raise ValueError("Non-monotonic or out-of-range provider timing")
        previous_start, previous_end = start, end

    # Word punctuation may be normalized by the provider; the lexical content must match.
    text = "".join(characters)
    matches = list(re.finditer(r"\S+", text))
    expected = [word for segment in segments for word in str(segment["text"]).split()]
    normalize = lambda value: re.sub(r"[^\w]", "", value.casefold())
    if [normalize(m.group()) for m in matches] != [normalize(word) for word in expected]:
        raise ValueError("Provider alignment does not match the narration")
    words = [
        {"text": word, "start_sec": float(starts[m.start()]), "end_sec": min(duration, float(ends[m.end() - 1]))}
        for word, m in zip(expected, matches, strict=True)
    ]
    if any(word["end_sec"] <= word["start_sec"] for word in words):
        raise ValueError("Zero-duration spoken word")
    timestamps = []
    offset = 0
    for index, segment in enumerate(segments):
        count = len(str(segment["text"]).split())
        if not count:
            raise ValueError("Empty narration segment")
        end_index = offset + count
        end = words[end_index]["start_sec"] if end_index < len(words) else duration
        start = words[offset]["start_sec"] if index else 0.0
        if end <= start:
            raise ValueError("Empty edit segment")
        for word in words[offset:end_index]:
            word["segment_id"] = segment["id"]
        timestamps.append({
            "segment_id": segment["id"], "text": segment["text"],
            "start_sec": start, "end_sec": end, "duration_sec": end - start,
            "speech_end_sec": words[end_index - 1]["end_sec"],
            "engine": "elevenlabs", "timing_source": "provider_character_alignment", "single_pass": True,
        })
        offset = end_index
    return timestamps, words
