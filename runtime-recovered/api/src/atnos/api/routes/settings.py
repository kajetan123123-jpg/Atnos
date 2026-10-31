from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict
from typing import Any, Literal

router = APIRouter(tags=["settings"])

ProviderPostureValue = Literal[
    "prefer-local",
    "improve-when-needed",
    "ask-before-external",
    "strict-local",
]
PersonaProfileValue = Literal["strategist", "critic", "executor", "mentor", "jarvis-mode"]


def _load_settings_repositories():
    from atnos.repositories.execution_policy_repository import ExecutionPolicyRepository
    from atnos.repositories.model_config_repository import ModelConfigRepository
    from atnos.repositories.project_repository import ProjectRepository
    from atnos.repositories.provider_repository import ProviderRepository
    from atnos.repositories.user_repository import UserRepository

    return (
        ExecutionPolicyRepository,
        ModelConfigRepository,
        ProjectRepository,
        ProviderRepository,
        UserRepository,
    )


def _load_provider_posture_resolver_dependencies():
    from atnos.services.provider_posture_resolver_service import (
        ProviderPostureProjectNotFoundError,
        ProviderPostureResolverError,
        ProviderPostureResolverService,
    )

    return (
        ProviderPostureProjectNotFoundError,
        ProviderPostureResolverError,
        ProviderPostureResolverService,
    )


def _load_persona_profile_resolver_dependencies():
    from atnos.services.persona_profile_resolver_service import (
        PersonaProfileProjectNotFoundError,
        PersonaProfileResolverError,
        PersonaProfileResolverService,
    )

    return (
        PersonaProfileProjectNotFoundError,
        PersonaProfileResolverError,
        PersonaProfileResolverService,
    )


class _RuntimeInventoryServiceProxy:
    def __init__(self, *args, **kwargs) -> None:
        from atnos.services.runtime_inventory_service import RuntimeInventoryService as RealService

        self._impl = RealService(*args, **kwargs)

    def snapshot(self, *args, **kwargs):
        return self._impl.snapshot(*args, **kwargs)


RuntimeInventoryService = _RuntimeInventoryServiceProxy


def _load_runtime_inventory_service():
    return RuntimeInventoryService


def _load_settings_enums():
    from atnos.db.models.enums import ModelTask, PersonaProfile, ProviderPosture

    return ModelTask, PersonaProfile, ProviderPosture


def _coerce_provider_posture(value: str | None) -> Any | None:
    if value is None:
        return None
    _, _, ProviderPosture = _load_settings_enums()
    return ProviderPosture(value)


def _coerce_persona_profile(value: str | None) -> Any | None:
    if value is None:
        return None
    _, PersonaProfile, _ = _load_settings_enums()
    return PersonaProfile(value)


def _model_task_general_chat() -> Any:
    ModelTask, _, _ = _load_settings_enums()
    return ModelTask.GENERAL_CHAT


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    enum_value = getattr(value, "value", None)
    return enum_value if enum_value is not None else str(value)


class ProviderPostureResolutionResponse(BaseModel):
    global_default: str
    project_override: str | None = None
    resolved: str
    source: Literal["global_default", "project_override"]
    project_id: int | None = None


class SettingsRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GlobalProviderPostureUpdateRequest(SettingsRequestModel):
    value: ProviderPostureValue


class ProjectProviderPostureUpdateRequest(SettingsRequestModel):
    value: ProviderPostureValue | None = None


class PersonaProfileResolutionResponse(BaseModel):
    global_default: str
    project_override: str | None = None
    resolved: str
    source: Literal["global_default", "project_override"]
    project_id: int | None = None


class GlobalPersonaProfileUpdateRequest(SettingsRequestModel):
    value: PersonaProfileValue


class ProjectPersonaProfileUpdateRequest(SettingsRequestModel):
    value: PersonaProfileValue | None = None


class RuntimeProviderInventoryItemResponse(BaseModel):
    provider_id: int
    provider_key: str
    label: str
    kind: str
    local_first: bool
    is_enabled: bool
    endpoint_url: str | None = None
    has_runtime_adapter: bool
    adapter_capabilities: list[str]
    configured_capabilities: list[str]
    status: Literal[
        "ready",
        "disabled",
        "missing_adapter",
        "capability_mismatch",
        "configuration_missing",
        "unknown",
    ]
    status_reason: str
    health_status: Literal["healthy", "unreachable", "not_supported", "unknown"]
    health_reason: str
    installed_models: list[str]
    health_latency_ms: int | None = None


