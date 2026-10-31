"""Pure reference normalization and output checks for the local CTC aligner."""
from __future__ import annotations

import math
import re


MIN_WORD_CONFIDENCE = 0.025


def build_alignment_targets(reference: str, labels: tuple[str, ...]) -> tuple[list[str], list[tuple[str, int, int]]]:
    """Return normalized CTC characters and each original word's character range.

    Whitespace runs (including newlines) collapse to one CTC word-boundary token.
    Hyphens and other punctuation remain in the displayed word but are omitted
    from the character targets. Curly apostrophes map to the model's ASCII token.
    """
    words = re.findall(r"\S+", reference)
    if not words:
        raise ValueError("Reference transcript has no words.")
    supported = set(labels)
    targets: list[str] = []
    ranges: list[tuple[str, int, int]] = []
    for word in words:
        if targets:
            targets.append("|")
        start = len(targets)
        for char in word.upper():
            if char in ("’", "‘", "ʼ"):
                char = "'"
            if char.isalnum() or char == "'":
                if char not in supported:
                    raise ValueError(f"Unsupported reference character: {char!r}")
                targets.append(char)
        end = len(targets)
        if end == start:
            raise ValueError(f"Reference word has no alignable characters: {word!r}")
        ranges.append((word, start, end))
    if any(char not in supported for char in targets):
        raise ValueError("Reference contains a character unsupported by the CTC model.")
    return targets, ranges


def build_word_timestamps(reference: str, char_spans: list[tuple[float, float, float]], duration: float) -> list[dict]:
    """Map one CTC span per target char to exact reference words and validate."""
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Audio duration must be finite and positive.")
    targets, ranges = build_alignment_targets(reference, tuple("|'ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
    if len(char_spans) != len(targets):
        raise ValueError(f"Alignment token count mismatch ({len(char_spans)} != {len(targets)}).")
    for idx, item in enumerate(char_spans):
        if len(item) != 3 or not all(math.isfinite(value) for value in item):
            raise ValueError(f"Non-finite CTC span value at target token {idx}.")
        if not 0 <= item[2] <= 1:
            raise ValueError(f"CTC confidence outside [0, 1] at target token {idx}.")
    words = []
    for word, start_i, end_i in ranges:
        span = char_spans[start_i:end_i]
        start = min(item[0] for item in span)
        end = max(item[1] for item in span)
        confidence = sum(item[2] for item in span) / len(span)
        if not math.isfinite(start) or not math.isfinite(end) or not math.isfinite(confidence):
            raise ValueError(f"Non-finite timestamp or confidence for word {word!r}.")
        if not 0 <= confidence <= 1:
            raise ValueError(f"CTC confidence outside [0, 1] for word {word!r}.")
        if confidence < MIN_WORD_CONFIDENCE:
            raise ValueError(f"Low CTC confidence for word {word!r}: {confidence:.4f}")
        if start < 0 or end <= start or end > duration + 1e-5:
            raise ValueError(f"Invalid timestamp for word {word!r}.")
        if words and start < words[-1]["end_sec"]:
            raise ValueError(f"Overlapping or non-monotonic timestamp for word {word!r}.")
        words.append({"text": word, "start_sec": round(start, 4), "end_sec": round(end, 4), "confidence": round(confidence, 5)})
    return words
