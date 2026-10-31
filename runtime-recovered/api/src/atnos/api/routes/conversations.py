from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from atnos.core.timestamps import as_utc

router = APIRouter(tags=["conversations"])

RESPONSE_STYLE_VALUES = {
    "concise-direct",
    "professional-clear",
    "supportive-guide",
    "critical-deep",
}
WORK_MODE_VALUES = {
    "auto",
    "quick",
    "deep",
    "execute",
}


def _load_conversation_repositories():
    from atnos.repositories.conversation_repository import ConversationRepository
    from atnos.repositories.project_repository import ProjectRepository

    return ConversationRepository, ProjectRepository


def _load_conversation_settings_dependencies():
    from atnos.services.conversation_settings_resolver_service import (
        ConversationSettingsNotFoundError,
        ConversationSettingsResolverError,
        ConversationSettingsResolverService,
    )

    return (
        ConversationSettingsNotFoundError,
        ConversationSettingsResolverError,
        ConversationSettingsResolverService,
    )


class ConversationSummaryResponse(BaseModel):
    id: int
    title: str
    project_id: int | None
    summary: str | None
    last_message_at: datetime | None
    updated_at: datetime
    archived: bool
    matched_source: str | None = None

    @field_validator('last_message_at', 'updated_at')
    @classmethod
    def utc_timestamps(cls, value: datetime | None) -> datetime | None:
        return as_utc(value) if value is not None else None


class ConversationsBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConversationCreateRequest(ConversationsBaseModel):
    title: str | None = Field(default=None, max_length=120)
    project_id: int | None = Field(default=None, gt=0)


class ConversationCreateResponse(BaseModel):
    conversation_id: int
    title: str
    project_id: int | None


class ConversationUpdateRequest(ConversationsBaseModel):
    title: str | None = Field(default=None, max_length=120)


class ConversationUpdateResponse(BaseModel):
    conversation_id: int
    title: str
    project_id: int | None


class ConversationDeleteRequest(ConversationsBaseModel):
    project_id: int | None = Field(default=None, gt=0)


class ConversationDeleteResponse(BaseModel):
    conversation_id: int
    archived: bool


class ConversationUnarchiveRequest(ConversationsBaseModel):
    project_id: int | None = Field(default=None, gt=0)


class ConversationUnarchiveResponse(BaseModel):
    conversation_id: int
    archived: bool


class ConversationSettingsResponse(BaseModel):
    conversation_id: int
    active_work_mode: str
    active_work_mode_source: Literal["conversation", "default"]
    response_style_override: str | None
    memory_enabled_override: bool | None
    file_enabled_override: bool | None
    internet_enabled_override: bool | None
    persona_profile_override: str | None
    provider_posture_override: str | None
    resolved_response_style: str | None
    resolved_response_style_source: Literal["conversation", "default"]
    resolved_memory_enabled: bool
    resolved_memory_enabled_source: Literal["conversation", "default"]
    resolved_file_enabled: bool
    resolved_file_enabled_source: Literal["conversation", "default"]
    resolved_internet_enabled: bool
    resolved_internet_enabled_source: Literal["conversation", "default"]
    resolved_persona_profile: str
    resolved_persona_profile_source: Literal["conversation", "project", "global"]
    resolved_provider_posture: str
    resolved_provider_posture_source: Literal["conversation", "project", "global"]


class ConversationSettingsUpdateRequest(ConversationsBaseModel):
    active_work_mode: str | None = Field(default=None, max_length=40)
    response_style_override: str | None = Field(default=None, max_length=40)
    memory_enabled_override: bool | None = None
    file_enabled_override: bool | None = None
    internet_enabled_override: bool | None = None
    persona_profile_override: str | None = Field(default=None, max_length=24)
    provider_posture_override: str | None = Field(default=None, max_length=40)

    @field_validator("active_work_mode", mode="before")
    @classmethod
    def validate_active_work_mode(cls, value: object) -> str:
        return _validate_work_mode(value if isinstance(value, str) or value is None else str(value))


