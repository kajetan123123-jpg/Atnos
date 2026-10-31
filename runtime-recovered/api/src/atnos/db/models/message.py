from __future__ import annotations

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atnos.db.base import Base, IdentityMixin, TimestampMixin
from atnos.db.models.enums import MessageRole, MessageStatus


class Message(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence_number", name="uq_messages_conversation_sequence"),
        UniqueConstraint("id", "conversation_id", name="uq_messages_id_conversation"),
    )

    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    parent_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[MessageRole] = mapped_column(
        Enum(MessageRole, native_enum=False, length=32),
        nullable=False,
    )
    status: Mapped[MessageStatus] = mapped_column(
        Enum(MessageStatus, native_enum=False, length=32),
        default=MessageStatus.CREATED,
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text(), nullable=False)
    content_format: Mapped[str] = mapped_column(String(24), default="markdown", nullable=False)
    prompt_improved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attachment_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
    parent_message: Mapped["Message | None"] = relationship(remote_side="Message.id")
    attachments: Mapped[list["Attachment"]] = relationship(
        back_populates="message",
        overlaps="conversation,attachments",
    )
    execution_traces: Mapped[list["ExecutionTrace"]] = relationship(
        back_populates="message",
        overlaps="conversation,execution_traces",
    )
    memory_entries: Mapped[list["ProjectMemory"]] = relationship(
        back_populates="source_message",
        overlaps="source_conversation,memory_entries",
    )
