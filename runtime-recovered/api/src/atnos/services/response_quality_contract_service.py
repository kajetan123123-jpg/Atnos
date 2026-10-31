from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Literal

from atnos.services.emotional_intelligence_service import EmotionalIntelligenceService


UserIntentKind = Literal[
    "simple_answer",
    "technical_execution",
    "debugging",
    "product_strategy",
    "decision_support",
    "creative_work",
    "emotional_friction",
    "quality_review",
    "unknown",
]
UserEmotion = Literal[
    "calm",
    "frustrated",
    "urgent",
    "uncertain",
    "blocked",
    "tired",
    "overwhelmed",
    "stressed",
    "excited",
    "needs_decision",
    "needs_action",
]
AnswerMode = Literal[
    "concise",
    "diagnose_decide_plan",
    "execute_and_report",
    "debug_and_fix",
    "empathetic_fix",
    "stabilize_and_simplify",
    "analytical_review",
    "creative_direction",
]


@dataclass(frozen=True, slots=True)
class ResponseQualityProfile:
    intent_kind: UserIntentKind
    emotion: UserEmotion
    answer_mode: AnswerMode
    tone: str
    structure: str
    verbosity: str
    ask_policy: str

    def to_prompt_lines(self) -> list[str]:
        return [
            f"- detected_intent: {self.intent_kind}",
            f"- detected_emotion: {self.emotion}",
            f"- answer_mode: {self.answer_mode}",
            f"- tone: {self.tone}",
            f"- structure: {self.structure}",
            f"- verbosity: {self.verbosity}",
            f"- ask_policy: {self.ask_policy}",
        ]


