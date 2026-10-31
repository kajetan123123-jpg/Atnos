from __future__ import annotations

from sqlalchemy import CheckConstraint, Enum, ForeignKey, ForeignKeyConstraint, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atnos.db.base import Base, IdentityMixin, TimestampMixin
from atnos.db.models.enums import ExecutionTraceKind, ExecutionTraceStatus


class ExecutionTrace(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "execution_traces"
    __table_args__ = (
        ForeignKeyConstraint(
            ["message_id", "conversation_id"],
            ["messages.id", "messages.conversation_id"],
            ondelete="CASCADE",
            name="fk_execution_traces_message_conversation",
        ),
        CheckConstraint(
            "project_id IS NOT NULL OR conversation_id IS NOT NULL OR message_id IS NOT NULL",
            name="ck_execution_traces_has_owner",
        ),
        CheckConstraint(
            "("
            "project_id IS NOT NULL AND conversation_id IS NULL AND message_id IS NULL"
            ") OR ("
            "project_id IS NULL AND conversation_id IS NOT NULL AND message_id IS NULL"
            ") OR ("
            "project_id IS NULL AND conversation_id IS NOT NULL AND message_id IS NOT NULL"
            ")",
            name="ck_execution_traces_owner_pattern",
        ),
    )

    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    message_id: Mapped[int | None] = mapped_column(
        index=True,
        nullable=True,
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    trace_kind: Mapped[ExecutionTraceKind] = mapped_column(
        Enum(ExecutionTraceKind, native_enum=False, length=40),
        nullable=False,
    )
    status: Mapped[ExecutionTraceStatus] = mapped_column(
        Enum(ExecutionTraceStatus, native_enum=False, length=24),
        default=ExecutionTraceStatus.PENDING,
        nullable=False,
    )
    step_name: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    plugin_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_summary: Mapped[str | None] = mapped_column(Text(), nullable=True)
    output_summary: Mapped[str | None] = mapped_column(Text(), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text(), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    conversation: Mapped["Conversation | None"] = relationship(
        back_populates="execution_traces",
        overlaps="execution_traces,message",
    )
    message: Mapped["Message | None"] = relationship(
        back_populates="execution_traces",
        overlaps="conversation,execution_traces",
    )
    project: Mapped["Project | None"] = relationship(back_populates="execution_traces")
