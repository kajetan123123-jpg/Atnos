from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from typing import Literal
import unicodedata

from atnos.core.config import Settings
from atnos.services.llm_usage_tracker_service import LlmUsageTrackerService
from atnos.services.ollama_model_roles_service import OllamaModelRolesService


class TaskType(str, Enum):
    TEXT = "text"
    AGENT = "agent"
    PLANNING = "planning"
    CONTENT = "content"
    FAST = "fast"
    VISION = "vision"
    PREMIUM = "premium"


class PrivacyMode(str, Enum):
    STRICT_LOCAL = "strict-local"
    ALLOW_CLOUD = "allow-cloud"
    CLOUD_FIRST = "cloud-first"


ProviderKey = Literal["ollama-local", "gemini-cloud"]


@dataclass(frozen=True, slots=True)
class BackendConfig:
    model: str
    provider: ProviderKey
    endpoint: str
    context_length: int
    supports_tools: bool
    supports_vision: bool
    routing_reason: str


class LLMRouter:
    """Small model-role router for Atnos.

    This intentionally does not execute requests. Existing providers/backends remain responsible
    for execution; this class only decides which configured model role fits a request.
    """

    def __init__(self, settings: Settings, *, usage_tracker: LlmUsageTrackerService | None = None) -> None:
        self.settings = settings
        self.usage_tracker = usage_tracker or LlmUsageTrackerService(settings)

    def detect_task_type(self, message: str, *, has_image: bool = False) -> TaskType:
        if has_image:
            return TaskType.VISION

        normalized = self._normalize_text(message)
        if not normalized:
            return TaskType.FAST

        if self._looks_fast(normalized):
            return TaskType.FAST
        if self._looks_premium(normalized) and self._gemini_configured():
            return TaskType.PREMIUM
        if self._looks_agentic(normalized):
            return TaskType.AGENT
        if self._looks_content(normalized):
            return TaskType.CONTENT
        if self._looks_planning(normalized):
            return TaskType.PLANNING
        return TaskType.TEXT

    def select_backend(
        self,
        task_type: TaskType,
        *,
        privacy_mode: PrivacyMode | str = PrivacyMode.ALLOW_CLOUD,
    ) -> BackendConfig:
        mode = self._normalize_privacy_mode(privacy_mode)
        if task_type == TaskType.VISION:
            return self._ollama_backend(
                model=self._vision_model(),
                supports_tools=False,
                supports_vision=True,
                reason="Vision task routed to local Ollama vision model.",
            )

        if task_type == TaskType.FAST:
            return self._ollama_backend(
                model=self._fast_model(),
                supports_tools=True,
                supports_vision=False,
                reason="Fast task routed to local Ollama fast model.",
            )

        if task_type == TaskType.PREMIUM and mode != PrivacyMode.STRICT_LOCAL:
            gemini_backend = self._gemini_backend_if_allowed()
            if gemini_backend is not None:
                return gemini_backend

        return self._ollama_backend(
            model=self._main_model(),
            supports_tools=True,
            supports_vision=False,
            reason=(
                "Cloud blocked by strict-local privacy; using local main model."
                if task_type == TaskType.PREMIUM and mode == PrivacyMode.STRICT_LOCAL
                else "Task routed to local Ollama main model."
            ),
        )

    def _gemini_backend_if_allowed(self) -> BackendConfig | None:
        if not self._gemini_configured():
            return None
        if self.usage_tracker.check_gemini_limit() == "exceeded":
            return None
        return BackendConfig(
            model=self.settings.gemini_model or "gemini-3.6-flash",
            provider="gemini-cloud",
            endpoint="google-generative-ai",
            context_length=1_000_000,
            supports_tools=True,
            supports_vision=True,
            routing_reason="Premium task routed to Gemini while cloud policy and usage limit allow it.",
        )

    def _ollama_backend(
        self,
        *,
        model: str,
        supports_tools: bool,
        supports_vision: bool,
        reason: str,
    ) -> BackendConfig:
        return BackendConfig(
            model=model,
            provider="ollama-local",
            endpoint=f"{self.settings.ollama_base_url.rstrip('/')}/api/chat",
            context_length=32_768,
            supports_tools=supports_tools,
            supports_vision=supports_vision,
            routing_reason=reason,
        )

    def _main_model(self) -> str:
        return (
            self.settings.ollama_main_model
            or self.settings.ollama_chat_model
            or OllamaModelRolesService.DEFAULT_MAIN_TEXT_MODEL
        )

    def _fast_model(self) -> str:
        return self.settings.ollama_fast_model or OllamaModelRolesService.DEFAULT_FAST_TEXT_MODEL

    def _vision_model(self) -> str:
        return self.settings.ollama_vision_model or OllamaModelRolesService.DEFAULT_VISION_MODEL

    def _gemini_configured(self) -> bool:
        return bool((self.settings.gemini_api_key or os.getenv("GEMINI_API_KEY", "")).strip())

    @staticmethod
    def _normalize_privacy_mode(value: PrivacyMode | str) -> PrivacyMode:
        if isinstance(value, PrivacyMode):
            return value
        normalized = (value or "").strip().casefold().replace("_", "-")
        if normalized in {"strict-local", "strictlocal"}:
            return PrivacyMode.STRICT_LOCAL
        if normalized in {"cloud-first", "cloudfirst"}:
            return PrivacyMode.CLOUD_FIRST
        return PrivacyMode.ALLOW_CLOUD

    @staticmethod
    def _normalize_text(value: str) -> str:
        folded = unicodedata.normalize("NFKD", value or "")
        without_accents = "".join(ch for ch in folded if not unicodedata.combining(ch))
        return without_accents.strip().casefold()

    @staticmethod
    def _looks_fast(normalized: str) -> bool:
        if len(normalized) > 30:
            return False
        return normalized in {"ok", "tak", "nie", "ile", "kiedy", "gdzie"} or normalized.endswith("?")

    @staticmethod
    def _looks_premium(normalized: str) -> bool:
        needles = ("najlepszy", "premium", "wysoka jakosc", "gemini", "najdokladniej")
        return any(needle in normalized for needle in needles)

    @staticmethod
    def _looks_agentic(normalized: str) -> bool:
        needles = ("wykonaj", "uzyj narzedzia", "agent loop", "agentic", "zrob to")
        return any(needle in normalized for needle in needles)

    @staticmethod
    def _looks_content(normalized: str) -> bool:
        needles = ("short", "shorta", "rolka", "reel", "skrypt", "content", "miniatura", "youtube")
        return any(needle in normalized for needle in needles)

    @staticmethod
    def _looks_planning(normalized: str) -> bool:
        needles = ("plan", "strategia", "roadmap", "rozpisz", "zaplanuj")
        return any(needle in normalized for needle in needles)
