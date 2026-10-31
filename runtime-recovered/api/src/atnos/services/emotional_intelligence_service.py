from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
import unicodedata


EmotionalState = Literal[
    "calm",
    "frustrated",
    "tired",
    "overwhelmed",
    "stressed",
    "excited",
    "uncertain",
    "needs_decision",
    "needs_action",
    "blocked",
]
EmotionalIntensity = Literal["low", "medium", "high"]


@dataclass(frozen=True, slots=True)
class EmotionalIntelligenceProfile:
    state: EmotionalState
    intensity: EmotionalIntensity
    user_need: str
    response_posture: str
    do_rules: tuple[str, ...]
    avoid_rules: tuple[str, ...]

    def to_prompt_lines(self) -> list[str]:
        return [
            f"- emotional_state: {self.state}",
            f"- emotional_intensity: {self.intensity}",
            f"- likely_user_need: {self.user_need}",
            f"- response_posture: {self.response_posture}",
            "- do:",
            *[f"  - {rule}" for rule in self.do_rules],
            "- avoid:",
            *[f"  - {rule}" for rule in self.avoid_rules],
        ]


class EmotionalIntelligenceService:
    """Deterministic emotional-read layer for Atnos responses.

    This does not diagnose the user. It only shapes how Atnos communicates:
    lower cognitive load under stress, be concrete under frustration, and avoid
    fake warmth or coaching filler.
    """

    FRUSTRATED = (
        "do cholery",
        "kurwa",
        "wkur",
        "gowno",
        "gownian",
        "tragiczne",
        "tragicznie",
        "bez sensu",
        "dalej nie dziala",
        "wciaz nie",
        "znowu",
        "czemu to",
    )
    TIRED = (
        "jestem zmeczony",
        "zmeczony",
        "zmeczona",
        "nie mam sily",
        "padam",
        "mam dosc",
        "wyczerp",
    )
    OVERWHELMED = (
        "chaos",
        "za duzo",
        "wszystko naraz",
        "nie ogarniam",
        "pogubilem",
        "pogubilam",
        "przytlacza",
        "balagan",
    )
    STRESSED = (
        "stres",
        "spina",
        "boje sie",
        "martwie",
        "presja",
        "ryzyko",
        "pilne",
        "deadline",
    )
    EXCITED = (
        "super",
        "sztos",
        "jedziemy",
        "mega",
        "chce to zrobic",
        "ruszaj",
        "idziemy",
    )
    UNCERTAIN = (
        "nie wiem",
        "nie jestem pewien",
        "nie jestem pewna",
        "moze",
        "chyba",
        "czy powinienem",
        "czy powinnam",
    )
    NEEDS_DECISION = (
        "zdecyduj",
        "wybierz",
        "co najpierw",
        "co mam zrobic",
        "jaka decyzja",
        "ktora opcja",
        "najlepszy krok",
    )
    NEEDS_ACTION = (
        "zrob",
        "napraw",
        "wdroz",
        "dodaj",
        "uruchom",
        "sprawdz",
        "przetestuj",
        "zaaplikuj",
    )
    BLOCKED = (
        "nie moge",
        "blokuje",
        "zablokowane",
        "nie laduje",
        "stuck",
        "failed",
        "error",
        "blad",
    )
    HIGH_INTENSITY = (
        "do cholery",
        "kurwa",
        "natychmiast",
        "pilne",
        "wkur",
        "mam dosc",
        "nie mam sily",
        "tragiczne",
    )

    @classmethod
    def analyze(cls, user_message: str) -> EmotionalIntelligenceProfile:
        text = cls._normalize(user_message)
        state = cls._detect_state(text)
        intensity = cls._detect_intensity(text, state)
        return cls._profile_for(state, intensity)

    @classmethod
    def build_system_section(cls, user_message: str) -> str:
        profile = cls.analyze(user_message)
        return "\n".join(
            [
                "ATNOS_EMOTIONAL_INTELLIGENCE:",
                "Purpose: respond with mature human judgment, not fake warmth.",
                "Never diagnose the user. Use this only to choose tone, structure and friction level.",
                *profile.to_prompt_lines(),
                "",
                "Universal emotional rules:",
                "- Empathy means reducing friction, not adding sweet language.",
                "- If the user is angry, do not mirror aggression and do not defend yourself.",
                "- If the user is overwhelmed, reduce choices and give one next move.",
                "- If the user is tired, shorten the answer and remove optional theory.",
                "- If the user is excited, keep momentum but add one quality/safety guardrail.",
                "- If the user is uncertain, separate known facts, assumptions and the recommended decision.",
                "- If you do not know something, say what is unknown and how to verify it.",
                "- Criticize the idea or execution path, not the user.",
            ]
        )

    @classmethod
    def _detect_state(cls, text: str) -> EmotionalState:
        if cls._contains_any(text, cls.FRUSTRATED):
            return "frustrated"
        if cls._contains_any(text, cls.BLOCKED):
            return "blocked"
        if cls._contains_any(text, cls.TIRED):
            return "tired"
        if cls._contains_any(text, cls.OVERWHELMED):
            return "overwhelmed"
        if cls._contains_any(text, cls.STRESSED):
            return "stressed"
        if cls._contains_any(text, cls.NEEDS_DECISION):
            return "needs_decision"
        if cls._contains_any(text, cls.UNCERTAIN):
            return "uncertain"
        if cls._contains_any(text, cls.EXCITED):
            return "excited"
        if cls._contains_any(text, cls.NEEDS_ACTION):
            return "needs_action"
        return "calm"

    @classmethod
    def _detect_intensity(cls, text: str, state: EmotionalState) -> EmotionalIntensity:
        if cls._contains_any(text, cls.HIGH_INTENSITY):
            return "high"
        if state in {"frustrated", "blocked", "overwhelmed", "stressed"}:
            return "medium"
        return "low"

    @staticmethod
    def _profile_for(state: EmotionalState, intensity: EmotionalIntensity) -> EmotionalIntelligenceProfile:
        profiles: dict[EmotionalState, tuple[str, str, tuple[str, ...], tuple[str, ...]]] = {
            "frustrated": (
                "fast concrete correction",
                "calm, accountable, blunt about the fix; no defensiveness",
                (
                    "name the concrete problem once",
                    "move directly to the fix or diagnostic step",
                    "show evidence after acting",
                ),
                (
                    "no defensiveness",
                    "defending yourself",
                    "apologies without a fix",
                    "long explanation before action",
                    "arguing with the user's emotion",
                ),
            ),
            "blocked": (
                "unblock the next step",
                "operator-like and precise",
                (
                    "state the blocker",
                    "give one recovery path",
                    "mark failed/skipped/passed honestly",
                ),
                (
                    "pretending the blocker is minor",
                    "suggesting many branches at once",
                ),
            ),
            "tired": (
                "low-effort clarity",
                "short, grounding, minimal cognitive load",
                (
                    "compress the answer",
                    "give one next action",
                    "avoid optional theory unless asked",
                ),
                (
                    "motivational speech",
                    "large menus of options",
                    "overexplaining",
                ),
            ),
            "overwhelmed": (
                "reduce chaos",
                "structured and decisive",
                (
                    "group the chaos into 2-3 buckets",
                    "choose the next best step",
                    "hide nonessential detail",
                ),
                (
                    "adding more options",
                    "turning the answer into a taxonomy",
                ),
            ),
            "stressed": (
                "stabilize risk and sequence",
                "calm, factual, risk-aware",
                (
                    "separate urgent from important",
                    "name the real risk",
                    "give a safe first move",
                ),
                (
                    "false reassurance",
                    "pressure language",
                    "unclear promises",
                ),
            ),
            "excited": (
                "preserve momentum with quality control",
                "energetic but disciplined",
                (
                    "turn energy into execution",
                    "add one quality guardrail",
                    "avoid slowing down with unnecessary caveats",
                ),
                (
                    "cheerleading",
                    "agreeing to unsafe shortcuts",
                ),
            ),
            "uncertain": (
                "make uncertainty actionable",
                "patient, clear, decision-oriented",
                (
                    "separate known, unknown and assumption",
                    "recommend a default",
                    "ask only one blocking question if needed",
                ),
                (
                    "vague neutrality",
                    "pretending confidence without evidence",
                ),
            ),
            "needs_decision": (
                "decision support",
                "direct and prioritizing",
                (
                    "make a recommendation",
                    "explain the tradeoff briefly",
                    "state what to do next",
                ),
                (
                    "listing every possible option",
                    "ending without a decision",
                ),
            ),
            "needs_action": (
                "execution",
                "pragmatic and action-first",
                (
                    "act when safe and feasible",
                    "state assumptions briefly",
                    "verify the outcome",
                ),
                (
                    "asking for permission when not needed",
                    "stopping at a plan when execution is safe",
                ),
            ),
            "calm": (
                "normal useful answer",
                "direct, mature, practical",
                (
                    "answer proportionally",
                    "choose structure based on task complexity",
                    "keep truth and evidence discipline",
                ),
                (
                    "generic chatbot filler",
                    "over-personalizing",
                ),
            ),
        }
        user_need, posture, do_rules, avoid_rules = profiles[state]
        return EmotionalIntelligenceProfile(
            state=state,
            intensity=intensity,
            user_need=user_need,
            response_posture=posture,
            do_rules=do_rules,
            avoid_rules=avoid_rules,
        )

    @staticmethod
    def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
        return any(token in text for token in tokens)

    @staticmethod
    def _normalize(value: str) -> str:
        text = (value or "").casefold()
        replacements = str.maketrans(
            {
                "ą": "a",
                "ć": "c",
                "ę": "e",
                "ł": "l",
                "ń": "n",
                "ó": "o",
                "ś": "s",
                "ż": "z",
                "ź": "z",
            }
        )
        text = text.translate(replacements)
        text = "".join(
            char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char)
        )
        return " ".join(text.split())
