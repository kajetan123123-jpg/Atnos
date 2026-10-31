from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

router = APIRouter(tags=["integrations"])
DEFAULT_LIST_LIMIT = 25
MAX_LIST_LIMIT = 50


def _load_integration_services():
    from atnos.services.integration_builder_service import IntegrationBuilderService
    from atnos.services.integration_config_service import IntegrationConfigService
    from atnos.services.integrations_registry import IntegrationsRegistryService

    return IntegrationBuilderService, IntegrationConfigService, IntegrationsRegistryService


def _load_obsidian_export_dependencies():
    from atnos.services.obsidian_export import (
        ObsidianExportConfigurationError,
        ObsidianExportNotFoundError,
        ObsidianExportPathError,
        ObsidianExportService,
    )

    return (
        ObsidianExportConfigurationError,
        ObsidianExportNotFoundError,
        ObsidianExportPathError,
        ObsidianExportService,
    )


def _load_obsidian_read_dependencies():
    from atnos.services.obsidian_read_service import (
        ObsidianReadConfigurationError,
        ObsidianReadNotFoundError,
        ObsidianReadPathError,
        ObsidianReadService,
    )

    return (
        ObsidianReadConfigurationError,
        ObsidianReadNotFoundError,
        ObsidianReadPathError,
        ObsidianReadService,
    )

SECRET_LIKE_FIELDS = frozenset(
    {
        "token",
        "api_key",
        "password",
        "secret",
        "client_secret",
        "private_key",
        "credential",
        "credentials",
    }
)


class SecretFieldRejectingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def reject_secret_like_fields(cls, value):
        if not isinstance(value, dict):
            return value

        for raw_key in value:
            normalized_key = str(raw_key).strip().lower().replace("-", "_")
            if normalized_key in SECRET_LIKE_FIELDS:
                raise ValueError(f"Field '{raw_key}' is not allowed in this request.")
        return value


class IntegrationDescriptorResponse(BaseModel):
    key: str
    label: str
    category: str
    status: str
    capabilities: list[str]
    permission_level: str
    is_available: bool
    is_write_capable: bool
    notes: str
    setup_mode: str
    setup_requirements: list[str]
    can_configure_in_ui: bool
    can_connect_account: bool
    missing_requirements: list[str]
    next_action_label: str
    origin: str
    source_kind: str
    has_adapter: bool
    configured_accounts_count: int


class IntegrationDraftRequest(SecretFieldRejectingModel):
    label: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=80)
    summary: str | None = Field(default=None, max_length=400)
    setup_mode: str = Field(default="oauth_planned", max_length=40)
    requested_capabilities: list[str] = Field(default_factory=list)
    source_kind: str = Field(default="user_defined", max_length=32)


class CustomIntegrationUpdateRequest(SecretFieldRejectingModel):
    label: str | None = Field(default=None, min_length=1, max_length=120)
    category: str | None = Field(default=None, min_length=1, max_length=80)
    summary: str | None = Field(default=None, max_length=400)
    setup_mode: str | None = Field(default=None, max_length=40)
    requested_capabilities: list[str] | None = None
    source_kind: str | None = Field(default=None, max_length=32)
    status: str | None = Field(default=None, pattern="^(planned|not_configured|disabled)$")


class IntegrationDraftPreviewResponse(BaseModel):
    descriptor: IntegrationDescriptorResponse
    manifest: str
    setup_plan: str
    warnings: list[str]


class ObsidianIntegrationConfigRequest(BaseModel):
    vault_path: str | None = Field(default=None, max_length=1024)
    export_enabled: bool = False


class ObsidianIntegrationConfigResponse(BaseModel):
    vault_path: str | None
    export_enabled: bool
    path_exists: bool
    source: str
    warnings: list[str]


class ObsidianExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note_id: int | None = None
    project_id: int | None = None
    title: str | None = Field(default=None, max_length=160)
    filename: str | None = Field(default=None, max_length=160)

    @model_validator(mode="after")
    def validate_single_export_target(self):
        if self.note_id is not None and self.project_id is not None:
            raise ValueError("Provide either note_id or project_id, not both.")
        return self


