from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .channel_models import ChannelProfile
from .content_strategy import topic_risk_level


DEFAULT_TOPIC_TEMPLATES: tuple[str, ...] = (
    "Zanim uzyjesz {niche}: sprawdz te 3 rzeczy",
    "Jak {audience} moga zrobic pierwszy workflow bez chaosu",
    "Lokalnie czy w chmurze: kiedy {niche} ma najwiecej sensu",
    "Od promptu do final.mp4: prosty workflow dla {audience}",
    "Co musi przejsc QA, zanim short trafi do uploadu",
    "3 sygnaly, ze automatyzacja shortow jest gotowa do testu",
    "Jak nie przepalic czasu na narzedzia AI, ktore nic nie dowoza",
    "Dlaczego prywatny upload jest lepszy niz szybka publikacja",
    "Jedna rzecz, ktora poprawia jakosc faceless shorta od razu",
    "Jak zaplanowac rolke, zeby nie wygladala jak placeholder",
    "Kiedy lokalne AI wystarczy, a kiedy warto uzyc chmury",
    "Checklist przed publikacja: audio, napisy, obraz i ryzyko",
)


@dataclass(frozen=True, slots=True)
class TopicBankSuggestion:
    topic: str
    source: str
    risk_level: str


class TopicBank:
    def __init__(self, *, channel_dir: Path) -> None:
        self.channel_dir = channel_dir
        self.topic_bank_path = channel_dir / "topic_bank.json"

    def suggest_topic(
        self,
        *,
        channel: ChannelProfile,
        sequence_number: int,
        existing_topics: set[str],
    ) -> TopicBankSuggestion:
        templates = self._templates()
        existing_normalized = {_normalize(topic) for topic in existing_topics}
        offset = max(0, sequence_number - 1)
        for attempt in range(len(templates) * 2):
            template = templates[(offset + attempt) % len(templates)]
            candidate = self._render_template(template, channel=channel, sequence_number=sequence_number + attempt)
            normalized = _normalize(candidate)
            risk = topic_risk_level(candidate)
            if normalized in existing_normalized or risk == "high":
                continue
            return TopicBankSuggestion(topic=candidate, source="topic_bank", risk_level=risk)

        fallback = f"{channel.niche}: praktyczny workflow numer {sequence_number}"
        while _normalize(fallback) in existing_normalized:
            sequence_number += 1
            fallback = f"{channel.niche}: praktyczny workflow numer {sequence_number}"
        return TopicBankSuggestion(topic=fallback, source="fallback", risk_level=topic_risk_level(fallback))

    def _templates(self) -> tuple[str, ...]:
        if not self.topic_bank_path.exists():
            self._write_default_bank()
            return DEFAULT_TOPIC_TEMPLATES
        try:
            raw = json.loads(self.topic_bank_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return DEFAULT_TOPIC_TEMPLATES
        templates = raw.get("templates") if isinstance(raw, dict) else None
        if not isinstance(templates, list):
            return DEFAULT_TOPIC_TEMPLATES
        cleaned = tuple(str(item).strip() for item in templates if str(item).strip())
        return cleaned or DEFAULT_TOPIC_TEMPLATES

    def _write_default_bank(self) -> None:
        self.topic_bank_path.parent.mkdir(parents=True, exist_ok=True)
        self.topic_bank_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "description": "Local deterministic topic bank for ChannelOps. Edit templates to steer the channel.",
                    "templates": list(DEFAULT_TOPIC_TEMPLATES),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _render_template(template: str, *, channel: ChannelProfile, sequence_number: int) -> str:
        return " ".join(
            template.format(
                niche=channel.niche,
                audience=channel.audience,
                language=channel.language,
                number=sequence_number,
            ).split()
        ).strip()


def _normalize(topic: str) -> str:
    return re.sub(r"\s+", " ", topic.casefold()).strip()
