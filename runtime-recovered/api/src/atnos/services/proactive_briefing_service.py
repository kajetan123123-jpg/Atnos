from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.db.models.enums import MessageRole, MessageStatus
from atnos.providers.registry import ProviderRegistry
from atnos.repositories.conversation_repository import ConversationRepository
from atnos.repositories.message_repository import MessageRepository
from atnos.services.briefing_service import BriefingPayload, BriefingService
from atnos.services.runtime_settings_service import RuntimeSettingsService


@dataclass(frozen=True, slots=True)
class BriefingMessageResult:
    conversation_id: int
    conversation_title: str
    assistant_message_id: int
    content: str
    metadata_json: dict[str, object]


class ProactiveBriefingService:
    QUIET_HOURS = timedelta(hours=8)

    def __init__(self, session: Session, *, settings: Settings, provider_registry: ProviderRegistry) -> None:
        self.session = session
        self.settings = settings
        self.provider_registry = provider_registry
        self.runtime_settings = RuntimeSettingsService(session, settings)
        self.conversations = ConversationRepository(session)
        self.messages = MessageRepository(session)
        self.briefing = BriefingService(session, settings=settings)

    def should_emit_on_bootstrap(self) -> bool:
        snapshot = self.runtime_settings.snapshot()
        now = datetime.now(UTC)
        if snapshot.last_user_interaction_at is None:
            return True
        if now - snapshot.last_user_interaction_at < self.QUIET_HOURS:
            return False
        if snapshot.last_briefing_at is None:
            return True
        return snapshot.last_briefing_at < snapshot.last_user_interaction_at

    def should_emit_scheduled_morning(self) -> bool:
        """Jeden briefing dziennie (Europa/Warszawa) z crona schedulera."""
        from zoneinfo import ZoneInfo

        snapshot = self.runtime_settings.snapshot()
        warsaw = ZoneInfo("Europe/Warsaw")
        today = datetime.now(warsaw).date()
        if snapshot.last_briefing_at is None:
            return True
        last = snapshot.last_briefing_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        return last.astimezone(warsaw).date() < today

    def generate_payload(self, *, project_id: int | None = None) -> BriefingPayload:
        return self.briefing.generate(project_id=project_id)

    def create_briefing_message(
        self,
        *,
        conversation_id: int | None = None,
        project_id: int | None = None,
        trigger: str = "manual",
    ) -> BriefingMessageResult:
        payload = self.generate_payload(project_id=project_id)
        conversation = self.conversations.get_any(conversation_id) if conversation_id is not None else None
        if conversation is None:
            conversation = self.conversations.create(
                title=f"Briefing - {project_id or 'Atnos'}",
                project_id=payload.project_id,
            )
        assistant_message = self.messages.create(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            status=MessageStatus.COMPLETED,
            content=self.briefing.render_message_content(payload),
            metadata_json={
                "kind": "briefing",
                "payload": payload.to_dict(),
                "trigger": trigger,
            },
        )
        self.conversations.touch_last_message(conversation)
        self.runtime_settings.mark_briefing_delivered()
        return BriefingMessageResult(
            conversation_id=conversation.id,
            conversation_title=conversation.title,
            assistant_message_id=assistant_message.id,
            content=assistant_message.content,
            metadata_json=dict(assistant_message.metadata_json or {}),
        )