class ObsidianExportPreviewResponse(BaseModel):
    filename: str
    relative_path: str
    markdown: str
    warnings: list[str]


class ObsidianExportConfirmResponse(BaseModel):
    status: str
    relative_path: str
    bytes_written: int


class ObsidianVaultStatusResponse(BaseModel):
    vault_name: str
    path_configured: bool
    path_exists: bool
    readable: bool
    note_count: int
    warnings: list[str]


class ObsidianNoteListItemResponse(BaseModel):
    title: str
    relative_path: str
    modified_at: str
    size_bytes: int
    tags: list[str]
    excerpt: str


class ObsidianNoteReadResponse(BaseModel):
    title: str
    relative_path: str
    markdown_preview: str
    frontmatter: dict[str, object] | None
    links: list[str]
    context_summary: str


class PublicDataEnabledRequest(SecretFieldRejectingModel):
    enabled: bool


class CapabilityQueryRequest(SecretFieldRejectingModel):
    base: str | None = None
    quote: str | None = None
    country: str | None = None
    indicator: str | None = None
    cik: str | None = None
    title: str | None = None
    query: str | None = None


class ExternalSocialConnectRequest(SecretFieldRejectingModel):
    display_name: str
    credential_ref: str
    account_id: str | None = None
    scopes: list[str] = Field(default_factory=list)


class ExternalSocialTestRequest(SecretFieldRejectingModel):
    account_id: str | None = None


class ExternalProjectAccountRequest(SecretFieldRejectingModel):
    platform_or_account: str | None = None


class ExternalOpenToonzPathRequest(SecretFieldRejectingModel):
    path: str = ""


class WikimediaIdentityRequest(SecretFieldRejectingModel):
    app_name: str = "Atnos"
    contact: str


class RouteDataRequest(SecretFieldRejectingModel):
    intent: str | None = None
    capability: str | None = None
    parameters: dict[str, Any] | None = None
    project_id: str | None = None
    freshness: str | None = None
    preferred_provider: str | None = None
    fallback_allowed: bool = True
    execute: bool = True


class CapabilityExecuteRequest(SecretFieldRejectingModel):
    intent: str | None = None
    capability: str | None = None
    parameters: dict[str, Any] | None = None
    project_id: str | None = None
    record: bool = True


@router.post("/integrations/public-data/wikimedia/identity")
def save_wikimedia_identity(payload: WikimediaIdentityRequest) -> dict:
    from atnos.integrations.public_data import probe_public_data
    from atnos.integrations.public_data import save_wikimedia_identity as save_identity

    saved = save_identity(payload.app_name, payload.contact)
    if saved.get("status") != "CONFIGURED":
        return {
            "status": "REQUIRES_SETUP",
            "detail": "Add a contact email, public URL, or Wikimedia user page before testing the connection.",
        }
    # Persistence alone is not HEALTHY. The status comes from the live check.
    return probe_public_data("wikimedia")


@router.post("/integrations/route")
def route_integration_need(payload: RouteDataRequest) -> dict:
    from atnos.integrations.capability_router import route_data_need
    from atnos.integrations.capability_runtime import classify_task, execute_capability, record_last_execution

    parameters = payload.parameters or {}
    for key in parameters:
        normalized = str(key).strip().lower().replace("-", "_")
        if normalized in SECRET_LIKE_FIELDS:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Field '{key}' is not allowed in this request.")
    # Public-data path first when capability is a data provider capability.
    public = {"FX_RATES", "MACRO_INDICATORS", "SEC_FILINGS", "COMPANY_FILINGS", "PUBLIC_COMPANY_FILINGS", "ENCYCLOPEDIC_KNOWLEDGE", "RESOURCE_DISCOVERY", "WEB_PAGE_EXTRACT"}
    capability = payload.capability
    if not capability and payload.intent:
        capability, _inferred = classify_task(payload.intent)
    if capability in public or (payload.capability in public if payload.capability else False):
        if capability in {"RESOURCE_DISCOVERY", "WEB_PAGE_EXTRACT"} or payload.capability in {
            "RESOURCE_DISCOVERY",
            "WEB_PAGE_EXTRACT",
        }:
            result = execute_capability(
                capability or payload.capability,
                intent=payload.intent,
                parameters=parameters,
                project_id=payload.project_id,
            )
            if payload.execute:
                record_last_execution(None, result)
            return result
        return route_data_need(
            intent=payload.intent,
            capability=payload.capability,
            parameters=parameters,
            project_id=payload.project_id,
            freshness=payload.freshness,
            preferred_provider=payload.preferred_provider,
            fallback_allowed=payload.fallback_allowed,
            execute=payload.execute,
        )
    result = execute_capability(
        payload.capability,
        intent=payload.intent,
        parameters=parameters,
        project_id=payload.project_id,
    )
    if payload.execute:
        record_last_execution(None, result)
    return result


