from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(tags=["projects"])

ProviderPostureValue = Literal[
    "prefer-local",
    "improve-when-needed",
    "ask-before-external",
    "strict-local",
]
PersonaProfileValue = Literal["strategist", "critic", "executor", "mentor", "jarvis-mode"]


def _load_project_repository():
    from atnos.repositories.project_repository import ProjectRepository

    return ProjectRepository


def _load_project_app_links_service():
    from atnos.services.project_app_links_service import ProjectAppLinksService

    return ProjectAppLinksService


def _load_project_readiness_service():
    from atnos.services.project_readiness_service import ProjectReadinessService

    return ProjectReadinessService


def _load_legacy_platform_binding_migration_service():
    from atnos.services.legacy_platform_binding_migration_service import LegacyPlatformBindingMigrationService

    return LegacyPlatformBindingMigrationService


def _load_project_enums():
    from atnos.db.models.enums import PersonaProfile, ProviderPosture

    return PersonaProfile, ProviderPosture


def _coerce_provider_posture(value: str | None) -> Any | None:
    if value is None:
        return None
    _, ProviderPosture = _load_project_enums()
    return ProviderPosture(value)


def _coerce_persona_profile(value: str | None) -> Any | None:
    if value is None:
        return None
    PersonaProfile, _ = _load_project_enums()
    return PersonaProfile(value)


class ProjectSummaryResponse(BaseModel):
    id: int
    name: str
    slug: str
    description: str | None
    summary: str | None
    status: str
    is_pinned: bool
    provider_posture: str | None
    persona_profile: str | None


class ProjectAppLinkAccountSummaryResponse(BaseModel):
    id: int
    label: str
    provider_key: str
    account_identifier: str
    status: str
    risk_level: str
    linked_integration_key: str | None


class ProjectAppLinkSummaryResponse(BaseModel):
    id: int
    integration_key: str
    integration_label: str
    account_id: int | None
    account_label: str | None
    resource_type: str
    resource_external_id: str | None
    resource_label: str | None
    environment: str
    connection_status: str
    permission_profile: str
    status: str
    purpose: str | None
    allowed_capabilities: list[str]
    is_default_for_project: bool
    is_default_for_content: bool
    is_default_for_analytics: bool
    is_default_for_storage: bool
    is_default_for_publish_preview: bool


class ProjectAppLinkDefaultRoleResponse(BaseModel):
    id: int
    label: str
    integration_key: str
    integration_label: str
    account_id: int | None
    account_label: str | None
    status: str
    permission_profile: str
    allowed_capabilities: list[str]


class ProjectAppLinksDefaultsResponse(BaseModel):
    linked_app_count: int
    project: ProjectAppLinkDefaultRoleResponse | None
    content: ProjectAppLinkDefaultRoleResponse | None
    analytics: ProjectAppLinkDefaultRoleResponse | None
    storage: ProjectAppLinkDefaultRoleResponse | None
    publish_preview: ProjectAppLinkDefaultRoleResponse | None


class ProjectDetailResponse(ProjectSummaryResponse):
    app_links_summary: list[ProjectAppLinkSummaryResponse]
    app_links_defaults: ProjectAppLinksDefaultsResponse


class ProjectsRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectMutationRequest(ProjectsRequestModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    provider_posture: ProviderPostureValue | None = None
    persona_profile: PersonaProfileValue | None = None


class ProjectAppLinkRequestBase(ProjectsRequestModel):
    integration_key: str = Field(min_length=1, max_length=120)
    custom_integration_id: int | None = Field(default=None, gt=0)
    account_id: int | None = Field(default=None, gt=0)
    label: str = Field(min_length=1, max_length=160)
    purpose: str | None = Field(default=None, max_length=1000)
    status: str = Field(default="configured", pattern="^(planned|configured|disabled|needs_review)$")
    permission_profile: str = Field(
        pattern="^(metadata_only|read_only|draft_write|supervised_write_later|publish_preview_later|financial_read_later|trading_preview_later)$"
    )
    allowed_capabilities: list[str] = Field(default_factory=list)
    safety_notes: str | None = Field(default=None, max_length=1000)
    is_default_for_project: bool = False
    is_default_for_content: bool = False
    is_default_for_analytics: bool = False
    is_default_for_storage: bool = False
    is_default_for_publish_preview: bool = False
    notes: str | None = Field(default=None, max_length=1000)
    resource_type: str | None = Field(
        default=None,
        pattern="^(account|channel|mailbox|broker_account|workspace|profile)$",
    )
    resource_external_id: str | None = Field(default=None, max_length=320)
    resource_label: str | None = Field(default=None, max_length=160)
    binding_role: str = Field(default="primary", min_length=1, max_length=64)
    environment: str = Field(default="production", pattern="^(demo|test|production)$")
    confirmation_policy: str = Field(default="supervised", pattern="^(none|supervised|always_confirm)$")
    delegation_policy: str = Field(default="project_only", pattern="^(disabled|project_only|approved_workers)$")
    allow_shared_account: bool = False
    permission_scopes: list[str] = Field(default_factory=list, max_length=100)


class ProjectAppLinkCreateRequest(ProjectAppLinkRequestBase):
    pass


class ProjectAppLinkUpdateRequest(ProjectsRequestModel):
    account_id: int | None = Field(default=None, gt=0)
    label: str | None = Field(default=None, min_length=1, max_length=160)
    purpose: str | None = Field(default=None, max_length=1000)
    status: str | None = Field(default=None, pattern="^(planned|configured|disabled|needs_review)$")
    permission_profile: str | None = Field(
        default=None,
        pattern="^(metadata_only|read_only|draft_write|supervised_write_later|publish_preview_later|financial_read_later|trading_preview_later)$",
    )
    allowed_capabilities: list[str] | None = None
    safety_notes: str | None = Field(default=None, max_length=1000)
    is_default_for_project: bool | None = None
    is_default_for_content: bool | None = None
    is_default_for_analytics: bool | None = None
    is_default_for_storage: bool | None = None
    is_default_for_publish_preview: bool | None = None
    notes: str | None = Field(default=None, max_length=1000)
    resource_type: str | None = Field(
        default=None,
        pattern="^(account|channel|mailbox|broker_account|workspace|profile)$",
    )
    resource_external_id: str | None = Field(default=None, max_length=320)
    resource_label: str | None = Field(default=None, max_length=160)
    binding_role: str | None = Field(default=None, min_length=1, max_length=64)
    environment: str | None = Field(default=None, pattern="^(demo|test|production)$")
    confirmation_policy: str | None = Field(default=None, pattern="^(none|supervised|always_confirm)$")
    delegation_policy: str | None = Field(default=None, pattern="^(disabled|project_only|approved_workers)$")
    allow_shared_account: bool | None = None
    permission_scopes: list[str] | None = Field(default=None, max_length=100)


class ProjectAppLinkResponse(BaseModel):
    id: int
    project_id: int
    integration_key: str
    custom_integration_id: int | None
    account_id: int | None
    resource_type: str
    resource_external_id: str | None
    resource_label: str | None
    binding_role: str
    environment: str
    confirmation_policy: str
    delegation_policy: str
    allow_shared_account: bool
    permission_scopes: list[str]
    connection_status: str
    connection_detail: str | None
    last_verified_at: str | None
    verification_evidence: dict[str, object]
    label: str
    purpose: str | None
    status: str
    permission_profile: str
    allowed_capabilities: list[str]
    safety_notes: str | None
    is_default_for_project: bool
    is_default_for_content: bool
    is_default_for_analytics: bool
    is_default_for_storage: bool
    is_default_for_publish_preview: bool
    notes: str | None
    integration_label: str
    account_summary: ProjectAppLinkAccountSummaryResponse | None
    created_at: str
    updated_at: str


class LegacyPlatformBindingMigrationRequest(ProjectsRequestModel):
    """`apply=False` przygotowuje plan; zapis wymaga jawnego przełączenia."""

    apply: bool = False
    project_ids: list[int] = Field(default_factory=list, max_length=100)


class LegacyPlatformBindingMigrationResponse(BaseModel):
    mode: Literal["dry_run", "apply"]
    migration_safe: bool
    mutates_credentials: bool
    legacy_fields_retained: bool
    summary: dict[str, int]
    items: list[dict[str, object]]


class ProjectMutationResponse(BaseModel):
    project_id: int
    name: str
    slug: str
    description: str | None
    summary: str | None
    status: str
    is_pinned: bool
    provider_posture: str | None
    persona_profile: str | None


class ProjectDeleteResponse(BaseModel):
    project_id: int
    name: str
    deleted: bool


@router.get("/projects", response_model=list[ProjectSummaryResponse])
def list_projects(request: Request) -> list[ProjectSummaryResponse]:
    ProjectRepository = _load_project_repository()
    database = request.app.state.database

    with database.session_scope() as session:
        projects = ProjectRepository(session).list_all()

    return [_build_project_summary_response(project) for project in projects]


@router.get("/api/projects/readiness")
def list_project_readiness(request: Request) -> dict[str, Any]:
    """Return one safe operator snapshot for all revenue/content projects."""
    ProjectReadinessService = _load_project_readiness_service()
    database = request.app.state.database
    with database.session_scope() as session:
        return ProjectReadinessService(request.app.state.settings, session=session).build()


@router.get("/projects/{project_id}", response_model=ProjectDetailResponse)
def get_project(project_id: int, request: Request) -> ProjectDetailResponse:
    ProjectRepository = _load_project_repository()
    ProjectAppLinksService = _load_project_app_links_service()
    database = request.app.state.database
    settings = request.app.state.settings

    with database.session_scope() as session:
        projects = ProjectRepository(session)
        project = projects.get(project_id)
        if project is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown project_id '{project_id}'.",
            )
        summaries = ProjectAppLinksService(session, settings).summarize_links(project_id)
        defaults = ProjectAppLinksService(session, settings).summarize_defaults(project_id)

    return ProjectDetailResponse(
        id=project.id,
        name=project.name,
        slug=project.slug,
        description=project.description,
        summary=project.summary,
        status=project.status.value,
        is_pinned=project.is_pinned,
        provider_posture=project.provider_posture.value if project.provider_posture is not None else None,
        persona_profile=project.persona_profile.value if project.persona_profile is not None else None,
        app_links_summary=[ProjectAppLinkSummaryResponse(**item) for item in summaries],
        app_links_defaults=ProjectAppLinksDefaultsResponse(
            linked_app_count=int(defaults["linked_app_count"]),
            project=(
                ProjectAppLinkDefaultRoleResponse(**defaults["project"])
                if defaults["project"] is not None
                else None
            ),
            content=(
                ProjectAppLinkDefaultRoleResponse(**defaults["content"])
                if defaults["content"] is not None
                else None
            ),
            analytics=(
                ProjectAppLinkDefaultRoleResponse(**defaults["analytics"])
                if defaults["analytics"] is not None
                else None
            ),
            storage=(
                ProjectAppLinkDefaultRoleResponse(**defaults["storage"])
                if defaults["storage"] is not None
                else None
            ),
            publish_preview=(
                ProjectAppLinkDefaultRoleResponse(**defaults["publish_preview"])
                if defaults["publish_preview"] is not None
                else None
            ),
        ),
    )