class ResponseQualityContractService:
    """Builds a stable response-quality contract for every provider prompt.

    The classifier is intentionally deterministic. It should not decide truth,
    tools, or capability. It only shapes how Atnos writes: intent, tone,
    structure, length, clarification policy and evidence discipline.
    """

    FRUSTRATION_TOKENS = (
        "do cholery",
        "kurwa",
        "wkur",
        "wkurza",
        "gown",
        "gowno",
        "tragic",
        "bez sensu",
        "nie dziala",
        "dalej nie",
        "wciaz",
        "ciagle",
    )
    URGENCY_TOKENS = ("teraz", "szybko", "pilne", "natychmiast", "od razu", "zaraz")
    UNCERTAINTY_TOKENS = ("nie wiem", "czy powinienem", "czy powinnam", "jak mam", "co powinienem", "co mam", "moze")
    BLOCKED_TOKENS = ("nie moge", "blokuje", "stuck", "zablokowane", "nie laduje")

    TECHNICAL_TOKENS = (
        "kod",
        "repo",
        "test",
        "bug",
        "napraw",
        "wdroz",
        "implement",
        "powershell",
        "python",
        "frontend",
        "backend",
    )
    PRODUCT_TOKENS = (
        "ux",
        "ui",
        "produkt",
        "architektur",
        "strateg",
        "roadmap",
        "plan rozwoju",
        "agent",
    )
    CREATIVE_TOKENS = ("short", "rolk", "film", "grafik", "thumbnail", "scenariusz", "content", "youtube")
    REVIEW_TOKENS = ("audyt", "review", "ocen", "jakość", "jakosc", "walidac", "sprawdz")
    DECISION_TOKENS = ("decyz", "wybierz", "priorytet", "co najpierw", "rekomend")

    @classmethod
    def classify(cls, user_message: str) -> ResponseQualityProfile:
        text = cls._normalize(user_message)
        emotion = cls._detect_emotion(text)
        intent_kind = cls._detect_intent(text, emotion)
        answer_mode = cls._answer_mode(intent_kind, emotion)
        tone, structure, verbosity, ask_policy = cls._presentation_policy(answer_mode, emotion)
        return ResponseQualityProfile(
            intent_kind=intent_kind,
            emotion=emotion,
            answer_mode=answer_mode,
            tone=tone,
            structure=structure,
            verbosity=verbosity,
            ask_policy=ask_policy,
        )

    @classmethod
    def build_system_section(
        cls,
        *,
        user_message: str,
        work_mode: str | None = None,
        response_style: str | None = None,
        quality_profile: str | None = None,
    ) -> str:
        profile = cls.classify(user_message)
        controls = [
            f"- work_mode: {work_mode or 'auto'}",
            f"- response_style: {response_style or 'default'}",
            f"- quality_profile: {quality_profile or 'standard'}",
        ]
        return "\n".join(
            [
                "ATNOS_RESPONSE_QUALITY_STANDARD:",
                "Goal: answer like a strong thinking partner: concrete, mature, logical, emotionally aware, and technically honest.",
                "",
                "Turn diagnosis:",
                *profile.to_prompt_lines(),
                "",
                EmotionalIntelligenceService.build_system_section(user_message),
                "",
                "Global writing rules:",
                "- Start with the useful answer, not with generic acknowledgement.",
                "- Prefer concrete decisions, commands, paths, risks, or next actions over broad commentary.",
                "- Keep structure proportional: simple tasks get 1-2 short paragraphs; complex tasks get clear sections.",
                "- If the user is frustrated, name the concrete issue once, do not defend yourself, then move to the fix.",
                "- Be firm when a requested direction is unsafe, chaotic, or low quality; explain the better path briefly.",
                "- Do not invent runtime facts. If evidence is missing, say what is known, unknown, skipped, failed, or needs configuration.",
                "- For tool work, include evidence such as command result, file path, artifact path, log path, screenshot path, or exact blocker.",
                "- Ask a question only when the missing answer blocks progress or the next action is risky.",
                "- Do not pad endings with generic offers. Close with the next concrete step when useful.",
                "",
                "Preferred structures:",
                "- simple_answer: direct answer -> optional next step.",
                "- technical_execution/debugging: issue -> fix applied or plan -> verification -> remaining risk.",
                "- product_strategy/decision_support: diagnosis -> recommendation -> priorities -> next step.",
                "- emotional_friction: concrete problem -> immediate fix path -> verification.",
                "- creative_work: concept -> structure -> quality bar -> production steps.",
                "- quality_review: findings first -> evidence -> priority fixes.",
                "",
                "Active controls:",
                *controls,
            ]
        )

    @classmethod
    def _detect_emotion(cls, text: str) -> UserEmotion:
        emotional_state = EmotionalIntelligenceService.analyze(text).state
        if emotional_state != "calm":
            return emotional_state
        if cls._contains_any(text, cls.FRUSTRATION_TOKENS):
            return "frustrated"
        if cls._contains_any(text, cls.BLOCKED_TOKENS):
            return "blocked"
        if cls._contains_any(text, cls.URGENCY_TOKENS):
            return "urgent"
        if cls._contains_any(text, cls.UNCERTAINTY_TOKENS):
            return "uncertain"
        return "calm"

    @classmethod
    def _detect_intent(cls, text: str, emotion: UserEmotion) -> UserIntentKind:
        if emotion in {"frustrated", "blocked"}:
            return "emotional_friction"
        if emotion == "needs_decision":
            return "decision_support"
        if cls._contains_any(text, cls.REVIEW_TOKENS):
            return "quality_review"
        if cls._contains_any(text, cls.CREATIVE_TOKENS):
            return "creative_work"
        if cls._contains_any(text, cls.TECHNICAL_TOKENS):
            if any(token in text for token in ("bug", "napraw", "nie dziala", "failed", "error")):
                return "debugging"
            return "technical_execution"
        if cls._contains_any(text, cls.PRODUCT_TOKENS):
            return "product_strategy"
        if cls._contains_any(text, cls.DECISION_TOKENS):
            return "decision_support"
        if len(text.split()) <= 12:
            return "simple_answer"
        return "unknown"

    @staticmethod
    def _answer_mode(intent_kind: UserIntentKind, emotion: UserEmotion) -> AnswerMode:
        if emotion in {"frustrated", "blocked"}:
            return "empathetic_fix"
        if emotion in {"tired", "overwhelmed", "stressed", "uncertain"}:
            return "stabilize_and_simplify"
        mapping: dict[UserIntentKind, AnswerMode] = {
            "simple_answer": "concise",
            "technical_execution": "execute_and_report",
            "debugging": "debug_and_fix",
            "product_strategy": "diagnose_decide_plan",
            "decision_support": "diagnose_decide_plan",
            "creative_work": "creative_direction",
            "quality_review": "analytical_review",
            "emotional_friction": "empathetic_fix",
            "unknown": "diagnose_decide_plan",
        }
        return mapping[intent_kind]

    @staticmethod
    def _presentation_policy(answer_mode: AnswerMode, emotion: UserEmotion) -> tuple[str, str, str, str]:
        ask_policy = "Ask only if the missing answer blocks progress or a risky action needs approval."
        if answer_mode == "concise":
            return (
                "direct, calm, no filler",
                "short answer plus optional next action",
                "short",
                ask_policy,
            )
        if answer_mode == "empathetic_fix":
            return (
                "calm, accountable, concrete; no defensiveness",
                "name issue -> fix path -> verification",
                "medium-short",
                ask_policy,
            )
        if answer_mode == "stabilize_and_simplify":
            return (
                "calm, grounding, low cognitive load; no fake warmth",
                "reduce chaos -> recommend one next action -> state verification path",
                "medium-short",
                ask_policy,
            )
        if answer_mode == "debug_and_fix":
            return (
                "precise, evidence-first",
                "symptom -> cause -> patch -> test result",
                "medium",
                ask_policy,
            )
        if answer_mode == "execute_and_report":
            return (
                "operator-like, practical",
                "action taken -> files/commands -> verification -> next step",
                "medium",
                ask_policy,
            )
        if answer_mode == "creative_direction":
            return (
                "clear creative director, quality-focused",
                "concept -> why it works -> production checklist -> quality gate",
                "medium",
                ask_policy,
            )
        if answer_mode == "analytical_review":
            return (
                "blunt but fair, findings-first",
                "findings -> evidence -> priority fixes -> verdict",
                "medium-long",
                ask_policy,
            )
        return (
            "principal engineer / product architect, direct and mature",
            "diagnosis -> recommendation -> plan -> risks -> next step",
            "medium",
            ask_policy,
        )

    @staticmethod
    def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
        return any(token in text for token in tokens)

    @staticmethod
    def _normalize(value: str) -> str:
        translated = (value or "").lower().translate(str.maketrans({"ł": "l", "Ł": "L"}))
        without_accents = "".join(
            char
            for char in unicodedata.normalize("NFKD", translated)
            if not unicodedata.combining(char)
        )
        return " ".join(without_accents.split())
