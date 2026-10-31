from __future__ import annotations

from pathlib import Path

import pytest

from atnos.core.config import Settings
from atnos.llm.role_registry import (
    ModelCapability,
    ModelRole,
    ModelRoleResolutionError,
    OllamaRoleRegistry,
)
from atnos.services.ollama_model_roles_service import OllamaModelRolesService


@pytest.fixture
def settings() -> Settings:
    return Settings(
        data_dir=Path("E:/Atnos/App/tmp/llm-role-tests"),
        ollama_main_model="qwen3:14b",
        ollama_chat_model="qwen3:8b",
        ollama_fast_model="qwen3:8b",
        ollama_vision_model="qwen3-vl:8b-instruct",
        semantic_knowledge_embedding_model="bge-m3:latest",
    )


def test_role_names_resolve_current_models_without_switching_defaults(settings: Settings) -> None:
    registry = OllamaRoleRegistry(settings)
    assert registry.resolve(ModelRole.CHAT_QUICK).model == "qwen3:8b"
    assert registry.resolve(ModelRole.AGENT_DEEP).model == "qwen3:14b"
    assert registry.resolve(ModelRole.VIDEO_EDIT_PLANNER).model == "qwen3:14b"
    assert registry.resolve(ModelRole.SCREEN_VISION).model == "qwen3-vl:8b-instruct"
    assert registry.resolve(ModelRole.KNOWLEDGE_EMBEDDING).model == "bge-m3:latest"
    assert OllamaModelRolesService(settings).fast_text_model == registry.resolve(ModelRole.AGENT_QUICK).model
    assert OllamaModelRolesService(settings).vision_model == registry.resolve(ModelRole.SCREEN_VISION).model


def test_frozen_order_models_are_required_and_not_replaced_by_new_candidate(settings: Settings) -> None:
    registry = OllamaRoleRegistry(settings)
    with pytest.raises(ModelRoleResolutionError, match="frozen order"):
        registry.resolve(ModelRole.PRODUCTION_SCRIPT_GENERATOR)
    binding = registry.resolve(ModelRole.PRODUCTION_SCRIPT_REVIEWER, frozen_model="qwen3:14b")
    assert binding.model == "qwen3:14b"
    assert binding.source == "frozen_order_profile"


def test_missing_exact_model_fails_closed(settings: Settings) -> None:
    registry = OllamaRoleRegistry(settings)
    with pytest.raises(ModelRoleResolutionError, match="missing local model"):
        registry.resolve(
            ModelRole.SCREEN_VISION,
            installed_models=("qwen3:14b", "qwen3:8b", "qwen3-vl:8b"),
        )


def test_verified_capability_mismatch_fails_closed(settings: Settings) -> None:
    registry = OllamaRoleRegistry(settings)
    with pytest.raises(ModelRoleResolutionError, match="lacks verified capabilities: vision"):
        registry.resolve(
            ModelRole.SCREEN_VISION,
            installed_models=("qwen3-vl:8b-instruct",),
            verified_capabilities={
                "qwen3-vl:8b-instruct": {ModelCapability.TEXT, ModelCapability.TOOLS},
            },
        )
    binding = registry.resolve(
        ModelRole.SCREEN_VISION,
        installed_models=("qwen3-vl:8b-instruct",),
        verified_capabilities={
            "qwen3-vl:8b-instruct": {ModelCapability.TEXT, ModelCapability.VISION},
        },
    )
    assert binding.evidence == "capabilities_verified"


def test_role_registry_rejects_unknown_role(settings: Settings) -> None:
    with pytest.raises(ModelRoleResolutionError, match="Unknown LLM role"):
        OllamaRoleRegistry(settings).resolve("editing")  # type: ignore[arg-type]