class RuntimeModelInventoryItemResponse(BaseModel):
    model_id: int
    model_key: str
    provider_key: str
    task: str
    is_enabled: bool
    is_default: bool
    status: Literal[
        "ready",
        "provider_disabled",
        "missing_adapter",
        "capability_mismatch",
        "configuration_missing",
        "disabled",
    ]
    status_reason: str
    runtime_available: bool | None = None
    runtime_model_present: bool | None = None
    configured_runtime_model_name: str | None = None
    resolved_runtime_model_name: str | None = None
    runtime_model_source: Literal["model_config", "env", "heuristic", "unknown"] = "unknown"
    runtime_model_name: str | None = None
    runtime_status_reason: str
    install_suggestion_status: Literal[
        "not_needed",
        "missing_model",
        "runtime_unavailable",
        "runtime_not_configured",
        "unknown_model",
        "not_supported",
    ] = "not_needed"
    install_suggestion_title: str | None = None
    install_suggestion_detail: str | None = None
    install_suggestion_command: str | None = None


class RuntimeTaskInventoryItemResponse(BaseModel):
    task: str
    has_local_default: bool
    has_external_default: bool
    ready: bool
    default_local_model_key: str | None = None
    default_external_model_key: str | None = None


class RuntimeInventorySummaryResponse(BaseModel):
    ready_providers: int
    enabled_models: int
    tasks_ready: int
    has_local_chat: bool
    has_external_fallback: bool


class RuntimeVoiceSttResponse(BaseModel):
    configured_engine: str
    configured_model_path: str | None = None
    configured_device: str
    effective_engine: Literal[
        "browser-speech",
        "whisper-local",
        "faster-whisper-local",
        "unavailable",
    ]
    status: Literal[
        "ready",
        "browser_fallback",
        "not_configured",
        "dependency_missing",
        "model_missing",
        "unavailable",
    ]
    status_reason: str
    dependency_available: bool | None = None
    model_path_exists: bool | None = None


class RuntimeInventoryResponse(BaseModel):
    providers: list[RuntimeProviderInventoryItemResponse]
    models: list[RuntimeModelInventoryItemResponse]
    tasks: list[RuntimeTaskInventoryItemResponse]
    summary: RuntimeInventorySummaryResponse
    voice_stt: RuntimeVoiceSttResponse


class RuntimeModelNameUpdateRequest(SettingsRequestModel):
    runtime_model_name: str | None = None


class RuntimeModelNameUpdateResponse(BaseModel):
    model_id: int
    model_key: str
    runtime_model_name: str | None = None


class LocalChatModelSwitchRequest(SettingsRequestModel):
    runtime_model_name: str


class LocalChatModelSwitchResponse(BaseModel):
    model_id: int
    model_key: str
    runtime_model_name: str


class ProviderEndpointUrlUpdateRequest(SettingsRequestModel):
    endpoint_url: str | None = None


class ProviderEndpointUrlUpdateResponse(BaseModel):
    provider_id: int
    provider_key: str
    endpoint_url: str | None = None


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None

    cleaned = value.strip()
    return cleaned or None


def _is_local_runtime_endpoint_edit_allowed(provider: Any) -> bool:
    if provider.key in {"llamacpp-local", "ollama-local"}:
        return True

    if _enum_value(provider.kind) == "local_llm":
        return True

    return provider.local_first is True and _enum_value(provider.transport) in {
        "local_runtime",
        "openai_compatible",
    }


def _serialize_provider_posture_resolution(
    result,
) -> ProviderPostureResolutionResponse:
    return ProviderPostureResolutionResponse(
        global_default=_enum_value(result.global_default) or "",
        project_override=_enum_value(result.project_override),
        resolved=_enum_value(result.resolved) or "",
        source=result.source,
        project_id=result.project_id,
    )


def _serialize_persona_profile_resolution(
    result,
) -> PersonaProfileResolutionResponse:
    return PersonaProfileResolutionResponse(
        global_default=_enum_value(result.global_default) or "",
        project_override=_enum_value(result.project_override),
        resolved=_enum_value(result.resolved) or "",
        source=result.source,
        project_id=result.project_id,
    )


