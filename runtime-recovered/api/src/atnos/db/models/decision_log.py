from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atnos.db.base import Base, IdentityMixin, TimestampMixin
from atnos.db.models.enums import DecisionLogKind, DecisionLogStatus


class DecisionLogEntry(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "decision_log"

    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    kind: Mapped[DecisionLogKind] = mapped_column(
        Enum(DecisionLogKind, native_enum=False, length=32),
        nullable=False,
    )
    summary: Mapped[str] = mapped_column(Text(), nullable=False)
    made_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[DecisionLogStatus] = mapped_column(
        Enum(DecisionLogStatus, native_enum=False, length=24),
        default=DecisionLogStatus.ACTIVE,
        nullable=False,
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_summary: Mapped[str | None] = mapped_column(Text(), nullable=True)
    evidence_message_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)

    conversation: Mapped["Conversation"] = relationship()
    project: Mapped["Project | None"] = relationship()
