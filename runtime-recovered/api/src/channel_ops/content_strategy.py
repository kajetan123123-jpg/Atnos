from __future__ import annotations

from dataclasses import dataclass, field
import re


RISKY_TOPIC_MARKERS = (
    "zarob gwarantowane",
    "gwarantowany zysk",
    "szybkie pieniadze",
    "bez ryzyka",
    "lek na",
    "wyleczy",
    "pewny sposob",
    "oszustwo",
    "afera",
    "sekret ktorego",
    "kliknij zanim",
)

CLICKBAIT_MARKERS = (
    "szok",
    "nie uwierzysz",
    "ukrywaja",
    "zakazali",
    "tajemnica",
    "najwiekszy blad",
    "musisz to zobaczyc",
)


@dataclass(frozen=True, slots=True)
class TopicRecommendation:
    topic: str
    reason: str
    risk_level: str = "low"
    based_on: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StrategyPatternReport:
    winning_patterns: list[str] = field(default_factory=list)
    weak_patterns: list[str] = field(default_factory=list)
    risky_topics: list[str] = field(default_factory=list)
    repeated_formats: list[str] = field(default_factory=list)


def normalize_topic(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def extract_format_signature(topic: str) -> str:
    normalized = normalize_topic(topic)
    if " vs " in normalized or " kontra " in normalized:
        return "comparison"
    if normalized.startswith(("jak ", "jak ")) or " jak " in normalized:
        return "how_to"
    if any(token in normalized for token in ("dlaczego", "czemu", "po co")):
        return "why_explainer"
    if any(token in normalized for token in ("lista", "top ", "5 ", "3 ")):
        return "listicle"
    if any(token in normalized for token in ("blad", "bledy", "nie rob")):
        return "mistake_warning"
    if any(token in normalized for token in ("case study", "przyklad", "historia")):
        return "story_case"
    return "explainer"


def topic_risk_level(topic: str) -> str:
    normalized = normalize_ascii(normalize_topic(topic))
    if any(marker in normalized for marker in RISKY_TOPIC_MARKERS):
        return "high"
    if any(marker in normalized for marker in CLICKBAIT_MARKERS):
        return "medium"
    return "low"


def is_clickbait_like(topic: str) -> bool:
    normalized = normalize_ascii(normalize_topic(topic))
    return any(marker in normalized for marker in CLICKBAIT_MARKERS)


def normalize_ascii(text: str) -> str:
    replacements = {
        "ą": "a",
        "ć": "c",
        "ę": "e",
        "ł": "l",
        "ń": "n",
        "ó": "o",
        "ś": "s",
        "ź": "z",
        "ż": "z",
    }
    return "".join(replacements.get(char, char) for char in text)