def _serialize_runtime_inventory(snapshot) -> RuntimeInventoryResponse:
    return RuntimeInventoryResponse(
        providers=[
            RuntimeProviderInventoryItemResponse(
                provider_id=item.provider_id,
                provider_key=item.provider_key,
                label=item.label,
                kind=item.kind,
                local_first=item.local_first,
                is_enabled=item.is_enabled,
                endpoint_url=item.endpoint_url,
                has_runtime_adapter=item.has_runtime_adapter,
                adapter_capabilities=list(item.adapter_capabilities),
                configured_capabilities=list(item.configured_capabilities),
                status=item.status,
                status_reason=item.status_reason,
                health_status=item.health_status,
                health_reason=item.health_reason,
                installed_models=list(item.installed_models),
                health_latency_ms=item.health_latency_ms,
            )
            for item in snapshot.providers
        ],
        models=[
            RuntimeModelInventoryItemResponse(
                model_id=item.model_id,
                model_key=item.model_key,
                provider_key=item.provider_key,
                task=item.task,
                is_enabled=item.is_enabled,
                is_default=item.is_default,
                status=item.status,
                status_reason=item.status_reason,
                runtime_available=item.runtime_available,
                runtime_model_present=item.runtime_model_present,
                configured_runtime_model_name=item.configured_runtime_model_name,
                resolved_runtime_model_name=item.resolved_runtime_model_name,
                runtime_model_source=item.runtime_model_source,
                runtime_model_name=item.runtime_model_name,
                runtime_status_reason=item.runtime_status_reason,
                install_suggestion_status=item.install_suggestion_status,
                install_suggestion_title=item.install_suggestion_title,
                install_suggestion_detail=item.install_suggestion_detail,
                install_suggestion_command=item.install_suggestion_command,
            )
            for item in snapshot.models
        ],
        tasks=[
            RuntimeTaskInventoryItemResponse(
                task=item.task,
                has_local_default=item.has_local_default,
                has_external_default=item.has_external_default,
                ready=item.ready,
                default_local_model_key=item.default_local_model_key,
                default_external_model_key=item.default_external_model_key,
            )
            for item in snapshot.tasks
        ],
        summary=RuntimeInventorySummaryResponse(
            ready_providers=snapshot.summary.ready_providers,
            enabled_models=snapshot.summary.enabled_models,
            tasks_ready=snapshot.summary.tasks_ready,
            has_local_chat=snapshot.summary.has_local_chat,
            has_external_fallback=snapshot.summary.has_external_fallback,
        ),
        voice_stt=RuntimeVoiceSttResponse(
            configured_engine=snapshot.voice_stt.configured_engine,
            configured_model_path=snapshot.voice_stt.configured_model_path,
            configured_device=snapshot.voice_stt.configured_device,
            effective_engine=snapshot.voice_stt.effective_engine,
            status=snapshot.voice_stt.status,
            status_reason=snapshot.voice_stt.status_reason,
            dependency_available=snapshot.voice_stt.dependency_available,
            model_path_exists=snapshot.voice_stt.model_path_exists,
        ),
    )


@router.get("/settings/runtime-inventory", response_model=RuntimeInventoryResponse)
def get_runtime_inventory(
    request: Request,
    include_live_health: bool = Query(default=False),
) -> RuntimeInventoryResponse:
    RuntimeInventoryService = _load_runtime_inventory_service()
    database = request.app.state.database
    provider_registry = request.app.state.providers

    with database.session_scope() as session:
        service = RuntimeInventoryService(
            session,
            provider_registry=provider_registry,
            settings=request.app.state.settings,
        )
        snapshot = service.snapshot(include_live_health=include_live_health)

    return _serialize_runtime_inventory(snapshot)


@router.put(
    "/settings/model-configs/{model_id}/runtime-model-name",
    response_model=RuntimeModelNameUpdateResponse,
)
def update_runtime_model_name(
    request: Request,
    model_id: int,
    payload: RuntimeModelNameUpdateRequest,
) -> RuntimeModelNameUpdateResponse:
    _, ModelConfigRepository, _, _, _ = _load_settings_repositories()
    database = request.app.state.database

    with database.session_scope() as session:
        models = ModelConfigRepository(session)
        model = models.get(model_id)
        if model is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown model_id '{model_id}'.",
            )

        updated = models.update_runtime_model_name(
            model,
            _normalize_optional_text(payload.runtime_model_name),
        )

    return RuntimeModelNameUpdateResponse(
        model_id=updated.id,
        model_key=updated.key,
        runtime_model_name=updated.runtime_model_name,
    )


