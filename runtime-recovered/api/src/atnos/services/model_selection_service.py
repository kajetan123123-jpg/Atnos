from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.db.models.enums import ModelTask, ProviderKind, ProviderPosture
from atnos.db.models.execution_policy import ExecutionPolicy
from atnos.db.models.model_config import ModelConfig
from atnos.providers.registry import ProviderRegistry
from atnos.repositories.model_config_repository import ModelConfigRepository
from atnos.services.provider_health_cache_service import ProviderHealthCacheService


class ModelSelectionError(RuntimeError):
    failure_kind = "provider_unavailable"


class ProviderConfigurationSelectionError(ModelSelectionError):
    failure_kind = "provider_configuration"


class ExternalConfirmationRequiredError(ModelSelectionError):
    code = "requires_external_confirmation"


@dataclass(slots=True, frozen=True)
class ModelSelectionResult:
    model_config: ModelConfig
    reason: str
    selection_reason_code: str = "default_local"

    @property
    def provider(self):
        return self.model_config.provider


@dataclass(slots=True, frozen=True)
class BlockedCandidate:
    model_config: ModelConfig
    reason: str
    failure_kind: str


@dataclass(slots=True, frozen=True)
class CandidatePools:
    local_candidates: tuple[ModelConfig, ...]
    external_candidates: tuple[ModelConfig, ...]
    local_blocked: tuple[BlockedCandidate, ...]
    external_blocked: tuple[BlockedCandidate, ...]