@router.post("/integrations/capabilities/execute")
def execute_integration_capability(request: Request, payload: CapabilityExecuteRequest) -> dict:
    from atnos.integrations.capability_runtime import execute_capability, record_last_execution

    parameters = payload.parameters or {}
    for key in parameters:
        normalized = str(key).strip().lower().replace("-", "_")
        if normalized in SECRET_LIKE_FIELDS:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Field '{key}' is not allowed in this request.")
    database = getattr(request.app.state, "database", None)
    if database is not None:
        with database.session_scope() as session:
            result = execute_capability(
                payload.capability,
                intent=payload.intent,
                parameters=parameters,
                project_id=payload.project_id,
                settings=request.app.state.settings,
                session=session,
            )
    else:
        result = execute_capability(
            payload.capability,
            intent=payload.intent,
            parameters=parameters,
            project_id=payload.project_id,
            settings=getattr(request.app.state, "settings", None),
            session=None,
        )
    if payload.record:
        record_last_execution(None, result)
    return result


@router.get("/integrations/capabilities/executable")
def list_executable_integration_capabilities() -> list[dict]:
    from atnos.integrations.capability_runtime import last_execution, list_executable_capabilities

    rows = list_executable_capabilities()
    last = last_execution()
    return [{**row, "last_execution": last if last and last.get("capability") == row["capability"] else None} for row in rows]


@router.get("/integrations/external-repos")
def list_external_repositories() -> list[dict]:
    from atnos.integrations.external_repositories import list_repositories

    return list_repositories()


@router.get("/integrations/external-repos/social")
def list_external_social_accounts() -> list[dict]:
    from atnos.integrations.external_repositories import list_social_accounts

    return list_social_accounts()


@router.post("/integrations/external-repos/social/{platform_id}/connect")
def connect_external_social(platform_id: str, payload: ExternalSocialConnectRequest) -> dict:
    from atnos.integrations.external_repositories import SOCIAL_PLATFORMS, connect_social_account

    if platform_id not in {item["id"] for item in SOCIAL_PLATFORMS}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nieznana platforma.")
    result = connect_social_account(
        platform_id,
        display_name=payload.display_name,
        credential_ref=payload.credential_ref,
        account_id=payload.account_id,
        scopes=payload.scopes,
    )
    if result.get("status") == "ERROR":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["detail"])
    return result


@router.post("/integrations/external-repos/social/{platform_id}/disconnect")
def disconnect_external_social(
    platform_id: str,
    request: Request,
    account_id: str | None = Query(default=None),
    remove_vault_secret: bool = Query(default=False),
) -> dict:
    from atnos.integrations.external_repositories import (
        SOCIAL_PLATFORMS,
        credential_refs_for_platform,
        disconnect_social_account,
    )

    if platform_id not in {item["id"] for item in SOCIAL_PLATFORMS}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nieznana platforma.")
    refs = credential_refs_for_platform(platform_id, account_id=account_id) if remove_vault_secret else []
    result = disconnect_social_account(platform_id, account_id=account_id)
    if remove_vault_secret and refs:
        from atnos.services.secret_vault_service import SecretVaultService

        vault = SecretVaultService(request.app.state.settings)
        for ref in refs:
            vault.delete_secret(ref)
        result["vault_secrets_removed"] = len(refs)
    return result


