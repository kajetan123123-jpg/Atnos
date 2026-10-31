from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from atnos.db.models.enums import MessageRole, MessageStatus
from atnos.db.models.message import Message


class MessageRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def next_sequence_number(self, conversation_id: int) -> int:
        statement = select(func.max(Message.sequence_number)).where(Message.conversation_id == conversation_id)
        current_max = self.session.scalar(statement)
        return (current_max or 0) + 1

    def create(
        self,
        *,
        conversation_id: int,
        role: MessageRole,
        content: str,
        status: MessageStatus = MessageStatus.CREATED,
        prompt_improved: bool = False,
        attachment_only: bool = False,
        content_format: str = "markdown",
        parent_message_id: int | None = None,
        metadata_json: dict | None = None,
    ) -> Message:
        message = Message(
            conversation_id=conversation_id,
            parent_message_id=parent_message_id,
            sequence_number=self.next_sequence_number(conversation_id),
            role=role,
            status=status,
            content=content,
            content_format=content_format,
            prompt_improved=prompt_improved,
            attachment_only=attachment_only,
            metadata_json=metadata_json,
        )
        self.session.add(message)
        self.session.flush()
        return message

    def list_for_conversation(self, conversation_id: int) -> list[Message]:
        statement = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.sequence_number.asc())
        )
        return list(self.session.scalars(statement))

    def list_before_sequence(
        self,
        conversation_id: int,
        before_sequence: int,
        *,
        limit: int,
    ) -> list[Message]:
        """Return the latest earlier turns, in conversation order, with a database-side bound."""
        if limit <= 0:
            return []
        statement = (
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.sequence_number < before_sequence,
            )
            .order_by(Message.sequence_number.desc())
            .limit(limit)
        )
        return list(reversed(self.session.scalars(statement).all()))
