from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atnos.db.base import Base, IdentityMixin, TimestampMixin
from atnos.db.models.enums import ConversationStatus, PersonaProfile, ProviderPosture


class Conversation(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_conversations_id_project"),
    )

    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    role_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("role_profiles.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text(), nullable=True)
    response_style_override: Mapped[str | None] = mapped_column(String(40), nullable=True)
    memory_enabled_override: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    file_enabled_override: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    internet_enabled_override: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    persona_profile_override: Mapped[PersonaProfile | None] = mapped_column(
        Enum(PersonaProfile, native_enum=False, length=24),
        nullable=True,
    )
    provider_posture_override: Mapped[ProviderPosture | None] = mapped_column(
        Enum(ProviderPosture, native_enum=False, length=40),
        nullable=True,
    )
    active_work_mode: Mapped[str] = mapped_column(String(40), default="auto", nullable=False)
    status: Mapped[ConversationStatus] = mapped_column(
        Enum(ConversationStatus, native_enum=False, length=32),
        default=ConversationStatus.ACTIVE,
        nullable=False,
    )
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped["Project | None"] = relationship(back_populates="conversations")
    role_profile: Mapped["RoleProfile | None"] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.sequence_number",
    )
    attachments: Mapped[list["Attachment"]] = relationship(
        back_populates="conversation",
        overlaps="message",
    )
    execution_traces: Mapped[list["ExecutionTrace"]] = relationship(
        back_populates="conversation",
        overlaps="message",
    )
    memory_entries: Mapped[list["ProjectMemory"]] = relationship(
        back_populates="source_conversation",
        overlaps="source_message,memory_entries",
    )