@router.post(
    "/projects",
    response_model=ProjectMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_project(
    request: Request,
    payload: ProjectMutationRequest,
) -> ProjectMutationResponse:
    ProjectRepository = _load_project_repository()
    normalized_name = _normalize_name(payload.name)
    normalized_description = _normalize_description(payload.description)
    database = request.app.state.database

    with database.session_scope() as session:
        project = ProjectRepository(session).create(
            normalized_name,
            description=normalized_description,
            provider_posture=_coerce_provider_posture(payload.provider_posture),
            persona_profile=_coerce_persona_profile(payload.persona_profile),
        )

    return _build_project_mutation_response(project)


@router.put("/projects/{project_id}", response_model=ProjectMutationResponse)
def update_project_name(
    request: Request,
    project_id: int,
    payload: ProjectMutationRequest,
) -> ProjectMutationResponse:
    ProjectRepository = _load_project_repository()
    normalized_name = _normalize_name(payload.name)
    normalized_description = _normalize_description(payload.description)
    database = request.app.state.database

    with database.session_scope() as session:
        projects = ProjectRepository(session)
        project = projects.get(project_id)
        if project is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown project_id '{project_id}'.",
            )

        updated_project = projects.update(
            project,
            name=normalized_name,
            description=normalized_description,
            provider_posture=_coerce_provider_posture(payload.provider_posture),
            persona_profile=_coerce_persona_profile(payload.persona_profile),
        )

    return _build_project_mutation_response(updated_project)


@router.delete("/projects/{project_id}", response_model=ProjectDeleteResponse)
def delete_project(project_id: int, request: Request) -> ProjectDeleteResponse:
    ProjectRepository = _load_project_repository()
    database = request.app.state.database

    with database.session_scope() as session:
        projects = ProjectRepository(session)
        project = projects.get(project_id)
        if project is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown project_id '{project_id}'.",
            )
        project_name = project.name
        projects.delete(project)

    return ProjectDeleteResponse(project_id=project_id, name=project_name, deleted=True)


