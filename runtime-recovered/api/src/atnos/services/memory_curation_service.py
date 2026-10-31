from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Literal

from atnos.db.models.enums import ProjectMemoryPriority


MemoryDomain = Literal[
    "user_preference",
    "project_goal",
    "task_state",
    "decision",
    "relationship_goal",
    "operational_fact",
    "noise",
    "sensitive",
]
MemoryPriority = Literal["low", "medium", "high", "critical"]


SECRET_PATTERNS = (
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd|haslo|hasło|authorization)\b\s*[:=]"),
    re.compile(r"(?i)\b(sk-[a-z0-9_\-]{12,}|gh[pousr]_[a-z0-9_]{12,}|xox[baprs]-[a-z0-9\-]{12,})\b"),
    re.compile(r"(?i)\b(client_secret|private[_-]?key|recovery[_-]?code|backup[_-]?code)\b"),
)


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    domain: MemoryDomain
    priority: MemoryPriority
    should_remember: bool
    reason: str
    signals: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "domain": self.domain,
            "priority": self.priority,
            "should_remember": self.should_remember,
            "reason": self.reason,
            "signals": list(self.signals),
        }


class MemoryCurationService:
    """Decides what deserves memory and how memory should be used.

    This service is deterministic on purpose. Memory writeback must be
    conservative: Atnos should become more context-aware, not more cluttered.
    """

    MIN_SIGNAL_CHARS = 16
    NOISE_TEXT = {
        "",
        "ok",
        "okej",
        "dobra",
        "tak",
        "nie",
        "dzieki",
        "dzięki",
        "super",
        "kontynuuj",
        "dalej",
    }

    DECISION_SIGNALS = (
        "decyzja",
        "ustalamy",
        "wybieramy",
        "zostajemy przy",
        "idziemy w",
        "nie robimy",
        "odrzucamy",
        "priorytet",
    )
    PREFERENCE_SIGNALS = (
        "wole",
        "preferuje",
        "pamietaj",
        "zapamietaj",
        "od teraz",
        "na przyszlosc",
        "nie chce",
        "chce zeby",
        "ma byc",
        "pisz",
        "odpowiadaj",
        "moj styl",
        "domyslnie",
        "zawsze",
        "nigdy",
    )
    PROJECT_SIGNALS = (
        "projekt",
        "atnos ma",
        "budujemy",
        "mvp",
        "cel produktu",
        "super-agent",
        "super agent",
        "architektura",
        "ux",
        "ui",
        "youtube",
        "short",
        "kanal",
        "kanał",
    )
    TASK_SIGNALS = (
        "do zrobienia",
        "nastepny krok",
        "kolejny krok",
        "trzeba",
        "musimy",
        "napraw",
        "wdroz",
        "wdroż",
        "sprawdz",
        "sprawdź",
    )
    RELATIONSHIP_GOAL_SIGNALS = (
        "moim celem",
        "chce zbudowac",
        "chcę zbudować",
        "zalezy mi",
        "zależy mi",
        "pracuje nad",
        "pracuję nad",
        "dla mnie wazne",
        "dla mnie ważne",
    )
    OPERATIONAL_FACT_SIGNALS = (
        "dziala",
        "działa",
        "nie dziala",
        "nie działa",
        "gotowe",
        "wdrozone",
        "wdrożone",
        "testy przeszly",
        "testy przeszły",
        "failed",
        "passed",
    )

    def classify_turn(
        self,
        *,
        user_text: str,
        assistant_text: str | None = None,
    ) -> MemoryCandidate:
        combined = self._normalize(f"{user_text}\n{assistant_text or ''}")
        user_normalized = self._normalize(user_text)

        if self._contains_secret_marker(combined):
            return MemoryCandidate(
                domain="sensitive",
                priority="critical",
                should_remember=False,
                reason="Potential secret or credential; do not persist in memory.",
                signals=("secret",),
            )

        if self._is_noise(user_normalized):
            return MemoryCandidate(
                domain="noise",
                priority="low",
                should_remember=False,
                reason="Short acknowledgement or low-signal turn.",
                signals=(),
            )

        checks: tuple[tuple[MemoryDomain, MemoryPriority, tuple[str, ...], str], ...] = (
            ("decision", "critical", self.DECISION_SIGNALS, "Durable decision or priority."),
            ("user_preference", "high", self.PREFERENCE_SIGNALS, "User preference or communication style."),
            ("relationship_goal", "high", self.RELATIONSHIP_GOAL_SIGNALS, "Durable user goal or personal working context."),
            ("project_goal", "high", self.PROJECT_SIGNALS, "Project/product goal or direction."),
            ("task_state", "medium", self.TASK_SIGNALS, "Action item or near-term task state."),
            ("operational_fact", "medium", self.OPERATIONAL_FACT_SIGNALS, "Runtime or implementation fact."),
        )
        for domain, priority, signals, reason in checks:
            matched = tuple(signal for signal in signals if self._normalize(signal) in combined)
            if matched:
                return MemoryCandidate(
                    domain=domain,
                    priority=priority,
                    should_remember=True,
                    reason=reason,
                    signals=matched[:6],
                )

        return MemoryCandidate(
            domain="noise",
            priority="low",
            should_remember=False,
            reason="No durable preference, decision, goal, task state, or operational fact detected.",
            signals=(),
        )

    @staticmethod
    def build_memory_use_policy_section() -> str:
        return "\n".join(
            [
                "ATNOS_MEMORY_USE_POLICY:",
                "- Use memory as lightweight orientation, not as unquestionable truth.",
                "- Prefer current user message over stale memory when they conflict.",
                "- Treat durable memory as: user preferences, project goals, active decisions, task state, constraints, and important relationships/goals.",
                "- Do not store or repeat secrets, passwords, API keys, tokens, private keys, recovery codes, or bank/card details.",
                "- Do not memorize acknowledgements, emotional noise, temporary debugging output, or random one-off details.",
                "- If memory is relevant, use it silently unless it changes the answer materially; then mention the remembered constraint briefly.",
                "- If memory appears contradictory, ask whether the user wants to update it instead of silently overwriting it.",
                "- Keep retrieved memory compact: only the few facts that change the answer should enter the prompt.",
            ]
        )

    @staticmethod
    def to_project_memory_priority(priority: MemoryPriority) -> ProjectMemoryPriority:
        mapping = {
            "low": ProjectMemoryPriority.LOW,
            "medium": ProjectMemoryPriority.MEDIUM,
            "high": ProjectMemoryPriority.HIGH,
            "critical": ProjectMemoryPriority.CRITICAL,
        }
        return mapping[priority]

    @classmethod
    def should_write_project_memory(
        cls,
        *,
        project_context_applied: bool,
        project_summary: str | None,
        candidate: MemoryCandidate,
    ) -> bool:
        if project_context_applied and candidate.domain != "sensitive":
            return True
        if candidate.should_remember:
            return True
        return not bool((project_summary or "").strip()) and candidate.should_remember

    @classmethod
    def _contains_secret_marker(cls, text: str) -> bool:
        return any(pattern.search(text) for pattern in SECRET_PATTERNS)

    @classmethod
    def _is_noise(cls, text: str) -> bool:
        if text in cls.NOISE_TEXT:
            return True
        if len(text) < cls.MIN_SIGNAL_CHARS and not any(char in text for char in "?!"):
            return True
        return False

    @staticmethod
    def _normalize(value: str) -> str:
        translated = (value or "").lower().translate(str.maketrans({"ł": "l", "Ł": "L"}))
        without_accents = "".join(
            char
            for char in unicodedata.normalize("NFKD", translated)
            if not unicodedata.combining(char)
        )
        return " ".join(without_accents.split())
