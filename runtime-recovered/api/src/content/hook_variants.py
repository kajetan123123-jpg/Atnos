from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from content.script_generator import ShortScript


@dataclass(frozen=True, slots=True)
class HookVariant:
    id: str
    text: str
    visual_text: str
    psychographic_target: str
    score: float
    reason: str
    selected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class HookVariantSet:
    topic: str
    selected_hook_id: str
    hooks: tuple[HookVariant, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "selected_hook_id": self.selected_hook_id,
            "hooks": [hook.to_dict() for hook in self.hooks],
        }


def build_hook_variants(*, script: ShortScript, topic: str) -> HookVariantSet:
    normalized_topic = " ".join(topic.split()).strip()
    base_hook = _clean(script.hook) or f"Ten blad blokuje temat: {normalized_topic}"
    ideas = (
        (
            "a",
            base_hook,
            _short_visual_text(base_hook),
            "curiosity_gap",
            8.4,
            "Uses the accepted script hook, so narration and visual promise stay aligned.",
        ),
        (
            "b",
            f"Wiekszosc osob zle zaczyna temat: {normalized_topic}.",
            "ZLE ZACZYNASZ",
            "fear_of_wasted_effort",
            7.8,
            "Creates tension without making an unverifiable claim.",
        ),
        (
            "c",
            f"Zanim zrobisz kolejny krok z {normalized_topic}, sprawdz jedna rzecz.",
            "SPRAWDZ 1 RZECZ",
            "practical_control",
            7.6,
            "Promises a concrete checkpoint instead of generic motivation.",
        ),
        (
            "d",
            f"To nie jest problem narzedzia. To problem workflow w: {normalized_topic}.",
            "TO PROBLEM WORKFLOW",
            "identity_reframe",
            7.4,
            "Reframes the topic and gives the viewer a reason to keep watching.",
        ),
        (
            "e",
            f"Pokaze ci prostsza wersje: {normalized_topic}.",
            "PROSTSZA WERSJA",
            "simplicity_seekers",
            7.2,
            "Positions the short as a low-friction explainer.",
        ),
    )
    selected_id = max(ideas, key=lambda item: item[4])[0]
    hooks = tuple(
        HookVariant(
            id=item[0],
            text=item[1],
            visual_text=item[2],
            psychographic_target=item[3],
            score=item[4],
            reason=item[5],
            selected=item[0] == selected_id,
        )
        for item in ideas
    )
    return HookVariantSet(topic=normalized_topic, selected_hook_id=selected_id, hooks=hooks)


def _clean(value: str) -> str:
    return " ".join(str(value or "").replace("`", "").split()).strip()


def _short_visual_text(value: str) -> str:
    words = _clean(value).upper().split()
    return " ".join(words[:5]) if words else "HOOK"
