from __future__ import annotations

import os
from dataclasses import dataclass
import importlib.util
from pathlib import Path
from typing import Literal

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.db.models.enums import ModelTask
from atnos.db.models.model_config import ModelConfig
from atnos.db.models.provider import Provider
from atnos.providers.base import (
    ProviderCapability,
    ProviderHealthCheck,
    required_capability_for_task,
)
from atnos.providers.registry import ProviderRegistry
from atnos.repositories.model_config_repository import ModelConfigRepository
from atnos.repositories.provider_repository import ProviderRepository
from atnos.services.runtime_settings_service import RuntimeSettingsService

ProviderStatus = Literal[
    "ready",
    "disabled",
    "missing_adapter",
    "capability_mismatch",
    "configuration_missing",
    "unknown",
]
ModelStatus = Literal[
    "ready",
    "provider_disabled",
    "missing_adapter",
    "capability_mismatch",
    "configuration_missing",
    "disabled",
]
ProviderHealthStatus = Literal["healthy", "unreachable", "not_supported", "unknown"]
RuntimeModelSource = Literal["model_config", "env", "heuristic", "unknown"]
RuntimeInstallSuggestionStatus = Literal[
    "not_needed",
    "missing_model",
    "runtime_unavailable",
    "runtime_not_configured",
    "unknown_model",
    "not_supported",
]
VoiceSttEffectiveEngine = Literal[
    "browser-speech",
    "whisper-local",
    "faster-whisper-local",
    "unavailable",
]
VoiceSttStatus = Literal[
    "ready",
    "browser_fallback",
    "not_configured",
    "dependency_missing",
    "model_missing",
    "unavailable",
]

DEPRECATED_PROVIDER_KEYS = frozenset({"grok-cloud"})


@dataclass(slots=True, frozen=True)
class RuntimeProviderInventoryItem:
    provider_id: int
    provider_key: str
    label: str
    kind: str
    local_first: bool
    is_enabled: bool
    endpoint_url: str | None
    has_runtime_adapter: bool
    adapter_capabilities: tuple[str, ...]
    configured_capabilities: tuple[str, ...]
    status: ProviderStatus
    status_reason: str
    health_status: ProviderHealthStatus
    health_reason: str
    installed_models: tuple[str, ...]
    health_latency_ms: int | None


@dataclass(slots=True, frozen=True)
class RuntimeModelInventoryItem:
    model_id: int
    model_key: str
    provider_key: str
    task: str
    is_enabled: bool
    is_default: bool
    status: ModelStatus
    status_reason: str
    runtime_available: bool | None
    runtime_model_present: bool | None
    configured_runtime_model_name: str | None
    resolved_runtime_model_name: str | None
    runtime_model_source: RuntimeModelSource
    runtime_model_name: str | None
    runtime_status_reason: str
    install_suggestion_status: RuntimeInstallSuggestionStatus
    install_suggestion_title: str | None
    install_suggestion_detail: str | None
    install_suggestion_command: str | None


@dataclass(slots=True, frozen=True)
class RuntimeModelResolution:
    configured_runtime_model_name: str | None
    resolved_runtime_model_name: str | None
    runtime_model_source: RuntimeModelSource


@dataclass(slots=True, frozen=True)
class RuntimeInstallSuggestion:
    status: RuntimeInstallSuggestionStatus
    title: str | None
    detail: str | None
    command: str | None


@dataclass(slots=True, frozen=True)
class RuntimeTaskInventoryItem:
    task: str
    has_local_default: bool
    has_external_default: bool
    ready: bool
    default_local_model_key: str | None
    default_external_model_key: str | None


@dataclass(slots=True, frozen=True)
class RuntimeInventorySummary:
    ready_providers: int
    enabled_models: int
    tasks_ready: int
    has_local_chat: bool
    has_external_fallback: bool


@dataclass(slots=True, frozen=True)
class RuntimeVoiceSttInventoryItem:
    configured_engine: str
    configured_model_path: str | None
    configured_device: str
    effective_engine: VoiceSttEffectiveEngine
    status: VoiceSttStatus
    status_reason: str
    dependency_available: bool | None
    model_path_exists: bool | None


@dataclass(slots=True, frozen=True)
class RuntimeInventorySnapshot:
    providers: tuple[RuntimeProviderInventoryItem, ...]
    models: tuple[RuntimeModelInventoryItem, ...]
    tasks: tuple[RuntimeTaskInventoryItem, ...]
    summary: RuntimeInventorySummary
    voice_stt: RuntimeVoiceSttInventoryItem


