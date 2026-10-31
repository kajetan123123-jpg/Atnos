from __future__ import annotations

from dataclasses import dataclass
import re

from content.script_generator import ShortScript


_TAG_PATTERN = re.compile(r"\[[^\]]{1,64}\]")


@dataclass(frozen=True, slots=True)
class ExpressiveVoicePlan:
    plain_text: str
    expressive_text: str
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "plain_text": self.plain_text,
            "expressive_text": self.expressive_text,
            "notes": list(self.notes),
        }


class ExpressiveVoiceDirector:
    """Creates a controlled narration performance layer for short-form voiceovers."""

    def build(self, script: ShortScript) -> ExpressiveVoicePlan:
        plain_text = self.strip_audio_tags(script.voiceover)
        sentences = self._split_sentences(plain_text)
        if not sentences:
            return ExpressiveVoicePlan(plain_text="", expressive_text="", notes=("empty voiceover",))

        tagged: list[str] = []
        for index, sentence in enumerate(sentences):
            tag = self._tag_for_sentence(index=index, total=len(sentences), sentence=sentence)
            tagged.append(f"{tag} {sentence}" if tag else sentence)

        expressive_text = " ".join(tagged).strip()
        return ExpressiveVoicePlan(
            plain_text=plain_text,
            expressive_text=expressive_text,
            notes=(
                "Use expressive_text only with a TTS model that supports audio tags.",
                "Use plain_text for Piper and other engines that may read tags aloud.",
            ),
        )

    @staticmethod
    def strip_audio_tags(text: str) -> str:
        without_tags = _TAG_PATTERN.sub("", text or "")
        return " ".join(without_tags.split()).strip()

    @staticmethod
    def _split_sentences(text: str) -> tuple[str, ...]:
        parts = re.split(r"(?<=[.!?])\s+", text.strip())
        return tuple(part.strip() for part in parts if part.strip())

    @staticmethod
    def _tag_for_sentence(*, index: int, total: int, sentence: str) -> str:
        lowered = sentence.casefold()
        if index == 0:
            return "[excited]"
        serious_markers = ("mistake", "risk", "warning", "careful", "problem", "danger", "avoid", "wrong")
        if any(marker in lowered for marker in serious_markers):
            return "[serious]"
        if "second" in lowered or "now" in lowered:
            return "[curious]"
        if any(marker in lowered for marker in ("błąd", "blad", "ryzyko", "uważaj", "uwazaj", "problem")):
            return "[serious]"
        if index == total - 1:
            return "[slow]"
        if "sekund" in lowered or "teraz" in lowered:
            return "[curious]"
        return ""
