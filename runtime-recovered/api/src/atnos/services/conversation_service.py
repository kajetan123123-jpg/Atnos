from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from atnos.db.models.conversation import Conversation
from atnos.db.models.enums import (
    AttachmentKind,
    ExecutionTraceKind,
    ExecutionTraceStatus,
    MessageRole,
    MessageStatus,
)
from atnos.db.models.execution_trace import ExecutionTrace
from atnos.db.models.message import Message
from atnos.repositories.attachment_repository import AttachmentRepository
from atnos.repositories.conversation_repository import ConversationRepository
from atnos.repositories.execution_trace_repository import ExecutionTraceRepository
from atnos.repositories.message_repository import MessageRepository
from atnos.services.attachment_understanding_service import AttachmentUnderstandingService
from atnos.core.config import Settings


@dataclass(slots=True, frozen=True)
class AttachmentDraft:
    filename: str
    storage_path: str
    kind: AttachmentKind
    detected_kind: str | None = None
    understanding_status: str | None = None
    understanding_reason: str | None = None
    preview_text: str | None = None
    truncated: bool = False
    media_type: str | None = None
    file_extension: str | None = None
    size_bytes: int | None = None
    extracted_text: str | None = None


@dataclass(slots=True, frozen=True)
class ExecutionPreferences:
    response_style: str | None = None
    persona_profile: str | None = None
    provider_posture: str | None = None
    quality_profile: str | None = None
    memory_enabled: bool | None = None
    file_enabled: bool | None = None
    internet_enabled: bool | None = None
    work_mode: str | None = None


@dataclass(slots=True, frozen=True)
class ActiveDocumentAction:
    attachment_id: str
    action: str


@dataclass(slots=True, frozen=True)
class ConversationTurnRequest:
    content: str
    conversation_id: int | None = None
    conversation_title: str | None = None
    project_id: int | None = None
    role_profile_id: int | None = None
    prompt_improved: bool = False
    force_external: bool = False
    execution_preferences: ExecutionPreferences | None = None
    active_document_action: ActiveDocumentAction | None = None
    attachments: tuple[AttachmentDraft, ...] = field(default_factory=tuple)
    message_role: MessageRole = MessageRole.USER
    message_metadata_json: dict | None = None
    assistant_metadata_json: dict | None = None


@dataclass(slots=True, frozen=True)
class ConversationTurnResult:
    conversation: Conversation
    user_message: Message
    traces: tuple[ExecutionTrace, ...]


class ConversationService:
    def __init__(self, session: Session, *, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings
        self.conversations = ConversationRepository(session)
        self.messages = MessageRepository(session)
        self.attachments = AttachmentRepository(session)
        self.execution_traces = ExecutionTraceRepository(session)
        self.attachment_understanding = AttachmentUnderstandingService()

    def process_user_turn(self, request: ConversationTurnRequest) -> ConversationTurnResult:
        normalized_content = self._normalize_content(request.content)
        attachment_only = not normalized_content and bool(request.attachments)
        if not normalized_content and not request.attachments:
            raise ValueError("Conversation turn must include content or at least one attachment.")

        if request.conversation_id is not None:
            conversation = self.conversations.get(request.conversation_id)
            if conversation is None:
                raise ValueError(f"Conversation {request.conversation_id} does not exist.")
            if request.project_id is not None and request.project_id != conversation.project_id:
                raise ValueError("Request project_id does not match the existing conversation project.")
            if request.role_profile_id is not None and request.role_profile_id != conversation.role_profile_id:
                raise ValueError("Request role_profile_id does not match the existing conversation role profile.")
            if self._should_auto_title_existing_conversation(conversation):
                conversation = self.conversations.update_title(
                    conversation,
                    self._derive_title(normalized_content, attachment_only=attachment_only),
                )
        else:
            conversation = self.conversations.create(
                title=request.conversation_title
                or self._derive_title(normalized_content, attachment_only=attachment_only),
                project_id=request.project_id,
                role_profile_id=request.role_profile_id,
            )

        user_message = self.messages.create(
            conversation_id=conversation.id,
            role=request.message_role,
            status=MessageStatus.COMPLETED,
            content=normalized_content,
            prompt_improved=request.prompt_improved,
            attachment_only=attachment_only,
            metadata_json=request.message_metadata_json,
        )

        for draft in request.attachments:
            understanding = self.attachment_understanding.understand_existing(
                filename=draft.filename,
                content_type=draft.media_type,
                storage_path=draft.storage_path,
                extracted_text=draft.extracted_text or draft.preview_text,
                app_root=self.settings.app_root if self.settings is not None else None,
                uploads_dir=self.settings.uploads_dir if self.settings is not None else None,
            )
            file_extension = draft.file_extension
            if not file_extension:
                suffix = Path(draft.filename).suffix.lower()
                file_extension = suffix or None
            self.attachments.create(
                filename=draft.filename,
                storage_path=draft.storage_path,
                kind=understanding.attachment_kind,
                conversation_id=conversation.id,
                message_id=user_message.id,
                media_type=draft.media_type or understanding.content_type,
                file_extension=file_extension,
                size_bytes=draft.size_bytes,
                extracted_text=understanding.extracted_text,
                metadata_json=self.attachment_understanding.build_metadata(understanding),
            )

        context_trace = self.execution_traces.create(
            step_name="conversation-turn-ingest",
            trace_kind=ExecutionTraceKind.CONTEXT,
            status=ExecutionTraceStatus.COMPLETED,
            conversation_id=conversation.id,
            message_id=user_message.id,
            input_summary=f"user_turn attachments={len(request.attachments)}",
            output_summary="conversation context assembled",
        )
        answer_trace = self.execution_traces.create(
            step_name="assistant-response-placeholder",
            trace_kind=ExecutionTraceKind.ANSWER,
            status=ExecutionTraceStatus.PENDING,
            conversation_id=conversation.id,
            message_id=user_message.id,
            input_summary="awaiting provider execution",
        )

        self.conversations.touch_last_message(conversation)
        self.session.flush()

        return ConversationTurnResult(
            conversation=conversation,
            user_message=user_message,
            traces=(context_trace, answer_trace),
        )

    @staticmethod
    def _derive_title(content: str, *, attachment_only: bool = False) -> str:
        if attachment_only:
            return "Rozmowa o pliku"

        normalized = ConversationService._normalize_content(content)
        if not normalized:
            return "Nowa rozmowa"

        return ConversationService._truncate_title(normalized)

    def _should_auto_title_existing_conversation(self, conversation: Conversation) -> bool:
        existing_messages = self.messages.list_for_conversation(conversation.id)
        if existing_messages:
            return False

        normalized_title = self._normalize_content(conversation.title)
        return normalized_title in {
            "",
            "Nowa rozmowa",
            "New conversation",
            "Rozmowa o pliku",
            "Rozmowa glosowa",
        }

    @staticmethod
    def _normalize_content(content: str) -> str:
        return " ".join(content.strip().split())

    @staticmethod
    def _truncate_title(content: str, *, max_length: int = 60) -> str:
        if len(content) <= max_length:
            return content

        candidate = content[:max_length].rstrip()
        if " " in candidate:
            candidate = candidate.rsplit(" ", 1)[0].rstrip()

        return candidate or content[:max_length].rstrip()