@router.post("/integrations/external-repos/social/{platform_id}/test")
def test_external_social(platform_id: str, payload: ExternalSocialTestRequest | None = None) -> dict:
    from atnos.integrations.external_repositories import SOCIAL_PLATFORMS, verify_social_connection

    if platform_id not in {item["id"] for item in SOCIAL_PLATFORMS}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nieznana platforma.")
    account_id = payload.account_id if payload else None
    return verify_social_connection(platform_id, account_id=account_id)


@router.get("/integrations/external-repos/projects")
def list_external_project_accounts() -> dict:
    from atnos.integrations.external_repositories import list_project_accounts

    return list_project_accounts()


@router.post("/integrations/external-repos/projects/{project_id}")
def set_external_project_account(project_id: str, payload: ExternalProjectAccountRequest) -> dict:
    from atnos.integrations.external_repositories import set_project_account

    result = set_project_account(project_id, payload.platform_or_account)
    if result.get("status") == "ERROR":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["detail"])
    return result


@router.get("/integrations/external-repos/skills")
def list_external_skills() -> list[dict]:
    from atnos.integrations.external_repositories import list_skills

    return list_skills()


@router.get("/integrations/external-repos/skills/{skill_id}")
def inspect_external_skill(skill_id: str) -> dict:
    from atnos.integrations.external_repositories import inspect_skill

    result = inspect_skill(skill_id)
    if result.get("status") == "ERROR":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result["detail"])
    return result


@router.post("/integrations/external-repos/skills/{skill_id}/validate")
def validate_external_skill(skill_id: str) -> dict:
    from atnos.integrations.external_repositories import inspect_skill, retention_from_rows

    inspected = inspect_skill(skill_id)
    if inspected.get("status") == "ERROR":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=inspected["detail"])
    skill = inspected["skill"]
    if skill_id == "yt-retention":
        sample = retention_from_rows([])
        return {
            "status": sample["status"],
            "skill_id": skill_id,
            "detail": sample.get("detail"),
            "trust": skill.get("trust"),
            "execution": skill.get("execution"),
        }
    return {
        "status": "OK",
        "skill_id": skill_id,
        "detail": "Manifest jest kompletny. Skill nie jest przypięty do jednego LLM.",
        "trust": skill.get("trust"),
        "provider_locked": skill.get("provider_locked"),
        "capabilities": skill.get("capabilities"),
    }


@router.post("/integrations/external-repos/skills/{skill_id}/enabled")
def set_external_skill_enabled(skill_id: str, payload: PublicDataEnabledRequest) -> dict:
    from atnos.integrations.external_repositories import set_skill_enabled

    result = set_skill_enabled(skill_id, payload.enabled)
    if result.get("status") == "ERROR":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result["detail"])
    return result


@router.post("/integrations/external-repos/awesome/sync")
def sync_external_awesome() -> dict:
    from atnos.integrations.external_repositories import sync_awesome

    return sync_awesome()


@router.get("/integrations/external-repos/awesome/search")
def search_external_awesome(q: str = "") -> list[dict]:
    from atnos.integrations.external_repositories import search_awesome

    return search_awesome(q)


@router.get("/integrations/external-repos/awesome/discover")
def discover_external_resources(q: str = "") -> dict:
    from atnos.integrations.external_repositories import discover_resources

    return discover_resources(q)


@router.get("/integrations/external-repos/opentoonz")
def external_opentoonz_status() -> dict:
    from atnos.integrations.external_repositories import opentoonz_status

    return opentoonz_status()


@router.post("/integrations/external-repos/opentoonz/path")
def set_external_opentoonz_path(payload: ExternalOpenToonzPathRequest) -> dict:
    from atnos.integrations.external_repositories import set_opentoonz_path

    result = set_opentoonz_path(payload.path)
    if result.get("status") == "ERROR":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result["detail"])
    return result


class OpenToonzInstallRequest(SecretFieldRejectingModel):
    confirm_download: bool = False
    confirm_run: bool = False