@router.post(
    "/projects/platform-bindings/migrate-legacy",
    response_model=LegacyPlatformBindingMigrationResponse,
)
def migrate_legacy_project_platform_bindings(
    request: Request,
    payload: LegacyPlatformBindingMigrationRequest,
) -> LegacyPlatformBindingMigrationResponse:
    """Migruje tylko jednoznaczne legacy relacje, nigdy hasła ani tokeny."""
    LegacyPlatformBindingMigrationService = _load_legacy_platform_binding_migration_service()
    database = request.app.state.database
    settings = request.app.state.settings
    with database.session_scope() as session:
        report = LegacyPlatformBindingMigrationService(session, settings).migrate(
            apply=payload.apply,
            project_ids=tuple(payload.project_ids),
        )
    return LegacyPlatformBindingMigrationResponse(**report)


@router.get("/projects/{project_id}/app-links", response_model=list[ProjectAppLinkResponse])
def list_project_app_links(project_id: int, request: Request) -> list[ProjectAppLinkResponse]:
    ProjectAppLinksService = _load_project_app_links_service()
    database = request.app.state.database
    settings = request.app.state.settings
    with database.session_scope() as session:
        items = ProjectAppLinksService(session, settings).list_links(project_id)
    if items is None:
        raise HTTPException(status_code=404, detail=f"Unknown project_id '{project_id}'.")
    return [_build_app_link_response(item) for item in items]


@router.post(
    "/projects/{project_id}/app-links",
    response_model=ProjectAppLinkResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_project_app_link(
    project_id: int,
    request: Request,
    payload: ProjectAppLinkCreateRequest,
) -> ProjectAppLinkResponse:
    ProjectAppLinksService = _load_project_app_links_service()
    database = request.app.state.database
    settings = request.app.state.settings
    try:
        with database.session_scope() as session:
            item = ProjectAppLinksService(session, settings).create_link(
                project_id,
                integration_key=payload.integration_key,
                custom_integration_id=payload.custom_integration_id,
                account_id=payload.account_id,
                label=payload.label,
                purpose=payload.purpose,
                status=payload.status,
                permission_profile=payload.permission_profile,
                allowed_capabilities=payload.allowed_capabilities,
                safety_notes=payload.safety_notes,
                is_default_for_project=payload.is_default_for_project,
                is_default_for_content=payload.is_default_for_content,
                is_default_for_analytics=payload.is_default_for_analytics,
                is_default_for_storage=payload.is_default_for_storage,
                is_default_for_publish_preview=payload.is_default_for_publish_preview,
                notes=payload.notes,
                resource_type=payload.resource_type,
                resource_external_id=payload.resource_external_id,
                resource_label=payload.resource_label,
                binding_role=payload.binding_role,
                environment=payload.environment,
                confirmation_policy=payload.confirmation_policy,
                delegation_policy=payload.delegation_policy,
                allow_shared_account=payload.allow_shared_account,
                permission_scopes=payload.permission_scopes,
            )
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if detail.startswith("Unknown project_id") else 422
        raise HTTPException(status_code=status_code, detail=detail) from exc
    return _build_app_link_response(item)


@router.get("/projects/{project_id}/app-links/{link_id}", response_model=ProjectAppLinkResponse)
def get_project_app_link(project_id: int, link_id: int, request: Request) -> ProjectAppLinkResponse:
    ProjectRepository = _load_project_repository()
    ProjectAppLinksService = _load_project_app_links_service()
    database = request.app.state.database
    settings = request.app.state.settings
    with database.session_scope() as session:
        project = ProjectRepository(session).get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"Unknown project_id '{project_id}'.")
        item = ProjectAppLinksService(session, settings).get_link(project_id, link_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Project app link {link_id} was not found.")
    return _build_app_link_response(item)


@router.patch("/projects/{project_id}/app-links/{link_id}", response_model=ProjectAppLinkResponse)
def update_project_app_link(
    project_id: int,
    link_id: int,
    request: Request,
    payload: ProjectAppLinkUpdateRequest,
) -> ProjectAppLinkResponse:
    ProjectAppLinksService = _load_project_app_links_service()
    database = request.app.state.database
    settings = request.app.state.settings
    fields = payload.model_dump(exclude_unset=True)
    update_kwargs = {
        key: fields[key]
        for key in (
            "account_id",
            "label",
            "purpose",
            "status",
            "permission_profile",
            "allowed_capabilities",
            "safety_notes",
            "is_default_for_project",
            "is_default_for_content",
            "is_default_for_analytics",
            "is_default_for_storage",
            "is_default_for_publish_preview",
            "notes",
            "resource_type",
            "resource_external_id",
            "resource_label",
            "binding_role",
            "environment",
            "confirmation_policy",
            "delegation_policy",
            "allow_shared_account",
            "permission_scopes",
        )
        if key in fields
    }
    try:
        with database.session_scope() as session:
            item = ProjectAppLinksService(session, settings).update_link(
                project_id,
                link_id,
                **update_kwargs,
            )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail=f"Project app link {link_id} was not found.")
    return _build_app_link_response(item)


