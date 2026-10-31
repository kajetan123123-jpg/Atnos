from __future__ import annotations

import re

from atnos.agents.topic_hook.models import Hook, ScoredTopic


class HookGenerator:
    def generate_hooks(self, topic: ScoredTopic, *, language: str, style_guide: str | None = None) -> list[Hook]:
        subject = _subject(topic.title)
        if language.casefold().startswith("pl"):
            candidates = [
                ("curiosity", f"Tego o {subject} nikt ci nie mowi", "Za chwile pokaze prosty mechanizm, ktory wiekszosc osob pomija."),
                ("benefit", f"Jak zrozumiec {subject} w 60 sekund", "Zacznij od jednej zasady, ktora porzadkuje caly temat."),
                ("question", f"Dlaczego wszyscy myla sie co do {subject}?", "Problem nie jest w braku wiedzy, tylko w zlym skrocie myslowym."),
                ("contrast", "Amatorzy robia jedno. Madrzy robia drugie.", "Roznica pojawia sie wtedy, gdy decyzja staje sie praktyczna."),
                ("shock", f"{subject.capitalize()} kosztuje wiecej, niz myslisz", "Najwiekszy koszt zwykle nie jest widoczny na poczatku."),
            ]
        else:
            candidates = [
                ("curiosity", f"No one tells you this about {subject}", f"Here is the simple mechanism most people miss."),
                ("benefit", f"Understand {subject} in 60 seconds", f"Start with one rule that makes the whole topic clearer."),
                ("question", f"Why is everyone wrong about {subject}?", f"The problem is not missing knowledge, but the wrong mental shortcut."),
                ("contrast", "Beginners do this. Smart people do that.", "The difference shows up when the choice becomes practical."),
                ("shock", f"{subject.capitalize()} costs more than you think", "The biggest cost is usually invisible at the start."),
            ]

        hooks: list[Hook] = []
        for variant, text, opening_line in candidates:
            hooks.append(
                Hook(
                    variant=variant,  # type: ignore[arg-type]
                    text=_limit_words(text, 12),
                    opening_line=_limit_words(_style_opening(opening_line, style_guide), 20),
                )
            )
        return hooks[:5]


def _subject(title: str) -> str:
    cleaned = re.sub(r"^[0-9]+[).:-]\s*", "", title.strip())
    cleaned = re.sub(r"\s+", " ", cleaned)
    words = cleaned.split()
    if len(words) <= 4:
        return cleaned.lower()
    return " ".join(words[:4]).lower()


def _limit_words(value: str, max_words: int) -> str:
    words = value.strip().split()
    return " ".join(words[:max_words])


def _style_opening(value: str, style_guide: str | None) -> str:
    if not style_guide:
        return value
    guide = style_guide.casefold()
    if "bezpo" in guide or "direct" in guide:
        return value.replace("Za chwile", "Teraz").replace("Here is", "Here is")
    if "spokoj" in guide or "calm" in guide:
        return value.replace("Najwiekszy", "Cichy")
    return value