@router.post("/integrations/external-repos/opentoonz/install")
def install_external_opentoonz(payload: OpenToonzInstallRequest) -> dict:
    from atnos.integrations.external_repositories import prepare_opentoonz_installer, run_opentoonz_installer

    if payload.confirm_run:
        prepared = prepare_opentoonz_installer(confirm_download=False)
        if prepared.get("status") not in {"READY"} and not payload.confirm_download:
            prepared = prepare_opentoonz_installer(confirm_download=True)
        if prepared.get("status") not in {"READY"}:
            return prepared
        return run_opentoonz_installer(confirm_run=True)
    return prepare_opentoonz_installer(confirm_download=payload.confirm_download)


@router.post("/integrations/external-repos/publish/{platform_id}")
def external_social_publish(platform_id: str, account_id: str | None = Query(default=None)) -> dict:
    from atnos.integrations.external_repositories import SOCIAL_PLATFORMS, request_publish

    if platform_id not in {item["id"] for item in SOCIAL_PLATFORMS}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nieznana platforma.")
    return request_publish(platform_id, approved=False, account_id=account_id)


@router.get("/integrations/public-data")
def list_public_data_integrations() -> list[dict]:
    from atnos.integrations.public_data import list_public_data

    return list_public_data()


@router.post("/integrations/public-data/{integration_id}/enabled")
def set_public_data_enabled(integration_id: str, payload: PublicDataEnabledRequest) -> dict:
    from atnos.integrations.public_data import known_ids, set_enabled

    if integration_id not in known_ids():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown public data integration.")
    return set_enabled(integration_id, payload.enabled)


@router.post("/integrations/public-data/{integration_id}/test")
def test_public_data_integration(integration_id: str) -> dict:
    from atnos.integrations.public_data import known_ids, probe_public_data

    if integration_id not in known_ids():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown public data integration.")
    return probe_public_data(integration_id)


@router.post("/integrations/capabilities/{capability}/query")
def query_public_capability(capability: str, payload: CapabilityQueryRequest) -> dict:
    from atnos.integrations.public_data import CAPABILITY_PROVIDER, query_capability

    if capability not in CAPABILITY_PROVIDER:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown capability.")
    return query_capability(capability, payload.model_dump(exclude_none=True))


@router.get("/integrations", response_model=list[IntegrationDescriptorResponse])
def list_integrations(request: Request) -> list[IntegrationDescriptorResponse]:
    _, _, IntegrationsRegistryService = _load_integration_services()
    database = request.app.state.database
    settings = request.app.state.settings

    with database.session_scope() as session:
        service = IntegrationsRegistryService(session, settings)
        return [
            _build_integration_response(descriptor)
            for descriptor in service.list_integrations()
        ]


@router.post("/integrations/draft-preview", response_model=IntegrationDraftPreviewResponse)
def preview_integration_draft(
    request: Request,
    payload: IntegrationDraftRequest,
) -> IntegrationDraftPreviewResponse:
    IntegrationBuilderService, _, _ = _load_integration_services()
    database = request.app.state.database

    try:
        with database.session_scope() as session:
            preview = IntegrationBuilderService(session).build_draft(
                label=payload.label,
                category=payload.category,
                summary=payload.summary,
                setup_mode=payload.setup_mode,
                requested_capabilities=payload.requested_capabilities,
                source_kind=payload.source_kind,
            )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return IntegrationDraftPreviewResponse(
        descriptor=_build_integration_response(preview.descriptor),
        manifest=preview.manifest,
        setup_plan=preview.setup_plan,
        warnings=list(preview.warnings),
    )


@router.post("/integrations/custom", response_model=IntegrationDescriptorResponse, status_code=status.HTTP_201_CREATED)
def create_custom_integration(
    request: Request,
    payload: IntegrationDraftRequest,
) -> IntegrationDescriptorResponse:
    IntegrationBuilderService, _, IntegrationsRegistryService = _load_integration_services()
    database = request.app.state.database
    settings = request.app.state.settings

    try:
        with database.session_scope() as session:
            created = IntegrationBuilderService(session).create_custom_integration(
                label=payload.label,
                category=payload.category,
                summary=payload.summary,
                setup_mode=payload.setup_mode,
                requested_capabilities=payload.requested_capabilities,
                source_kind=payload.source_kind,
            )
            descriptor = IntegrationsRegistryService(session, settings)._custom_descriptor(created, 0)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return _build_integration_response(descriptor)