@router.post(
    "/settings/runtime/local-chat-model",
    response_model=LocalChatModelSwitchResponse,
)
def switch_local_chat_model(
    request: Request,
    payload: LocalChatModelSwitchRequest,
) -> LocalChatModelSwitchResponse:
    RuntimeInventoryService = _load_runtime_inventory_service()
    _, ModelConfigRepository, _, _, _ = _load_settings_repositories()
    database = request.app.state.database
    provider_registry = request.app.state.providers

    with database.session_scope() as session:
        target_model_name = payload.runtime_model_name.strip()
        if not target_model_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="runtime_model_name cannot be empty.",
            )

        runtime_inventory = RuntimeInventoryService(
            session,
            provider_registry=provider_registry,
            settings=request.app.state.settings,
        )
        snapshot = runtime_inventory.snapshot(include_live_health=True)
        ollama_provider = next(
            (item for item in snapshot.providers if item.provider_key == "ollama-local"),
            None,
        )
        if ollama_provider is None or ollama_provider.health_status != "healthy":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Ollama runtime is not healthy enough for local model switching.",
            )

        installed_models = {item.strip() for item in ollama_provider.installed_models if item.strip()}
        if installed_models and target_model_name not in installed_models:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Model '{target_model_name}' is not installed in the Ollama runtime.",
            )

        models = ModelConfigRepository(session)
        local_chat_candidates = [
            model
            for model in models.list_enabled_for_task(_model_task_general_chat())
            if model.provider is not None
            and model.provider.is_enabled
            and (model.provider.key == "ollama-local" or _enum_value(model.provider.kind) == "local_llm")
        ]
        target = next((model for model in local_chat_candidates if model.is_default), None) or (
            local_chat_candidates[0] if local_chat_candidates else None
        )
        if target is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No enabled local general_chat model configuration exists.",
            )
        updated = models.update_runtime_model_name(target, target_model_name)

    return LocalChatModelSwitchResponse(
        model_id=updated.id,
        model_key=updated.key,
        runtime_model_name=target_model_name,
    )


@router.put(
    "/settings/providers/{provider_id}/endpoint-url",
    response_model=ProviderEndpointUrlUpdateResponse,
)
def update_provider_endpoint_url(
    request: Request,
    provider_id: int,
    payload: ProviderEndpointUrlUpdateRequest,
) -> ProviderEndpointUrlUpdateResponse:
    _, _, _, ProviderRepository, _ = _load_settings_repositories()
    database = request.app.state.database

    with database.session_scope() as session:
        providers = ProviderRepository(session)
        provider = providers.get(provider_id)
        if provider is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown provider_id '{provider_id}'.",
            )
        if not _is_local_runtime_endpoint_edit_allowed(provider):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Provider '{provider.key}' does not support local runtime endpoint remediation."
                ),
            )

        updated = providers.update_endpoint_url(
            provider,
            _normalize_optional_text(payload.endpoint_url),
        )

    return ProviderEndpointUrlUpdateResponse(
        provider_id=updated.id,
        provider_key=updated.key,
        endpoint_url=updated.endpoint_url,
    )


@router.get("/settings/provider-posture", response_model=ProviderPostureResolutionResponse)
def get_provider_posture_resolution(
    request: Request,
    project_id: int | None = Query(default=None, gt=0),
) -> ProviderPostureResolutionResponse:
    (
        ProviderPostureProjectNotFoundError,
        ProviderPostureResolverError,
        ProviderPostureResolverService,
    ) = _load_provider_posture_resolver_dependencies()
    database = request.app.state.database

    try:
        with database.session_scope() as session:
            service = ProviderPostureResolverService(session)
            result = service.resolve(project_id=project_id)
    except ProviderPostureProjectNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ProviderPostureResolverError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    return _serialize_provider_posture_resolution(result)


@router.get("/settings/persona-profile", response_model=PersonaProfileResolutionResponse)
def get_persona_profile_resolution(
    request: Request,
    project_id: int | None = Query(default=None, gt=0),
) -> PersonaProfileResolutionResponse:
    (
        PersonaProfileProjectNotFoundError,
        PersonaProfileResolverError,
        PersonaProfileResolverService,
    ) = _load_persona_profile_resolver_dependencies()
    database = request.app.state.database

    try:
        with database.session_scope() as session:
            service = PersonaProfileResolverService(session)
            result = service.resolve(project_id=project_id)
    except PersonaProfileProjectNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PersonaProfileResolverError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    return _serialize_persona_profile_resolution(result)