@router.get("/conversations", response_model=list[ConversationSummaryResponse])
def list_conversations(
    request: Request,
    project_id: int | None = Query(default=None, gt=0),
    sort: Literal["last_activity", "title"] = Query(default="last_activity"),
    include_archived: bool = Query(default=False),
    search: str | None = Query(default=None, max_length=120),
) -> list[ConversationSummaryResponse]:
    ConversationRepository, ProjectRepository = _load_conversation_repositories()
    database = request.app.state.database

    with database.session_scope() as session:
        projects = ProjectRepository(session)
        if project_id is not None and projects.get(project_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown project_id '{project_id}'.",
            )

        conversations = ConversationRepository(session).list_recent(
            project_id,
            sort=sort,
            include_archived=include_archived,
            search=search,
        )

    return [
        ConversationSummaryResponse(
            id=item.conversation.id,
            title=item.conversation.title,
            project_id=item.conversation.project_id,
            summary=item.conversation.summary,
            last_message_at=item.conversation.last_message_at,
            updated_at=item.conversation.updated_at,
            archived=include_archived,
            matched_source=item.matched_source,
        )
        for item in conversations
    ]


@router.post(
    "/conversations",
    response_model=ConversationCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_conversation(
    request: Request,
    payload: ConversationCreateRequest,
) -> ConversationCreateResponse:
    ConversationRepository, ProjectRepository = _load_conversation_repositories()
    database = request.app.state.database
    normalized_title = _normalize_title(payload.title)

    with database.session_scope() as session:
        projects = ProjectRepository(session)
        if payload.project_id is not None and projects.get(payload.project_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown project_id '{payload.project_id}'.",
            )

        conversation = ConversationRepository(session).create(
            title=normalized_title,
            project_id=payload.project_id,
        )

    return ConversationCreateResponse(
        conversation_id=conversation.id,
        title=conversation.title,
        project_id=conversation.project_id,
    )


@router.put("/conversations/{conversation_id}", response_model=ConversationUpdateResponse)
def update_conversation(
    conversation_id: int,
    request: Request,
    payload: ConversationUpdateRequest,
) -> ConversationUpdateResponse:
    ConversationRepository, _ = _load_conversation_repositories()
    database = request.app.state.database
    normalized_title = _normalize_title(payload.title)

    with database.session_scope() as session:
        conversations = ConversationRepository(session)
        conversation = conversations.get(conversation_id)
        if conversation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown conversation_id '{conversation_id}'.",
            )

        conversation = conversations.update_title(conversation, normalized_title)

    return ConversationUpdateResponse(
        conversation_id=conversation.id,
        title=conversation.title,
        project_id=conversation.project_id,
    )


@router.get(
    "/conversations/{conversation_id}/settings",
    response_model=ConversationSettingsResponse,
)
def get_conversation_settings(
    conversation_id: int,
    request: Request,
) -> ConversationSettingsResponse:
    (
        ConversationSettingsNotFoundError,
        ConversationSettingsResolverError,
        ConversationSettingsResolverService,
    ) = _load_conversation_settings_dependencies()
    database = request.app.state.database

    try:
        with database.session_scope() as session:
            resolution = ConversationSettingsResolverService(session).resolve(
                conversation_id=conversation_id
            )
    except ConversationSettingsNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ConversationSettingsResolverError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    return ConversationSettingsResponse(
        conversation_id=resolution.conversation_id,
        active_work_mode=resolution.active_work_mode,
        active_work_mode_source=resolution.active_work_mode_source,
        response_style_override=resolution.response_style_override,
        memory_enabled_override=resolution.memory_enabled_override,
        file_enabled_override=resolution.file_enabled_override,
        internet_enabled_override=resolution.internet_enabled_override,
        persona_profile_override=_enum_value(resolution.persona_profile_override),
        provider_posture_override=_enum_value(resolution.provider_posture_override),
        resolved_response_style=resolution.resolved_response_style,
        resolved_response_style_source=resolution.resolved_response_style_source,
        resolved_memory_enabled=resolution.resolved_memory_enabled,
        resolved_memory_enabled_source=resolution.resolved_memory_enabled_source,
        resolved_file_enabled=resolution.resolved_file_enabled,
        resolved_file_enabled_source=resolution.resolved_file_enabled_source,
        resolved_internet_enabled=resolution.resolved_internet_enabled,
        resolved_internet_enabled_source=resolution.resolved_internet_enabled_source,
        resolved_persona_profile=_enum_value(resolution.resolved_persona_profile),
        resolved_persona_profile_source=resolution.resolved_persona_profile_source,
        resolved_provider_posture=_enum_value(resolution.resolved_provider_posture),
        resolved_provider_posture_source=resolution.resolved_provider_posture_source,
    )


