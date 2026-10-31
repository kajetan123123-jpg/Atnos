"""Context size levels for Local Brain benchmarks."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ContextLevel(str, Enum):
    SHORT = "SHORT"
    MEDIUM = "MEDIUM"
    LONG = "LONG"
    STRESS = "STRESS"


@dataclass(frozen=True, slots=True)
class ContextLevelSpec:
    level: ContextLevel
    target_tokens: int
    description: str


CONTEXT_LEVELS: tuple[ContextLevelSpec, ...] = (
    ContextLevelSpec(ContextLevel.SHORT, 3_000, "~2–4k tokens"),
    ContextLevelSpec(ContextLevel.MEDIUM, 8_000, "~8k tokens"),
    ContextLevelSpec(ContextLevel.LONG, 16_000, "~16k tokens"),
    ContextLevelSpec(ContextLevel.STRESS, 32_000, "~32k tokens"),
)


def pad_context(seed: str, *, target_tokens: int) -> str:
    """Deterministic filler to approximate token budgets (words ≈ tokens/1.3)."""
    unit = (
        "Project note: prefer retrieval over dumping entire Project Brain. "
        "UNKNOWN remains UNKNOWN. Evidence gates beat model confidence. "
    )
    # Rough English heuristic: ~0.75 words/token → words ≈ target_tokens * 0.75
    target_words = max(1, int(target_tokens * 0.75))
    words = (seed + " " + unit).split()
    out: list[str] = []
    i = 0
    while len(out) < target_words:
        out.append(words[i % len(words)])
        i += 1
    return " ".join(out)