@router.delete("/projects/{project_id}/app-links/{link_id}", response_model=ProjectAppLinkResponse)
def disable_project_app_link(project_id: int, link_id: int, request: Request) -> ProjectAppLinkResponse:
    ProjectRepository = _load_project_repository()
    ProjectAppLinksService = _load_project_app_links_service()
    database = request.app.state.database
    settings = request.app.state.settings
    with database.session_scope() as session:
        project = ProjectRepository(session).get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"Unknown project_id '{project_id}'.")
        item = ProjectAppLinksService(session, settings).disable_link(project_id, link_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Project app link {link_id} was not found.")
    return _build_app_link_response(item)


@router.post("/projects/{project_id}/app-links/{link_id}/verify", response_model=ProjectAppLinkResponse)
def verify_project_app_link(project_id: int, link_id: int, request: Request) -> ProjectAppLinkResponse:
    """Sprawdza realną referencję połączenia bez zwracania sekretów."""
    ProjectRepository = _load_project_repository()
    ProjectAppLinksService = _load_project_app_links_service()
    database = request.app.state.database
    settings = request.app.state.settings
    with database.session_scope() as session:
        project = ProjectRepository(session).get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"Unknown project_id '{project_id}'.")
        item = ProjectAppLinksService(session, settings).verify_link(project_id, link_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Project app link {link_id} was not found.")
    return _build_app_link_response(item)


def _normalize_name(name: str) -> str:
    normalized = " ".join(name.split())
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Project name cannot be empty.",
        )
    return normalized


def _normalize_description(description: str | None) -> str | None:
    if description is None:
        return None
    normalized = description.strip()
    return normalized or None


def _build_project_summary_response(project) -> ProjectSummaryResponse:
    return ProjectSummaryResponse(
        id=project.id,
        name=project.name,
        slug=project.slug,
        description=project.description,
        summary=project.summary,
        status=project.status.value,
        is_pinned=project.is_pinned,
        provider_posture=project.provider_posture.value if project.provider_posture is not None else None,
        persona_profile=project.persona_profile.value if project.persona_profile is not None else None,
    )


def _build_project_mutation_response(project) -> ProjectMutationResponse:
    return ProjectMutationResponse(
        project_id=project.id,
        name=project.name,
        slug=project.slug,
        description=project.description,
        summary=project.summary,
        status=project.status.value,
        is_pinned=project.is_pinned,
        provider_posture=project.provider_posture.value if project.provider_posture is not None else None,
        persona_profile=project.persona_profile.value if project.persona_profile is not None else None,
    )


def _build_app_link_response(item) -> ProjectAppLinkResponse:
    return ProjectAppLinkResponse(
        id=item.id,
        project_id=item.project_id,
        integration_key=item.integration_key,
        custom_integration_id=item.custom_integration_id,
        account_id=item.account_id,
        resource_type=item.resource_type,
        resource_external_id=item.resource_external_id,
        resource_label=item.resource_label,
        binding_role=item.binding_role,
        environment=item.environment,
        confirmation_policy=item.confirmation_policy,
        delegation_policy=item.delegation_policy,
        allow_shared_account=item.allow_shared_account,
        permission_scopes=list(item.permission_scopes),
        connection_status=item.connection_status,
        connection_detail=item.connection_detail,
        last_verified_at=item.last_verified_at,
        verification_evidence=item.verification_evidence,
        label=item.label,
        purpose=item.purpose,
        status=item.status,
        permission_profile=item.permission_profile,
        allowed_capabilities=list(item.allowed_capabilities),
        safety_notes=item.safety_notes,
        is_default_for_project=item.is_default_for_project,
        is_default_for_content=item.is_default_for_content,
        is_default_for_analytics=item.is_default_for_analytics,
        is_default_for_storage=item.is_default_for_storage,
        is_default_for_publish_preview=item.is_default_for_publish_preview,
        notes=item.notes,
        integration_label=item.integration_label,
        account_summary=(
            ProjectAppLinkAccountSummaryResponse(**item.account_summary)
            if item.account_summary is not None
            else None
        ),
        created_at=item.created_at,
        updated_at=item.updated_at,
    )
