from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from atnos.db.models.conversation import Conversation
from atnos.db.models.enums import ConversationStatus, PersonaProfile, ProviderPosture

ConversationSort = str
_UNSET = object()


@dataclass(slots=True)
class ConversationListItem:
    conversation: Conversation
    matched_source: str | None = None


class ConversationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        title: str,
        project_id: int | None = None,
        role_profile_id: int | None = None,
        active_work_mode: str = "auto",
        status: ConversationStatus = ConversationStatus.ACTIVE,
    ) -> Conversation:
        conversation = Conversation(
            title=title,
            project_id=project_id,
            role_profile_id=role_profile_id,
            active_work_mode=active_work_mode,
            status=status,
        )
        self.session.add(conversation)
        self.session.flush()
        return conversation

    def get(self, conversation_id: int) -> Conversation | None:
        statement = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.status == ConversationStatus.ACTIVE,
        )
        return self.session.scalar(statement)

    def get_any(self, conversation_id: int) -> Conversation | None:
        statement = select(Conversation).where(Conversation.id == conversation_id)
        return self.session.scalar(statement)

    def list_recent(
        self,
        project_id: int | None = None,
        *,
        sort: ConversationSort = "last_activity",
        include_archived: bool = False,
        search: str | None = None,
    ) -> list[ConversationListItem]:
        statement = select(Conversation)
        statement = statement.where(
            Conversation.status
            == (
                ConversationStatus.ARCHIVED
                if include_archived
                else ConversationStatus.ACTIVE
            )
        )
        if project_id is not None:
            statement = statement.where(Conversation.project_id == project_id)

        normalized_search = search.strip() if search is not None else ""
        if normalized_search:
            search_pattern = f"%{normalized_search}%"
            statement = statement.where(
                or_(
                    Conversation.title.ilike(search_pattern),
                    Conversation.summary.ilike(search_pattern),
                )
            )

        if sort == "title":
            statement = statement.order_by(
                func.lower(Conversation.title),
                Conversation.id.asc(),
            )
        else:
            statement = statement.order_by(
                Conversation.last_message_at.desc().nullslast(),
                Conversation.updated_at.desc(),
                Conversation.id.desc(),
            )

        conversations = list(self.session.scalars(statement))
        return [
            ConversationListItem(
                conversation=conversation,
                matched_source=(
                    "title"
                    if normalized_search
                    and normalized_search.lower() in conversation.title.lower()
                    else (
                        "summary"
                        if normalized_search
                        and conversation.summary is not None
                        and normalized_search.lower() in conversation.summary.lower()
                        else None
                    )
                ),
            )
            for conversation in conversations
        ]

    def list_by_project(self, project_id: int) -> list[Conversation]:
        return [item.conversation for item in self.list_recent(project_id)]

    def update_title(self, conversation: Conversation, title: str) -> Conversation:
        conversation.title = title
        self.session.add(conversation)
        self.session.flush()
        return conversation

    def update_settings_overrides(
        self,
        conversation: Conversation,
        *,
        active_work_mode: str | object = _UNSET,
        response_style_override: str | None | object = _UNSET,
        memory_enabled_override: bool | None | object = _UNSET,
        file_enabled_override: bool | None | object = _UNSET,
        internet_enabled_override: bool | None | object = _UNSET,
        persona_profile_override: PersonaProfile | None | object = _UNSET,
        provider_posture_override: ProviderPosture | None | object = _UNSET,
    ) -> Conversation:
        if active_work_mode is not _UNSET:
            conversation.active_work_mode = active_work_mode
        if response_style_override is not _UNSET:
            conversation.response_style_override = response_style_override
        if memory_enabled_override is not _UNSET:
            conversation.memory_enabled_override = memory_enabled_override
        if file_enabled_override is not _UNSET:
            conversation.file_enabled_override = file_enabled_override
        if internet_enabled_override is not _UNSET:
            conversation.internet_enabled_override = internet_enabled_override
        if persona_profile_override is not _UNSET:
            conversation.persona_profile_override = persona_profile_override
        if provider_posture_override is not _UNSET:
            conversation.provider_posture_override = provider_posture_override
        self.session.add(conversation)
        self.session.flush()
        return conversation

    def archive(self, conversation: Conversation) -> Conversation:
        conversation.status = ConversationStatus.ARCHIVED
        self.session.add(conversation)
        self.session.flush()
        return conversation

    def unarchive(self, conversation: Conversation) -> Conversation:
        conversation.status = ConversationStatus.ACTIVE
        self.session.add(conversation)
        self.session.flush()
        return conversation

    def update_summary(self, conversation: Conversation, summary: str | None) -> Conversation:
        conversation.summary = summary
        self.session.add(conversation)
        self.session.flush()
        return conversation

    def touch_last_message(self, conversation: Conversation) -> None:
        conversation.last_message_at = datetime.now(UTC)
        self.session.add(conversation)