@router.patch("/integrations/custom/{integration_id}", response_model=IntegrationDescriptorResponse)
def update_custom_integration(
    integration_id: int,
    request: Request,
    payload: CustomIntegrationUpdateRequest,
) -> IntegrationDescriptorResponse:
    IntegrationBuilderService, _, IntegrationsRegistryService = _load_integration_services()
    database = request.app.state.database
    settings = request.app.state.settings

    try:
        with database.session_scope() as session:
            updated = IntegrationBuilderService(session).update_custom_integration(
                integration_id=integration_id,
                label=payload.label,
                category=payload.category,
                summary=payload.summary,
                setup_mode=payload.setup_mode,
                requested_capabilities=payload.requested_capabilities,
                source_kind=payload.source_kind,
                status=payload.status,
            )
            if updated is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Custom integration {integration_id} was not found.",
                )
            descriptor = IntegrationsRegistryService(session, settings)._custom_descriptor(updated, 0)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return _build_integration_response(descriptor)


@router.get("/integrations/obsidian/config", response_model=ObsidianIntegrationConfigResponse)
def get_obsidian_config(request: Request) -> ObsidianIntegrationConfigResponse:
    _, IntegrationConfigService, _ = _load_integration_services()
    database = request.app.state.database
    settings = request.app.state.settings

    with database.session_scope() as session:
        config = IntegrationConfigService(session, settings).get_obsidian_config()

    return _build_obsidian_config_response(config)


@router.patch("/integrations/obsidian/config", response_model=ObsidianIntegrationConfigResponse)
def update_obsidian_config(
    request: Request,
    payload: ObsidianIntegrationConfigRequest,
) -> ObsidianIntegrationConfigResponse:
    _, IntegrationConfigService, _ = _load_integration_services()
    database = request.app.state.database
    settings = request.app.state.settings

    try:
        with database.session_scope() as session:
            config = IntegrationConfigService(session, settings).update_obsidian_config(
                vault_path=payload.vault_path,
                export_enabled=payload.export_enabled,
            )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return _build_obsidian_config_response(config)


@router.get("/integrations/obsidian/status", response_model=ObsidianVaultStatusResponse)
def get_obsidian_status(request: Request) -> ObsidianVaultStatusResponse:
    _, _, _, ObsidianReadService = _load_obsidian_read_dependencies()
    database = request.app.state.database
    settings = request.app.state.settings

    with database.session_scope() as session:
        result = ObsidianReadService(session, settings).get_vault_status()

    return ObsidianVaultStatusResponse(
        vault_name=result.vault_name,
        path_configured=result.path_configured,
        path_exists=result.path_exists,
        readable=result.readable,
        note_count=result.note_count,
        warnings=list(result.warnings),
    )