class RuntimeInventoryService:
    def __init__(
        self,
        session: Session,
        *,
        provider_registry: ProviderRegistry,
        settings: Settings,
    ) -> None:
        self.providers = ProviderRepository(session)
        self.models = ModelConfigRepository(session)
        self.provider_registry = provider_registry
        self.settings = RuntimeSettingsService(session, settings).build_effective_settings()

    def snapshot(self, *, include_live_health: bool = False) -> RuntimeInventorySnapshot:
        all_providers = self.providers.list_all()
        deprecated_provider_ids = {
            provider.id for provider in all_providers if provider.key in DEPRECATED_PROVIDER_KEYS
        }
        providers = [
            provider for provider in all_providers if provider.key not in DEPRECATED_PROVIDER_KEYS
        ]
        models = [
            model
            for model in self.models.list_all()
            if model.provider_id not in deprecated_provider_ids
            and (
                model.provider is None
                or model.provider.key not in DEPRECATED_PROVIDER_KEYS
            )
        ]
        health_checks = self._collect_provider_health_checks(
            providers,
            include_live_health=include_live_health,
        )
        voice_stt = self._build_voice_stt_item()

        provider_items = tuple(
            self._build_provider_item(
                provider,
                models,
                health_check=health_checks.get(provider.key),
                include_live_health=include_live_health,
            )
            for provider in providers
        )
        model_items = tuple(
            self._build_model_item(
                model,
                health_check=health_checks.get(model.provider.key)
                if model.provider is not None
                else None,
                include_live_health=include_live_health,
            )
            for model in models
        )
        task_items = tuple(
            self._build_task_item(task, model_items, models, voice_stt=voice_stt)
            for task in ModelTask
        )

        general_chat_task = next(
            (item for item in task_items if item.task == ModelTask.GENERAL_CHAT.value),
            None,
        )

        return RuntimeInventorySnapshot(
            providers=provider_items,
            models=model_items,
            tasks=task_items,
            summary=RuntimeInventorySummary(
                ready_providers=sum(1 for item in provider_items if item.status == "ready"),
                enabled_models=sum(1 for item in model_items if item.is_enabled),
                tasks_ready=sum(1 for item in task_items if item.ready),
                has_local_chat=bool(general_chat_task and general_chat_task.has_local_default),
                has_external_fallback=bool(
                    general_chat_task and general_chat_task.has_external_default
                ),
            ),
            voice_stt=voice_stt,
        )

    def _build_voice_stt_item(self) -> RuntimeVoiceSttInventoryItem:
        configured_engine = self.settings.voice_stt_engine
        configured_model_path = self._normalize_optional_model_name(
            self.settings.voice_stt_model_path
        )
        configured_device = self.settings.voice_stt_device

        if configured_engine == "browser-speech":
            return RuntimeVoiceSttInventoryItem(
                configured_engine=configured_engine,
                configured_model_path=configured_model_path,
                configured_device=configured_device,
                effective_engine="browser-speech",
                status="browser_fallback",
                status_reason=(
                    "Local STT is not configured. Browser speech fallback is available "
                    "when transcript_hint is provided."
                ),
                dependency_available=None,
                model_path_exists=None,
            )

        dependency_available = self._voice_stt_dependency_available(configured_engine)

        if configured_model_path is None:
            return RuntimeVoiceSttInventoryItem(
                configured_engine=configured_engine,
                configured_model_path=None,
                configured_device=configured_device,
                effective_engine="browser-speech",
                status="not_configured",
                status_reason=(
                    "Local STT engine is selected, but no voice_stt_model_path is configured. "
                    "Browser speech fallback remains the only available path."
                ),
                dependency_available=dependency_available,
                model_path_exists=None,
            )

        model_path_exists = self._voice_stt_model_path_exists(configured_model_path)
        if dependency_available is False:
            return RuntimeVoiceSttInventoryItem(
                configured_engine=configured_engine,
                configured_model_path=configured_model_path,
                configured_device=configured_device,
                effective_engine="unavailable",
                status="dependency_missing",
                status_reason=(
                    f"Configured local STT engine '{configured_engine}' is missing its Python dependency."
                ),
                dependency_available=False,
                model_path_exists=model_path_exists,
            )

        if model_path_exists is False:
            return RuntimeVoiceSttInventoryItem(
                configured_engine=configured_engine,
                configured_model_path=configured_model_path,
                configured_device=configured_device,
                effective_engine="unavailable",
                status="model_missing",
                status_reason=(
                    "Configured voice_stt_model_path looks like a filesystem path, but it does not exist."
                ),
                dependency_available=dependency_available,
                model_path_exists=False,
            )

        return RuntimeVoiceSttInventoryItem(
            configured_engine=configured_engine,
            configured_model_path=configured_model_path,
            configured_device=configured_device,
            effective_engine=configured_engine,
            status="ready",
            status_reason=(
                "Local STT configuration looks ready. This readiness check does not load the model."
            ),
            dependency_available=dependency_available,
            model_path_exists=model_path_exists,
        )

    def _build_provider_item(
        self,
        provider: Provider,
        models: list[ModelConfig],
        *,
        health_check: ProviderHealthCheck | None,
        include_live_health: bool,
    ) -> RuntimeProviderInventoryItem:
        adapter = self.provider_registry.get(provider.key)
        adapter_capabilities = self._serialize_capabilities(adapter.capabilities if adapter else ())
        configured_capabilities = self._serialize_capabilities(
            self._configured_capabilities_for_provider(provider)
        )

        if not provider.is_enabled:
            status: ProviderStatus = "disabled"
            reason = "Provider is disabled in configuration."
        elif adapter is None:
            status = "missing_adapter"
            reason = "Enabled provider has no runtime adapter registered."
        else:
            missing_capabilities = sorted(
                set(configured_capabilities).difference(adapter_capabilities)
            )
            provider_models = [model for model in models if model.provider_id == provider.id]
            unsupported_model_tasks = sorted(
                {
                    model.task.value
                    for model in provider_models
                    if model.is_enabled and not adapter.supports_task(model.task)
                }
            )
            if missing_capabilities or unsupported_model_tasks:
                status = "capability_mismatch"
                parts: list[str] = []
                if missing_capabilities:
                    parts.append(
                        "Configured capabilities missing in adapter: "
                        + ", ".join(missing_capabilities)
                    )
                if unsupported_model_tasks:
                    parts.append(
                        "Enabled models require unsupported tasks: "
                        + ", ".join(unsupported_model_tasks)
                    )
                reason = ". ".join(parts)
            elif not configured_capabilities and not provider_models:
                status = "unknown"
                reason = "Provider has no configured capabilities and no registered models."
            else:
                readiness = adapter.configuration_status(provider=provider)
                if readiness.ready:
                    status = "ready"
                    reason = "Provider adapter and configured capabilities are aligned."
                else:
                    status = "configuration_missing"
                    reason = readiness.reason or "Provider configuration is incomplete."

        return RuntimeProviderInventoryItem(
            provider_id=provider.id,
            provider_key=provider.key,
            label=provider.label,
            kind=provider.kind.value,
            local_first=provider.local_first,
            is_enabled=provider.is_enabled,
            endpoint_url=provider.endpoint_url,
            has_runtime_adapter=adapter is not None,
            adapter_capabilities=adapter_capabilities,
            configured_capabilities=configured_capabilities,
            status=status,
            status_reason=reason,
            health_status=self._provider_health_status(
                health_check,
                include_live_health=include_live_health,
            ),
            health_reason=self._provider_health_reason(
                provider,
                health_check,
                include_live_health=include_live_health,
            ),
            installed_models=health_check.installed_models if health_check else (),
            health_latency_ms=health_check.latency_ms if health_check else None,
        )

    def _build_model_item(
        self,
        model: ModelConfig,
        *,
        health_check: ProviderHealthCheck | None,
        include_live_health: bool,
    ) -> RuntimeModelInventoryItem:
        provider = model.provider
        if provider is None:
            resolution = RuntimeModelResolution(
                configured_runtime_model_name=self._normalize_optional_model_name(
                    model.runtime_model_name
                ),
                resolved_runtime_model_name=None,
                runtime_model_source="unknown",
            )
            return RuntimeModelInventoryItem(
                model_id=model.id,
                model_key=model.key,
                provider_key="unknown",
                task=model.task.value,
                is_enabled=model.is_enabled,
                is_default=model.is_default,
                status="missing_adapter",
                status_reason="Model has no provider relationship loaded.",
                runtime_available=None,
                runtime_model_present=None,
                configured_runtime_model_name=resolution.configured_runtime_model_name,
                resolved_runtime_model_name=resolution.resolved_runtime_model_name,
                runtime_model_source=resolution.runtime_model_source,
                runtime_model_name=resolution.resolved_runtime_model_name,
                runtime_status_reason="Provider relationship is unavailable.",
                install_suggestion_status="not_supported",
                install_suggestion_title=None,
                install_suggestion_detail=None,
                install_suggestion_command=None,
            )

        adapter = self.provider_registry.get(provider.key)
        if not model.is_enabled:
            status: ModelStatus = "disabled"
            reason = "Model is disabled."
        elif not provider.is_enabled:
            status = "provider_disabled"
            reason = "Provider is disabled for this model."
        elif adapter is None:
            status = "missing_adapter"
            reason = "Provider has no runtime adapter registered."
        elif not adapter.supports_task(model.task):
            status = "capability_mismatch"
            required = required_capability_for_task(model.task).value
            reason = f"Provider adapter is missing required capability '{required}'."
        else:
            readiness = adapter.execution_readiness(provider=provider, task=model.task)
            if readiness.ready:
                status = "ready"
                reason = "Model is ready for routing."
            else:
                status = "configuration_missing"
                reason = readiness.reason or "Provider configuration is incomplete for this model."

        resolution = self._resolve_runtime_model_resolution(
            model,
            provider,
            installed_models=health_check.installed_models if health_check else (),
        )

        runtime_available, runtime_model_present, runtime_status_reason = (
            self._resolve_model_runtime_health(
                health_check=health_check,
                include_live_health=include_live_health,
                resolution=resolution,
            )
        )
        install_suggestion = self._build_install_suggestion(
            model,
            provider,
            include_live_health=include_live_health,
            health_check=health_check,
            resolution=resolution,
            runtime_available=runtime_available,
            runtime_model_present=runtime_model_present,
        )

        return RuntimeModelInventoryItem(
            model_id=model.id,
            model_key=model.key,
            provider_key=provider.key,
            task=model.task.value,
            is_enabled=model.is_enabled,
            is_default=model.is_default,
            status=status,
            status_reason=reason,
            runtime_available=runtime_available,
            runtime_model_present=runtime_model_present,
            configured_runtime_model_name=resolution.configured_runtime_model_name,
            resolved_runtime_model_name=resolution.resolved_runtime_model_name,
            runtime_model_source=resolution.runtime_model_source,
            runtime_model_name=resolution.resolved_runtime_model_name,
            runtime_status_reason=runtime_status_reason,
            install_suggestion_status=install_suggestion.status,
            install_suggestion_title=install_suggestion.title,
            install_suggestion_detail=install_suggestion.detail,
            install_suggestion_command=install_suggestion.command,
        )

    def _build_task_item(
        self,
        task: ModelTask,
        model_items: tuple[RuntimeModelInventoryItem, ...],
        models: list[ModelConfig],
        *,
        voice_stt: RuntimeVoiceSttInventoryItem,
    ) -> RuntimeTaskInventoryItem:
        if task == ModelTask.TRANSCRIPTION:
            has_transcription_default = voice_stt.status in {"ready", "browser_fallback"}
            return RuntimeTaskInventoryItem(
                task=task.value,
                has_local_default=has_transcription_default,
                has_external_default=False,
                ready=has_transcription_default,
                default_local_model_key=(
                    f"voice-stt/{voice_stt.effective_engine}"
                    if has_transcription_default
                    else None
                ),
                default_external_model_key=None,
            )

        ready_models_by_key = {
            item.model_key: item
            for item in model_items
            if item.task == task.value and item.status == "ready"
        }
        task_models = [
            model
            for model in models
            if model.task == task and model.is_default and model.key in ready_models_by_key
        ]

        local_model = next(
            (
                model
                for model in task_models
                if model.provider is not None
                and (model.provider.local_first or model.provider.kind.value == "local_llm")
            ),
            None,
        )
        external_model = next(
            (
                model
                for model in task_models
                if model.provider is not None
                and not (model.provider.local_first or model.provider.kind.value == "local_llm")
            ),
            None,
        )

        return RuntimeTaskInventoryItem(
            task=task.value,
            has_local_default=local_model is not None,
            has_external_default=external_model is not None,
            ready=local_model is not None or external_model is not None,
            default_local_model_key=local_model.key if local_model else None,
            default_external_model_key=external_model.key if external_model else None,
        )

    @staticmethod
    def _configured_capabilities_for_provider(provider: Provider) -> tuple[ProviderCapability, ...]:
        capabilities: list[ProviderCapability] = []
        if provider.supports_chat:
            capabilities.append(ProviderCapability.CHAT)
        if provider.supports_vision:
            capabilities.append(ProviderCapability.VISION)
        if provider.supports_audio_in:
            capabilities.append(ProviderCapability.AUDIO_IN)
        if provider.supports_audio_out:
            capabilities.append(ProviderCapability.AUDIO_OUT)
        if provider.supports_image_generation:
            capabilities.append(ProviderCapability.IMAGE_GENERATION)
        return tuple(capabilities)

    @staticmethod
    def _serialize_capabilities(
        capabilities: tuple[ProviderCapability, ...] | tuple[str, ...],
    ) -> tuple[str, ...]:
        return tuple(sorted(capability.value if isinstance(capability, ProviderCapability) else capability for capability in capabilities))

    def _collect_provider_health_checks(
        self,
        providers: list[Provider],
        *,
        include_live_health: bool,
    ) -> dict[str, ProviderHealthCheck]:
        if not include_live_health:
            return {}

        checks: dict[str, ProviderHealthCheck] = {}
        for provider in providers:
            adapter = self.provider_registry.get(provider.key)
            if adapter is None:
                checks[provider.key] = ProviderHealthCheck(
                    status="unknown",
                    status_reason="Provider has no runtime adapter to health check.",
                )
                continue

            try:
                checks[provider.key] = adapter.check_health(provider=provider)
            except Exception as exc:  # pragma: no cover - defensive boundary
                checks[provider.key] = ProviderHealthCheck(
                    status="unknown",
                    status_reason=f"Health check failed unexpectedly: {exc}",
                )
        return checks

    @staticmethod
    def _provider_health_status(
        health_check: ProviderHealthCheck | None,
        *,
        include_live_health: bool,
    ) -> ProviderHealthStatus:
        if not include_live_health or health_check is None:
            return "unknown"
        return health_check.status

    @staticmethod
    def _provider_health_reason(
        provider: Provider,
        health_check: ProviderHealthCheck | None,
        *,
        include_live_health: bool,
    ) -> str:
        if not include_live_health:
            return "Live health check not requested."
        if health_check is None:
            return f"Provider '{provider.key}' has no live health result."
        return health_check.status_reason

    def _resolve_model_runtime_health(
        self,
        *,
        health_check: ProviderHealthCheck | None,
        include_live_health: bool,
        resolution: RuntimeModelResolution,
    ) -> tuple[bool | None, bool | None, str]:
        if not include_live_health:
            return (None, None, "Live health check not requested.")

        if health_check is None:
            return (None, None, "No live provider health result is available.")

        if health_check.status == "unreachable":
            return (False, None, "Provider runtime is unavailable.")

        if health_check.status == "not_supported":
            return (None, None, "Provider adapter does not support live health.")

        if health_check.status != "healthy":
            return (None, None, health_check.status_reason)

        runtime_model_name = resolution.resolved_runtime_model_name
        if runtime_model_name is None:
            return (
                True,
                None,
                "Model presence is unknown: no reliable runtime alias could be inferred.",
            )

        runtime_model_present = self._runtime_model_is_present(
            runtime_model_name,
            health_check.installed_models,
        )
        return (
            True,
            runtime_model_present,
            "Matching runtime model was detected."
            if runtime_model_present
            else "Configured runtime model was not found in the local runtime.",
        )

    def _build_install_suggestion(
        self,
        model: ModelConfig,
        provider: Provider,
        *,
        include_live_health: bool,
        health_check: ProviderHealthCheck | None,
        resolution: RuntimeModelResolution,
        runtime_available: bool | None,
        runtime_model_present: bool | None,
    ) -> RuntimeInstallSuggestion:
        if not include_live_health:
            return RuntimeInstallSuggestion(
                status="not_needed",
                title=None,
                detail=None,
                command=None,
            )

        if health_check is None:
            return RuntimeInstallSuggestion(
                status="not_supported",
                title=None,
                detail=None,
                command=None,
            )

        if health_check.status == "not_supported":
            return RuntimeInstallSuggestion(
                status="not_supported",
                title=None,
                detail="Health check niewspierany dla tego providera.",
                command=None,
            )

        if health_check.status == "unreachable" or runtime_available is False:
            return RuntimeInstallSuggestion(
                status="runtime_unavailable",
                title="Runtime niedostepny",
                detail=health_check.status_reason,
                command=None,
            )

        if (
            provider.key == "llamacpp-local"
            and health_check.status == "unknown"
            and health_check.status_reason == "No endpoint URL configured for llama.cpp runtime."
        ):
            return RuntimeInstallSuggestion(
                status="runtime_not_configured",
                title="Nie skonfigurowano endpointu",
                detail="Ustaw endpoint llama.cpp, np. http://127.0.0.1:8080.",
                command=None,
            )

        if health_check.status != "healthy":
            return RuntimeInstallSuggestion(
                status="not_supported",
                title=None,
                detail=health_check.status_reason,
                command=None,
            )

        if resolution.resolved_runtime_model_name is None:
            return RuntimeInstallSuggestion(
                status="unknown_model",
                title="Nie wiadomo, ktorego modelu szukac",
                detail=f"Ustaw runtime_model_name dla modelu {model.key}.",
                command=None,
            )

        if runtime_model_present is True:
            return RuntimeInstallSuggestion(
                status="not_needed",
                title=None,
                detail=None,
                command=None,
            )

        if runtime_model_present is False:
            if provider.key == "ollama-local":
                runtime_model_name = resolution.resolved_runtime_model_name
                return RuntimeInstallSuggestion(
                    status="missing_model",
                    title=f"Brakuje modelu {runtime_model_name}",
                    detail="Runtime Ollamy dziala, ale ten model nie jest lokalnie zainstalowany.",
                    command=f"ollama pull {runtime_model_name}",
                )

            if provider.key == "llamacpp-local":
                return RuntimeInstallSuggestion(
                    status="missing_model",
                    title="Model nie jest wystawiony przez llama-server",
                    detail=(
                        "Uruchom llama-server z wlasciwym modelem albo popraw "
                        "runtime_model_name."
                    ),
                    command=None,
                )

            return RuntimeInstallSuggestion(
                status="missing_model",
                title="Brakuje modelu runtime",
                detail="Resolved runtime model nie zostal znaleziony w dostepnym runtime.",
                command=None,
            )

        return RuntimeInstallSuggestion(
            status="unknown_model",
            title="Nie wiadomo, czy model jest dostepny",
            detail="Brakuje wystarczajacej informacji, zeby potwierdzic obecnosc modelu.",
            command=None,
        )

    def _resolve_runtime_model_resolution(
        self,
        model: ModelConfig,
        provider: Provider,
        *,
        installed_models: tuple[str, ...],
    ) -> RuntimeModelResolution:
        explicit_runtime_model_name = self._normalize_optional_model_name(
            model.runtime_model_name
        )
        if explicit_runtime_model_name is not None:
            return RuntimeModelResolution(
                configured_runtime_model_name=explicit_runtime_model_name,
                resolved_runtime_model_name=explicit_runtime_model_name,
                runtime_model_source="model_config",
            )

        configured_runtime_model_name = self._resolve_configured_runtime_model_name(
            model,
            provider,
        )
        if configured_runtime_model_name is not None:
            return RuntimeModelResolution(
                configured_runtime_model_name=None,
                resolved_runtime_model_name=configured_runtime_model_name,
                runtime_model_source="env",
            )

        installed_by_normalized = {
            self._normalize_model_name(installed_model): installed_model
            for installed_model in installed_models
        }

        for candidate in self._runtime_model_candidates(model):
            normalized_candidate = self._normalize_model_name(candidate)
            exact_match = installed_by_normalized.get(normalized_candidate)
            if exact_match is not None:
                return RuntimeModelResolution(
                    configured_runtime_model_name=None,
                    resolved_runtime_model_name=exact_match,
                    runtime_model_source="heuristic",
                )

            candidate_without_tag = self._strip_model_tag(candidate)
            for installed_model in installed_models:
                if self._strip_model_tag(installed_model) == candidate_without_tag:
                    return RuntimeModelResolution(
                        configured_runtime_model_name=None,
                        resolved_runtime_model_name=installed_model,
                        runtime_model_source="heuristic",
                    )

        return RuntimeModelResolution(
            configured_runtime_model_name=None,
            resolved_runtime_model_name=None,
            runtime_model_source="unknown",
        )

    def _resolve_configured_runtime_model_name(
        self,
        model: ModelConfig,
        provider: Provider,
    ) -> str | None:
        if provider.key == "ollama-local":
            if model.task == ModelTask.ORCHESTRATION:
                for configured_model in (
                    self.settings.ollama_orchestration_model,
                    self.settings.ollama_main_model,
                    self.settings.ollama_chat_model,
                ):
                    normalized_model = RuntimeInventoryService._normalize_optional_model_name(
                        configured_model
                    )
                    if normalized_model is not None:
                        return normalized_model
                return None
            if model.task in {ModelTask.CODING, ModelTask.SEARCH_RESEARCH}:
                for configured_model in (
                    self.settings.ollama_orchestration_model,
                    self.settings.ollama_main_model,
                    self.settings.ollama_chat_model,
                ):
                    normalized_model = RuntimeInventoryService._normalize_optional_model_name(
                        configured_model
                    )
                    if normalized_model is not None:
                        return normalized_model
                return None
            if model.task == ModelTask.VISION:
                configured_model = RuntimeInventoryService._normalize_optional_model_name(
                    self.settings.ollama_vision_model
                )
                if configured_model is not None:
                    return configured_model
                return None
            if model.task == ModelTask.SYNTHESIS:
                for configured_model in (
                    self.settings.ollama_document_model,
                    self.settings.ollama_synthesis_model,
                    self.settings.ollama_main_model,
                    self.settings.ollama_chat_model,
                ):
                    normalized_model = RuntimeInventoryService._normalize_optional_model_name(
                        configured_model
                    )
                    if normalized_model is not None:
                        return normalized_model
                return None
            if model.task == ModelTask.GENERAL_CHAT:
                for configured_model in (
                    self.settings.ollama_chat_model,
                    self.settings.ollama_main_model,
                ):
                    normalized_model = RuntimeInventoryService._normalize_optional_model_name(
                        configured_model
                    )
                    if normalized_model is not None:
                        return normalized_model

        if provider.key == "llamacpp-local":
            if model.task != ModelTask.GENERAL_CHAT:
                return None
            configured_model = RuntimeInventoryService._normalize_optional_model_name(
                os.getenv("LLAMACPP_CHAT_MODEL")
            )
            if configured_model is not None:
                return configured_model

        return None

    @staticmethod
    def _runtime_model_candidates(model: ModelConfig) -> tuple[str, ...]:
        candidates: list[str] = []
        for candidate in (model.key, model.key.split("/")[-1], model.label):
            if isinstance(candidate, str):
                cleaned = candidate.strip()
                if cleaned and cleaned not in candidates:
                    candidates.append(cleaned)
        return tuple(candidates)

    @classmethod
    def _runtime_model_is_present(
        cls,
        runtime_model_name: str,
        installed_models: tuple[str, ...],
    ) -> bool:
        normalized_runtime_name = cls._normalize_model_name(runtime_model_name)
        runtime_name_without_tag = cls._strip_model_tag(runtime_model_name)
        for installed_model in installed_models:
            if cls._normalize_model_name(installed_model) == normalized_runtime_name:
                return True
            if cls._strip_model_tag(installed_model) == runtime_name_without_tag:
                return True
        return False

    @staticmethod
    def _normalize_model_name(value: str) -> str:
        return value.strip().lower()

    @staticmethod
    def _strip_model_tag(value: str) -> str:
        return value.strip().lower().split(":", maxsplit=1)[0]

    @staticmethod
    def _normalize_optional_model_name(value: str | None) -> str | None:
        if value is None:
            return None

        cleaned = value.strip()
        return cleaned or None

    @staticmethod
    def _voice_stt_dependency_available(engine: str) -> bool | None:
        if engine == "faster-whisper-local":
            return importlib.util.find_spec("faster_whisper") is not None
        if engine == "whisper-local":
            return importlib.util.find_spec("whisper") is not None
        return None

    @classmethod
    def _voice_stt_model_path_exists(cls, value: str) -> bool | None:
        if not cls._looks_like_filesystem_path(value):
            return None
        return Path(value).expanduser().exists()

    @staticmethod
    def _looks_like_filesystem_path(value: str) -> bool:
        return (
            "\\" in value
            or "/" in value
            or value.startswith(".")
            or (len(value) > 1 and value[1] == ":")
        )