class ModelSelectionService:
    DEFAULT_PROVIDER_PRIORITY_ORDER = (
        "openai-cloud",
        "grok-cloud",
        "anthropic-cloud",
        "ollama-local",
    )
    GENERIC_PROVIDER_UNAVAILABLE_MESSAGE = (
        "Brak skonfigurowanego providera do obslugi rozmowy. "
        "Sprawdz lokalny runtime albo skonfiguruj API key."
    )
    EXTERNAL_PROVIDER_CONFIGURATION_MESSAGE = (
        "Wybrany provider zewnetrzny nie jest skonfigurowany. "
        "Dodaj API key albo wroc do lokalnego runtime."
    )

    def __init__(
        self,
        session: Session,
        *,
        provider_registry: ProviderRegistry,
        settings: Settings | None = None,
    ) -> None:
        self.models = ModelConfigRepository(session)
        self.provider_registry = provider_registry
        self.settings = settings
        self.provider_priority_order = (
            tuple(settings.provider_priority_order)
            if settings is not None
            else self.DEFAULT_PROVIDER_PRIORITY_ORDER
        )
        health_ttl_s = (
            settings.provider_health_cache_ttl_s
            if settings is not None
            else 30
        )
        self.provider_health = ProviderHealthCacheService(ttl_s=health_ttl_s)

    def select_for_task(
        self,
        *,
        task: ModelTask,
        execution_policy: ExecutionPolicy,
        provider_posture: ProviderPosture,
        internet_enabled: bool = True,
        force_external: bool = False,
    ) -> ModelSelectionResult:
        candidate_pools = self._build_candidate_pools(task)
        local_candidates = list(candidate_pools.local_candidates)
        external_candidates = list(candidate_pools.external_candidates)
        if not internet_enabled:
            external_candidates = []

        if force_external:
            return self._select_force_external(
                task=task,
                local_candidates=local_candidates,
                external_candidates=external_candidates,
            )

        # `strict-local` is a hard privacy/runtime boundary. It must be evaluated
        # before the convenience default resolver and before any cloud health probe.
        if provider_posture == ProviderPosture.STRICT_LOCAL:
            return self._select_strict_local(
                task=task,
                local_candidates=local_candidates,
                external_candidates=external_candidates,
                external_blocked=candidate_pools.external_blocked,
            )

        autonomous_default = self._select_autonomous_default(
            task=task,
            local_candidates=local_candidates,
            external_candidates=external_candidates,
            internet_enabled=internet_enabled,
        )
        if autonomous_default is not None:
            return autonomous_default

        if provider_posture == ProviderPosture.ASK_BEFORE_EXTERNAL:
            return self._select_ask_before_external(
                task=task,
                execution_policy=execution_policy,
                local_candidates=local_candidates,
                external_candidates=external_candidates,
                external_blocked=candidate_pools.external_blocked,
                internet_enabled=internet_enabled,
                force_external=force_external,
            )

        if provider_posture == ProviderPosture.IMPROVE_WHEN_NEEDED:
            return self._select_improve_when_needed(
                task=task,
                execution_policy=execution_policy,
                local_candidates=local_candidates,
                external_candidates=external_candidates,
                external_blocked=candidate_pools.external_blocked,
                internet_enabled=internet_enabled,
            )

        return self._select_prefer_local(
            task=task,
            execution_policy=execution_policy,
            local_candidates=local_candidates,
            external_candidates=external_candidates,
            external_blocked=candidate_pools.external_blocked,
            internet_enabled=internet_enabled,
        )

    def _sort_candidates(self, candidates: list[ModelConfig]) -> list[ModelConfig]:
        return sorted(
            candidates,
            key=lambda model: (
                self._provider_priority_index(model.provider.key),
                not model.provider.is_builtin,
                model.provider.key,
                model.key,
            ),
        )

    def _provider_priority_index(self, provider_key: str) -> int:
        try:
            return self.provider_priority_order.index(provider_key)
        except ValueError:
            return len(self.provider_priority_order)

    @staticmethod
    def _build_result(
        model: ModelConfig,
        reason: str,
        *,
        selection_reason_code: str = "default_local",
    ) -> ModelSelectionResult:
        return ModelSelectionResult(
            model_config=model,
            reason=reason,
            selection_reason_code=selection_reason_code,
        )

    def _select_force_external(
        self,
        *,
        task: ModelTask,
        local_candidates: list[ModelConfig],
        external_candidates: list[ModelConfig],
    ) -> ModelSelectionResult:
        external = self._first_healthy_candidate(
            self._ordered_cloud_candidates(external_candidates),
            timeout_s=3.0,
        )
        if external is not None:
            return self._build_result(
                external,
                "Selected cloud provider because force_external=true and the provider is configured and healthy.",
                selection_reason_code="force_external",
            )

        local_fallback = self._find_candidate_by_provider(local_candidates, "ollama-local")
        if local_fallback is None:
            local_fallback = self._find_local_chat_fallback(task)
        if local_fallback is not None:
            return self._build_result(
                local_fallback,
                "cloud_unavailable \u2192 fallback: ollama-local",
                selection_reason_code="policy_fallback",
            )

        raise ModelSelectionError(
            f"force_external=true requested task '{task.value}', but no cloud provider or local fallback model is available."
        )

    def _select_autonomous_default(
        self,
        *,
        task: ModelTask,
        local_candidates: list[ModelConfig],
        external_candidates: list[ModelConfig],
        internet_enabled: bool,
    ) -> ModelSelectionResult | None:
        local_default = self._find_candidate_by_provider(local_candidates, "ollama-local")
        if local_default is None:
            local_default = self._find_local_chat_fallback(task)
        if local_default is not None:
            return self._build_result(
                local_default,
                "Selected ollama-local under the default autonomous routing policy. Runtime failures are handled by execution fallback.",
                selection_reason_code="default_local",
            )

        if internet_enabled:
            external = self._first_healthy_candidate(
                self._ordered_cloud_candidates(external_candidates),
                timeout_s=3.0,
            )
            if external is not None:
                return self._build_result(
                    external,
                    "Local runtime is unavailable, so autonomous routing selected the first healthy cloud fallback.",
                    selection_reason_code="local_unavailable",
                )

        if local_default is not None:
            return self._build_result(
                local_default,
                "Local runtime health is unavailable and no healthy cloud fallback exists, so routing keeps the local default path.",
                selection_reason_code="policy_fallback",
            )
        return None

    def build_execution_fallback_chain(
        self,
        *,
        task: ModelTask,
        selected_provider_key: str,
        internet_enabled: bool = True,
        provider_posture: ProviderPosture = ProviderPosture.PREFER_LOCAL,
        probe_health: bool = True,
        allow_external_fallback: bool = True,
    ) -> tuple[ModelSelectionResult, ...]:
        if provider_posture == ProviderPosture.STRICT_LOCAL:
            internet_enabled = False
        if not allow_external_fallback:
            internet_enabled = False
        candidate_pools = self._build_candidate_pools(task)
        candidates: list[ModelConfig] = []
        if internet_enabled:
            candidates.extend(self._ordered_cloud_candidates(list(candidate_pools.external_candidates)))
        candidates.extend(self._sort_candidates(list(candidate_pools.local_candidates)))

        results: list[ModelSelectionResult] = []
        seen: set[str] = {selected_provider_key}
        for candidate in candidates:
            provider_key = candidate.provider.key
            if provider_key in seen:
                continue
            seen.add(provider_key)
            adapter = self.provider_registry.get(provider_key)
            if adapter is None:
                continue
            if candidate.provider is None:
                continue
            if probe_health:
                timeout_s = 2.0 if provider_key == "ollama-local" else 3.0
                if not self._candidate_is_healthy(candidate, timeout_s=timeout_s):
                    continue
                reason = f"Execution fallback selected provider '{provider_key}' after the previous provider failed."
            else:
                reason = (
                    f"Execution fallback candidate '{provider_key}' will be tried only if the previous provider fails."
                )
                if not adapter.configuration_status(provider=candidate.provider).ready:
                    continue
            results.append(
                self._build_result(
                    candidate,
                    reason,
                    selection_reason_code="policy_fallback",
                )
            )
        return tuple(results)

    def _ordered_cloud_candidates(self, candidates: list[ModelConfig]) -> list[ModelConfig]:
        return self._sort_candidates(
            [
                candidate
                for candidate in candidates
                if not self._is_local_provider(candidate)
            ]
        )

    def _first_healthy_candidate(
        self,
        candidates: list[ModelConfig],
        *,
        timeout_s: float,
    ) -> ModelConfig | None:
        for candidate in candidates:
            if self._candidate_is_healthy(candidate, timeout_s=timeout_s):
                return candidate
        return None

    def _candidate_is_healthy(self, candidate: ModelConfig, *, timeout_s: float) -> bool:
        provider = candidate.provider
        if provider is None:
            return False
        adapter = self.provider_registry.get(provider.key)
        if adapter is None:
            return False
        return self.provider_health.is_healthy(
            provider=provider,
            adapter=adapter,
            timeout_s=timeout_s,
        )

    @staticmethod
    def _find_candidate_by_provider(
        candidates: list[ModelConfig],
        provider_key: str,
    ) -> ModelConfig | None:
        for candidate in candidates:
            if candidate.provider is not None and candidate.provider.key == provider_key:
                return candidate
        return None

    @staticmethod
    def _is_local_provider(candidate: ModelConfig) -> bool:
        provider = candidate.provider
        return bool(
            provider is not None
            and (provider.kind == ProviderKind.LOCAL_LLM or provider.local_first)
        )

    def _build_candidate_pools(self, task: ModelTask) -> CandidatePools:
        enabled_models = [
            model
            for model in self.models.list_enabled_for_task(task)
            if model.provider is not None and model.provider.is_enabled and model.is_default
        ]

        local_candidates: list[ModelConfig] = []
        external_candidates: list[ModelConfig] = []
        local_blocked: list[BlockedCandidate] = []
        external_blocked: list[BlockedCandidate] = []

        for model in enabled_models:
            provider = model.provider
            assert provider is not None
            adapter = self.provider_registry.get(provider.key)
            if adapter is None:
                blocked = BlockedCandidate(
                    model_config=model,
                    reason=f"Provider '{provider.key}' has no runtime adapter registered.",
                    failure_kind="provider_unavailable",
                )
            else:
                readiness = adapter.execution_readiness(provider=provider, task=task)
                if readiness.ready:
                    target_list = (
                        local_candidates
                        if provider.kind == ProviderKind.LOCAL_LLM or provider.local_first
                        else external_candidates
                    )
                    target_list.append(model)
                    continue
                blocked = BlockedCandidate(
                    model_config=model,
                    reason=readiness.reason or "Provider is not ready for execution.",
                    failure_kind=readiness.failure_kind or "provider_unavailable",
                )

            target_blocked = (
                local_blocked
                if provider.kind == ProviderKind.LOCAL_LLM or provider.local_first
                else external_blocked
            )
            target_blocked.append(blocked)

        return CandidatePools(
            local_candidates=tuple(self._sort_candidates(local_candidates)),
            external_candidates=tuple(self._sort_candidates(external_candidates)),
            local_blocked=tuple(local_blocked),
            external_blocked=tuple(external_blocked),
        )

    def _find_local_chat_fallback(self, task: ModelTask) -> ModelConfig | None:
        if task not in {
            ModelTask.SYNTHESIS,
            ModelTask.CODING,
            ModelTask.SEARCH_RESEARCH,
        }:
            return None

        fallback_candidates: list[ModelConfig] = []
        for model in self.models.list_enabled_for_task(ModelTask.GENERAL_CHAT):
            provider = model.provider
            if (
                provider is None
                or not model.is_default
                or not model.is_enabled
                or not provider.is_enabled
                or not (provider.kind == ProviderKind.LOCAL_LLM or provider.local_first)
            ):
                continue

            adapter = self.provider_registry.get(provider.key)
            if adapter is None:
                continue

            readiness = adapter.execution_readiness(
                provider=provider,
                task=ModelTask.GENERAL_CHAT,
            )
            if readiness.ready:
                fallback_candidates.append(model)

        sorted_candidates = self._sort_candidates(fallback_candidates)
        return sorted_candidates[0] if sorted_candidates else None

    def _select_prefer_local(
        self,
        *,
        task: ModelTask,
        execution_policy: ExecutionPolicy,
        local_candidates: list[ModelConfig],
        external_candidates: list[ModelConfig],
        external_blocked: tuple[BlockedCandidate, ...],
        internet_enabled: bool,
    ) -> ModelSelectionResult:
        if local_candidates:
            return self._build_result(
                local_candidates[0],
                "Selected local default model because resolved provider posture prefers local execution.",
            )
        local_chat_fallback = self._find_local_chat_fallback(task)
        if local_chat_fallback is not None:
            return self._build_result(
                local_chat_fallback,
                (
                    f"No local default model available for task '{task.value}', "
                    "so resolved provider posture fell back to the local chat model."
                ),
            )
        if execution_policy.allow_external_fallback and external_candidates:
            return self._build_result(
                external_candidates[0],
                "No local default model available, routed to external fallback because resolved provider posture allows fallback.",
            )
        if execution_policy.allow_external_fallback and self._has_provider_configuration_issue(
            external_blocked
        ):
            raise ProviderConfigurationSelectionError(
                self.GENERIC_PROVIDER_UNAVAILABLE_MESSAGE
            )
        if not internet_enabled:
            raise ModelSelectionError(
                f"No enabled local default model available for task '{task.value}', and conversation settings block internet access."
            )
        if external_candidates:
            raise ModelSelectionError(
                f"No enabled local default model available for task '{task.value}', and external fallback is disabled by execution policy."
            )
        raise ModelSelectionError(
            f"No enabled default model available for task '{task.value}' under resolved provider posture '{ProviderPosture.PREFER_LOCAL.value}'."
        )

    def _select_improve_when_needed(
        self,
        *,
        task: ModelTask,
        execution_policy: ExecutionPolicy,
        local_candidates: list[ModelConfig],
        external_candidates: list[ModelConfig],
        external_blocked: tuple[BlockedCandidate, ...],
        internet_enabled: bool,
    ) -> ModelSelectionResult:
        if self._should_prefer_external_for_task(task):
            if internet_enabled and execution_policy.allow_external_fallback and external_candidates:
                return self._build_result(
                    external_candidates[0],
                    f"Selected external default model because resolved provider posture improves execution for task '{task.value}' when needed.",
                )
            if local_candidates:
                return self._build_result(
                    local_candidates[0],
                    (
                        f"Resolved provider posture would allow external execution for task '{task.value}', "
                        "but the configured external path is unavailable, so this turn remains local."
                    ),
                )
            local_chat_fallback = self._find_local_chat_fallback(task)
            if local_chat_fallback is not None:
                return self._build_result(
                    local_chat_fallback,
                    (
                        f"Resolved provider posture would allow external execution for task '{task.value}', "
                        "but the configured external path is unavailable, so this turn fell back to the local chat model."
                    ),
                )
            if execution_policy.allow_external_fallback and self._has_provider_configuration_issue(
                external_blocked
            ):
                raise ProviderConfigurationSelectionError(
                    self.GENERIC_PROVIDER_UNAVAILABLE_MESSAGE
                )
            if not internet_enabled:
                raise ModelSelectionError(
                    f"No enabled local default model available for task '{task.value}', and conversation settings block internet access."
                )
            if external_candidates:
                raise ModelSelectionError(
                    f"No enabled local default model available for task '{task.value}', and resolved provider posture cannot use external execution because fallback is disabled by execution policy."
                )

        if local_candidates:
            return self._build_result(
                local_candidates[0],
                "Selected local default model because resolved provider posture keeps routine execution local.",
            )
        if execution_policy.allow_external_fallback and external_candidates:
            return self._build_result(
                external_candidates[0],
                "No local default model available, routed to external fallback under improve-when-needed posture.",
            )
        if execution_policy.allow_external_fallback and self._has_provider_configuration_issue(
            external_blocked
        ):
            raise ProviderConfigurationSelectionError(
                self.GENERIC_PROVIDER_UNAVAILABLE_MESSAGE
            )
        if not internet_enabled:
            raise ModelSelectionError(
                f"No enabled local default model available for task '{task.value}', and conversation settings block internet access."
            )
        if external_candidates:
            raise ModelSelectionError(
                f"No enabled local default model available for task '{task.value}', and external fallback is disabled by execution policy."
            )
        raise ModelSelectionError(
            f"No enabled default model available for task '{task.value}' under resolved provider posture '{ProviderPosture.IMPROVE_WHEN_NEEDED.value}'."
        )

    def _select_ask_before_external(
        self,
        *,
        task: ModelTask,
        execution_policy: ExecutionPolicy,
        local_candidates: list[ModelConfig],
        external_candidates: list[ModelConfig],
        external_blocked: tuple[BlockedCandidate, ...],
        internet_enabled: bool,
        force_external: bool,
    ) -> ModelSelectionResult:
        if force_external:
            if internet_enabled and execution_policy.allow_external_fallback and external_candidates:
                return self._build_result(
                    external_candidates[0],
                    "Selected external default model after explicit confirmation under ask-before-external posture.",
                )
            if self._has_provider_configuration_issue(external_blocked):
                raise ProviderConfigurationSelectionError(
                    self.EXTERNAL_PROVIDER_CONFIGURATION_MESSAGE
                )
            if not internet_enabled:
                raise ModelSelectionError(
                    f"External execution was requested for task '{task.value}', but conversation settings block internet access."
                )
            if external_candidates:
                raise ModelSelectionError(
                    f"External execution was confirmed for task '{task.value}', but external fallback is disabled by execution policy."
                )

        if local_candidates:
            return self._build_result(
                local_candidates[0],
                "Selected local default model because resolved provider posture requires confirmation before external execution.",
            )
        if not internet_enabled:
            raise ModelSelectionError(
                f"No enabled local default model available for task '{task.value}', and conversation settings block internet access."
            )
        if external_candidates:
            raise ExternalConfirmationRequiredError(
                f"No enabled local default model available for task '{task.value}'. Resolved provider posture '{ProviderPosture.ASK_BEFORE_EXTERNAL.value}' requires confirmation before external execution."
            )
        if self._has_provider_configuration_issue(external_blocked):
            raise ProviderConfigurationSelectionError(
                self.GENERIC_PROVIDER_UNAVAILABLE_MESSAGE
            )
        raise ModelSelectionError(
            f"No enabled local default model available for task '{task.value}' under resolved provider posture '{ProviderPosture.ASK_BEFORE_EXTERNAL.value}'."
        )

    def _select_strict_local(
        self,
        *,
        task: ModelTask,
        local_candidates: list[ModelConfig],
        external_candidates: list[ModelConfig],
        external_blocked: tuple[BlockedCandidate, ...],
    ) -> ModelSelectionResult:
        if local_candidates:
            return self._build_result(
                local_candidates[0],
                "Selected local default model because resolved provider posture blocks external execution.",
            )
        if self._has_provider_configuration_issue(external_blocked):
            raise ProviderConfigurationSelectionError(
                self.GENERIC_PROVIDER_UNAVAILABLE_MESSAGE
            )
        if external_candidates:
            raise ModelSelectionError(
                f"No enabled local default model available for task '{task.value}'. Resolved provider posture '{ProviderPosture.STRICT_LOCAL.value}' blocks external execution."
            )
        raise ModelSelectionError(
            f"No enabled local default model available for task '{task.value}' under resolved provider posture '{ProviderPosture.STRICT_LOCAL.value}'."
        )

    @staticmethod
    def _has_provider_configuration_issue(
        blocked_candidates: tuple[BlockedCandidate, ...],
    ) -> bool:
        return any(
            candidate.failure_kind == "provider_configuration"
            for candidate in blocked_candidates
        )

    @staticmethod
    def _should_prefer_external_for_task(task: ModelTask) -> bool:
        return task in {
            ModelTask.CODING,
            ModelTask.SEARCH_RESEARCH,
            ModelTask.VISION,
            ModelTask.SYNTHESIS,
        }