@router.put(
    "/conversations/{conversation_id}/settings",
    response_model=ConversationSettingsResponse,
)
def update_conversation_settings(
    conversation_id: int,
    request: Request,
    payload: ConversationSettingsUpdateRequest,
) -> ConversationSettingsResponse:
    ConversationRepository, _ = _load_conversation_repositories()
    (
        ConversationSettingsNotFoundError,
        ConversationSettingsResolverError,
        ConversationSettingsResolverService,
    ) = _load_conversation_settings_dependencies()
    database = request.app.state.database

    try:
        with database.session_scope() as session:
            conversations = ConversationRepository(session)
            conversation = conversations.get(conversation_id)
            if conversation is None:
                raise ConversationSettingsNotFoundError(
                    f"Unknown conversation_id '{conversation_id}'."
                )

            updates = payload.model_dump(exclude_unset=True)
            validated_updates: dict[str, object | None] = {}
            if "active_work_mode" in updates:
                validated_updates["active_work_mode"] = payload.active_work_mode
            if "response_style_override" in updates:
                validated_updates["response_style_override"] = _validate_response_style(
                    payload.response_style_override
                )
            if "memory_enabled_override" in updates:
                validated_updates["memory_enabled_override"] = payload.memory_enabled_override
            if "file_enabled_override" in updates:
                validated_updates["file_enabled_override"] = payload.file_enabled_override
            if "internet_enabled_override" in updates:
                validated_updates["internet_enabled_override"] = payload.internet_enabled_override
            if "persona_profile_override" in updates:
                validated_updates["persona_profile_override"] = _validate_persona_profile(
                    payload.persona_profile_override
                )
            if "provider_posture_override" in updates:
                validated_updates[
                    "provider_posture_override"
                ] = _validate_provider_posture(payload.provider_posture_override)

            conversation = conversations.update_settings_overrides(
                conversation,
                **validated_updates,
            )
            resolution = ConversationSettingsResolverService(session).resolve(
                conversation=conversation
            )
    except ConversationSettingsNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ConversationSettingsResolverError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    return ConversationSettingsResponse(
        conversation_id=resolution.conversation_id,
        active_work_mode=resolution.active_work_mode,
        active_work_mode_source=resolution.active_work_mode_source,
        response_style_override=resolution.response_style_override,
        memory_enabled_override=resolution.memory_enabled_override,
        file_enabled_override=resolution.file_enabled_override,
        internet_enabled_override=resolution.internet_enabled_override,
        persona_profile_override=_enum_value(resolution.persona_profile_override),
        provider_posture_override=_enum_value(resolution.provider_posture_override),
        resolved_response_style=resolution.resolved_response_style,
        resolved_response_style_source=resolution.resolved_response_style_source,
        resolved_memory_enabled=resolution.resolved_memory_enabled,
        resolved_memory_enabled_source=resolution.resolved_memory_enabled_source,
        resolved_file_enabled=resolution.resolved_file_enabled,
        resolved_file_enabled_source=resolution.resolved_file_enabled_source,
        resolved_internet_enabled=resolution.resolved_internet_enabled,
        resolved_internet_enabled_source=resolution.resolved_internet_enabled_source,
        resolved_persona_profile=_enum_value(resolution.resolved_persona_profile),
        resolved_persona_profile_source=resolution.resolved_persona_profile_source,
        resolved_provider_posture=_enum_value(resolution.resolved_provider_posture),
        resolved_provider_posture_source=resolution.resolved_provider_posture_source,
    )