@router.put("/settings/provider-posture", response_model=ProviderPostureResolutionResponse)
def update_global_provider_posture(
    request: Request,
    payload: GlobalProviderPostureUpdateRequest,
) -> ProviderPostureResolutionResponse:
    (
        ExecutionPolicyRepository,
        _,
        _,
        _,
        UserRepository,
    ) = _load_settings_repositories()
    _, ProviderPostureResolverError, ProviderPostureResolverService = (
        _load_provider_posture_resolver_dependencies()
    )
    database = request.app.state.database

    try:
        with database.session_scope() as session:
            users = UserRepository(session)
            execution_policies = ExecutionPolicyRepository(session)
            resolver = ProviderPostureResolverService(session)

            owner = users.get_owner()
            if owner is None:
                raise ProviderPostureResolverError("Owner user is not configured.")

            execution_policy = execution_policies.get_for_user(owner.id)
            if execution_policy is None:
                raise ProviderPostureResolverError(
                    "Execution policy is not configured for the owner user."
                )

            execution_policies.update_default_provider_posture(
                execution_policy,
                _coerce_provider_posture(payload.value),
            )
            result = resolver.resolve()
    except ProviderPostureResolverError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    return _serialize_provider_posture_resolution(result)


@router.put("/settings/persona-profile", response_model=PersonaProfileResolutionResponse)
def update_global_persona_profile(
    request: Request,
    payload: GlobalPersonaProfileUpdateRequest,
) -> PersonaProfileResolutionResponse:
    (
        ExecutionPolicyRepository,
        _,
        _,
        _,
        UserRepository,
    ) = _load_settings_repositories()
    _, PersonaProfileResolverError, PersonaProfileResolverService = (
        _load_persona_profile_resolver_dependencies()
    )
    database = request.app.state.database

    try:
        with database.session_scope() as session:
            users = UserRepository(session)
            execution_policies = ExecutionPolicyRepository(session)
            resolver = PersonaProfileResolverService(session)

            owner = users.get_owner()
            if owner is None:
                raise PersonaProfileResolverError("Owner user is not configured.")

            execution_policy = execution_policies.get_for_user(owner.id)
            if execution_policy is None:
                raise PersonaProfileResolverError(
                    "Execution policy is not configured for the owner user."
                )

            execution_policies.update_default_persona_profile(
                execution_policy,
                _coerce_persona_profile(payload.value),
            )
            result = resolver.resolve()
    except PersonaProfileResolverError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    return _serialize_persona_profile_resolution(result)


@router.put(
    "/settings/projects/{project_id}/provider-posture",
    response_model=ProviderPostureResolutionResponse,
)
def update_project_provider_posture(
    request: Request,
    project_id: int,
    payload: ProjectProviderPostureUpdateRequest,
) -> ProviderPostureResolutionResponse:
    _, _, ProjectRepository, _, _ = _load_settings_repositories()
    (
        ProviderPostureProjectNotFoundError,
        ProviderPostureResolverError,
        ProviderPostureResolverService,
    ) = _load_provider_posture_resolver_dependencies()
    database = request.app.state.database

    try:
        with database.session_scope() as session:
            projects = ProjectRepository(session)
            resolver = ProviderPostureResolverService(session)

            project = projects.get(project_id)
            if project is None:
                raise ProviderPostureProjectNotFoundError(
                    f"Unknown project_id '{project_id}' for provider posture resolution."
                )

            projects.update_provider_posture(project, _coerce_provider_posture(payload.value))
            result = resolver.resolve(project_id=project_id)
    except ProviderPostureProjectNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ProviderPostureResolverError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    return _serialize_provider_posture_resolution(result)


@router.put(
    "/settings/projects/{project_id}/persona-profile",
    response_model=PersonaProfileResolutionResponse,
)
def update_project_persona_profile(
    request: Request,
    project_id: int,
    payload: ProjectPersonaProfileUpdateRequest,
) -> PersonaProfileResolutionResponse:
    _, _, ProjectRepository, _, _ = _load_settings_repositories()
    (
        PersonaProfileProjectNotFoundError,
        PersonaProfileResolverError,
        PersonaProfileResolverService,
    ) = _load_persona_profile_resolver_dependencies()
    database = request.app.state.database

    try:
        with database.session_scope() as session:
            projects = ProjectRepository(session)
            resolver = PersonaProfileResolverService(session)

            project = projects.get(project_id)
            if project is None:
                raise PersonaProfileProjectNotFoundError(
                    f"Unknown project_id '{project_id}' for persona profile resolution."
                )

            projects.update_persona_profile(project, _coerce_persona_profile(payload.value))
            result = resolver.resolve(project_id=project_id)
    except PersonaProfileProjectNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PersonaProfileResolverError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    return _serialize_persona_profile_resolution(result)
