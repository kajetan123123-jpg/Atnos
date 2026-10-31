"""Resolve Atnos' local LLM roles without changing execution or cloud policy.

The resolver is deliberately side-effect free. Callers may pass live model names
and probed capabilities; it never downloads or silently substitutes a model.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Collection, Mapping

from atnos.core.config import Settings


DEFAULT_MAIN_TEXT_MODEL = "qwen3:14b"
DEFAULT_FAST_TEXT_MODEL = "qwen3.5:9b"
DEFAULT_VISION_MODEL = "qwen3-vl:8b-instruct"
DEFAULT_EMBEDDING_MODEL = "bge-m3:latest"


class ModelRole(str, Enum):
    CHAT_QUICK = "chat.quick"
    CHAT_DEEP = "chat.deep"
    AGENT_QUICK = "agent.quick"
    AGENT_DEEP = "agent.deep"
    VIDEO_EDIT_PLANNER = "video.edit_planner"
    SCREEN_VISION = "screen.vision"
    PRODUCTION_SCRIPT_GENERATOR = "production.script.generator"
    PRODUCTION_SCRIPT_REVIEWER = "production.script.reviewer"
    PRODUCTION_VISUAL_QA = "production.visual_qa"
    KNOWLEDGE_EMBEDDING = "knowledge.embedding"


class ModelCapability(str, Enum):
    TEXT = "text"
    TOOLS = "tools"
    VISION = "vision"
    STRUCTURED_JSON = "structured_json"
    EMBEDDING = "embedding"


class ModelRoleResolutionError(ValueError):
    """Required role, installed model, or verified capability is unavailable."""


@dataclass(frozen=True, slots=True)
class RoleBinding:
    role: ModelRole
    provider: str
    model: str
    source: str
    capabilities: frozenset[ModelCapability]
    evidence: str


_ROLE_CAPABILITIES: Mapping[ModelRole, frozenset[ModelCapability]] = {
    ModelRole.CHAT_QUICK: frozenset({ModelCapability.TEXT, ModelCapability.TOOLS}),
    ModelRole.CHAT_DEEP: frozenset({ModelCapability.TEXT, ModelCapability.TOOLS}),
    ModelRole.AGENT_QUICK: frozenset({ModelCapability.TEXT, ModelCapability.TOOLS}),
    ModelRole.AGENT_DEEP: frozenset({ModelCapability.TEXT, ModelCapability.TOOLS}),
    ModelRole.VIDEO_EDIT_PLANNER: frozenset({ModelCapability.TEXT, ModelCapability.STRUCTURED_JSON}),
    ModelRole.SCREEN_VISION: frozenset({ModelCapability.TEXT, ModelCapability.VISION}),
    ModelRole.PRODUCTION_SCRIPT_GENERATOR: frozenset({ModelCapability.TEXT, ModelCapability.STRUCTURED_JSON}),
    ModelRole.PRODUCTION_SCRIPT_REVIEWER: frozenset({ModelCapability.TEXT, ModelCapability.STRUCTURED_JSON}),
    ModelRole.PRODUCTION_VISUAL_QA: frozenset({ModelCapability.TEXT, ModelCapability.VISION, ModelCapability.STRUCTURED_JSON}),
    ModelRole.KNOWLEDGE_EMBEDDING: frozenset({ModelCapability.EMBEDDING}),
}


class OllamaRoleRegistry:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def resolve(
        self,
        role: ModelRole,
        *,
        frozen_model: str | None = None,
        installed_models: Collection[str] | None = None,
        verified_capabilities: Mapping[str, Collection[ModelCapability]] | None = None,
    ) -> RoleBinding:
        if not isinstance(role, ModelRole):
            raise ModelRoleResolutionError(f"Unknown LLM role: {role!r}")
        if role in {ModelRole.PRODUCTION_SCRIPT_GENERATOR, ModelRole.PRODUCTION_SCRIPT_REVIEWER}:
            if not frozen_model or not frozen_model.strip():
                raise ModelRoleResolutionError(f"{role.value} requires the frozen order profile model")
            model, source = frozen_model.strip(), "frozen_order_profile"
        elif role in {ModelRole.CHAT_QUICK, ModelRole.AGENT_QUICK}:
            model = self.settings.ollama_fast_model or DEFAULT_FAST_TEXT_MODEL
            source = "ollama_fast_model" if self.settings.ollama_fast_model else "default_fast"
        elif role in {ModelRole.CHAT_DEEP, ModelRole.AGENT_DEEP, ModelRole.VIDEO_EDIT_PLANNER}:
            model = (
                self.settings.ollama_main_model
                or self.settings.ollama_chat_model
                or self.settings.ollama_orchestration_model
                or DEFAULT_MAIN_TEXT_MODEL
            )
            source = "configured_main" if model != DEFAULT_MAIN_TEXT_MODEL else "default_main"
        elif role in {ModelRole.SCREEN_VISION, ModelRole.PRODUCTION_VISUAL_QA}:
            model = self.settings.ollama_vision_model or DEFAULT_VISION_MODEL
            source = "ollama_vision_model" if self.settings.ollama_vision_model else "default_vision"
        else:
            model = self.settings.semantic_knowledge_embedding_model or DEFAULT_EMBEDDING_MODEL
            source = "semantic_knowledge_embedding_model"

        model = model.strip()
        if not model:
            raise ModelRoleResolutionError(f"{role.value} has an empty runtime model")
        declared = _ROLE_CAPABILITIES[role]
        evidence = "configured_only"
        if installed_models is not None:
            available = {item.strip() for item in installed_models if item.strip()}
            if model not in available:
                raise ModelRoleResolutionError(f"{role.value} requires missing local model '{model}'")
            evidence = "installed"
        if verified_capabilities is not None:
            supported = verified_capabilities.get(model)
            if supported is None:
                raise ModelRoleResolutionError(f"{role.value} has no capability evidence for '{model}'")
            if not declared.issubset(set(supported)):
                missing = sorted(item.value for item in declared - set(supported))
                raise ModelRoleResolutionError(
                    f"{role.value} model '{model}' lacks verified capabilities: {', '.join(missing)}"
                )
            evidence = "capabilities_verified"
        return RoleBinding(
            role=role,
            provider="ollama-local",
            model=model,
            source=source,
            capabilities=declared,
            evidence=evidence,
        )