@router.delete("/conversations/{conversation_id}", response_model=ConversationDeleteResponse)
def archive_conversation(
    conversation_id: int,
    request: Request,
    payload: ConversationDeleteRequest | None = None,
) -> ConversationDeleteResponse:
    ConversationRepository, _ = _load_conversation_repositories()
    database = request.app.state.database
    expected_project_id = payload.project_id if payload is not None else None

    with database.session_scope() as session:
        conversations = ConversationRepository(session)
        conversation = conversations.get(conversation_id)
        if conversation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown conversation_id '{conversation_id}'.",
            )
        if conversation.project_id != expected_project_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation is not available in the active project scope.",
            )

        conversation = conversations.archive(conversation)

    return ConversationDeleteResponse(
        conversation_id=conversation.id,
        archived=True,
    )


@router.put(
    "/conversations/{conversation_id}/unarchive",
    response_model=ConversationUnarchiveResponse,
)
def unarchive_conversation(
    conversation_id: int,
    request: Request,
    payload: ConversationUnarchiveRequest | None = None,
) -> ConversationUnarchiveResponse:
    ConversationRepository, _ = _load_conversation_repositories()
    database = request.app.state.database
    expected_project_id = payload.project_id if payload is not None else None

    with database.session_scope() as session:
        conversations = ConversationRepository(session)
        conversation = conversations.get_any(conversation_id)
        if conversation is None or conversation.status.name != "ARCHIVED":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown archived conversation_id '{conversation_id}'.",
            )
        if conversation.project_id != expected_project_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation is not available in the active project scope.",
            )

        conversation = conversations.unarchive(conversation)

    return ConversationUnarchiveResponse(
        conversation_id=conversation.id,
        archived=False,
    )


def _normalize_title(title: str | None) -> str:
    if title is None:
        return "Nowa rozmowa"

    normalized = " ".join(title.split())
    if not normalized:
        return "Nowa rozmowa"

    if len(normalized) <= 120:
        return normalized

    candidate = normalized[:120].rstrip()
    if " " in candidate:
        candidate = candidate.rsplit(" ", 1)[0].rstrip()

    return candidate or normalized[:120].rstrip()


def _validate_response_style(value: str | None) -> str | None:
    if value is None:
        return None
    if value not in RESPONSE_STYLE_VALUES:
        raise ValueError(
            f"Unsupported response_style_override '{value}'."
        )
    return value


def _validate_work_mode(value: str | None) -> str:
    normalized = " ".join((value or "").split()).lower()
    if not normalized:
        return "auto"
    if normalized not in WORK_MODE_VALUES:
        raise ValueError(f"Unsupported active_work_mode '{value}'.")
    return normalized


def _validate_persona_profile(value: str | None) -> Any | None:
    if value is None:
        return None
    from atnos.db.models.enums import PersonaProfile

    try:
        return PersonaProfile(value)
    except ValueError as exc:
        raise ValueError(f"Unsupported persona_profile_override '{value}'.") from exc


def _validate_provider_posture(value: str | None) -> Any | None:
    if value is None:
        return None
    from atnos.db.models.enums import ProviderPosture

    try:
        return ProviderPosture(value)
    except ValueError as exc:
        raise ValueError(f"Unsupported provider_posture_override '{value}'.") from exc


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    enum_value = getattr(value, "value", None)
    return enum_value if enum_value is not None else str(value)