@router.get("/integrations/obsidian/notes", response_model=list[ObsidianNoteListItemResponse])
def list_obsidian_notes(
    request: Request,
    limit: int = Query(default=DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
    search: str | None = Query(default=None, max_length=120),
) -> list[ObsidianNoteListItemResponse]:
    (
        ObsidianReadConfigurationError,
        _,
        ObsidianReadPathError,
        ObsidianReadService,
    ) = _load_obsidian_read_dependencies()
    database = request.app.state.database
    settings = request.app.state.settings

    try:
        with database.session_scope() as session:
            items = ObsidianReadService(session, settings).list_notes(limit=limit, search=search)
    except (ObsidianReadConfigurationError, ObsidianReadPathError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return [
        ObsidianNoteListItemResponse(
            title=item.title,
            relative_path=item.relative_path,
            modified_at=item.modified_at,
            size_bytes=item.size_bytes,
            tags=list(item.tags),
            excerpt=item.excerpt,
        )
        for item in items
    ]


@router.get("/integrations/obsidian/note", response_model=ObsidianNoteReadResponse)
def read_obsidian_note(
    request: Request,
    path: str = Query(min_length=1, max_length=1024),
) -> ObsidianNoteReadResponse:
    (
        ObsidianReadConfigurationError,
        ObsidianReadNotFoundError,
        ObsidianReadPathError,
        ObsidianReadService,
    ) = _load_obsidian_read_dependencies()
    database = request.app.state.database
    settings = request.app.state.settings

    try:
        with database.session_scope() as session:
            note = ObsidianReadService(session, settings).read_note(path)
    except ObsidianReadNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (ObsidianReadConfigurationError, ObsidianReadPathError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return ObsidianNoteReadResponse(
        title=note.title,
        relative_path=note.relative_path,
        markdown_preview=note.markdown_preview,
        frontmatter=note.frontmatter,
        links=list(note.links),
        context_summary=note.context_summary,
    )


@router.post("/integrations/obsidian/export-preview", response_model=ObsidianExportPreviewResponse)
def preview_obsidian_export(
    request: Request,
    payload: ObsidianExportRequest,
) -> ObsidianExportPreviewResponse:
    (
        ObsidianExportConfigurationError,
        ObsidianExportNotFoundError,
        ObsidianExportPathError,
        ObsidianExportService,
    ) = _load_obsidian_export_dependencies()
    database = request.app.state.database
    settings = request.app.state.settings

    try:
        with database.session_scope() as session:
            preview = ObsidianExportService(session, settings).build_preview(
                note_id=payload.note_id,
                project_id=payload.project_id,
                title=payload.title,
                filename=payload.filename,
            )
    except ObsidianExportNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (ObsidianExportConfigurationError, ObsidianExportPathError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return ObsidianExportPreviewResponse(
        filename=preview.filename,
        relative_path=preview.relative_path,
        markdown=preview.markdown,
        warnings=list(preview.warnings),
    )


@router.post("/integrations/obsidian/export-confirm", response_model=ObsidianExportConfirmResponse)
def confirm_obsidian_export(
    request: Request,
    payload: ObsidianExportRequest,
) -> ObsidianExportConfirmResponse:
    (
        ObsidianExportConfigurationError,
        ObsidianExportNotFoundError,
        ObsidianExportPathError,
        ObsidianExportService,
    ) = _load_obsidian_export_dependencies()
    database = request.app.state.database
    settings = request.app.state.settings

    try:
        with database.session_scope() as session:
            result = ObsidianExportService(session, settings).confirm_export(
                note_id=payload.note_id,
                project_id=payload.project_id,
                title=payload.title,
                filename=payload.filename,
            )
    except ObsidianExportNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (ObsidianExportConfigurationError, ObsidianExportPathError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return ObsidianExportConfirmResponse(
        status=result.status,
        relative_path=result.relative_path,
        bytes_written=result.bytes_written,
    )


def _build_integration_response(descriptor) -> IntegrationDescriptorResponse:
    return IntegrationDescriptorResponse(
        key=descriptor.key,
        label=descriptor.label,
        category=descriptor.category,
        status=descriptor.status,
        capabilities=list(descriptor.capabilities),
        permission_level=descriptor.permission_level,
        is_available=descriptor.is_available,
        is_write_capable=descriptor.is_write_capable,
        notes=descriptor.notes,
        setup_mode=descriptor.setup_mode,
        setup_requirements=list(descriptor.setup_requirements),
        can_configure_in_ui=descriptor.can_configure_in_ui,
        can_connect_account=descriptor.can_connect_account,
        missing_requirements=list(descriptor.missing_requirements),
        next_action_label=descriptor.next_action_label,
        origin=descriptor.origin,
        source_kind=descriptor.source_kind,
        has_adapter=descriptor.has_adapter,
        configured_accounts_count=getattr(descriptor, "configured_accounts_count", 0),
    )


def _build_obsidian_config_response(config) -> ObsidianIntegrationConfigResponse:
    return ObsidianIntegrationConfigResponse(
        vault_path=str(config.vault_path) if config.vault_path is not None else None,
        export_enabled=config.export_enabled,
        path_exists=config.path_exists,
        source=config.source,
        warnings=list(config.warnings),
    )
