from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atnos.db.base import Base, IdentityMixin, TimestampMixin


class ProjectCommandTask(IdentityMixin, TimestampMixin, Base):
    """Trwaly stan wykonania polecenia wydanego w centrum projektu."""

    __tablename__ = "project_command_tasks"

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    intent: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="planning", index=True)
    phase: Mapped[str] = mapped_column(String(32), nullable=False, default="planning")
    redacted_command: Mapped[str] = mapped_column(Text(), nullable=False)
    plan_json: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False, default=list)
    result_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    evidence_json: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False, default=list)
    error_message: Mapped[str | None] = mapped_column(Text(), nullable=True)
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped["Project"] = relationship()
    conversation: Mapped["Conversation | None"] = relationship()
