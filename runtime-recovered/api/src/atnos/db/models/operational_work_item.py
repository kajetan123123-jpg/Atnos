from __future__ import annotations

from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atnos.db.base import Base, IdentityMixin, TimestampMixin
from atnos.db.models.enums import (
    OperationalWorkItemPriority,
    OperationalWorkItemReviewState,
    OperationalWorkItemStatus,
)


class OperationalWorkItem(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "operational_work_items"

    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_note_id: Mapped[int | None] = mapped_column(
        ForeignKey("self_improvement_notes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text(), nullable=False)
    next_step: Mapped[str | None] = mapped_column(Text(), nullable=True)
    status: Mapped[OperationalWorkItemStatus] = mapped_column(
        Enum(OperationalWorkItemStatus, native_enum=False, length=24),
        default=OperationalWorkItemStatus.ACTIVE,
        nullable=False,
    )
    priority: Mapped[OperationalWorkItemPriority] = mapped_column(
        Enum(OperationalWorkItemPriority, native_enum=False, length=8),
        default=OperationalWorkItemPriority.P1,
        nullable=False,
    )
    review_state: Mapped[OperationalWorkItemReviewState] = mapped_column(
        Enum(OperationalWorkItemReviewState, native_enum=False, length=24),
        default=OperationalWorkItemReviewState.UNREVIEWED,
        nullable=False,
    )
    blocked_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)

    project: Mapped["Project | None"] = relationship()
    source_note: Mapped["SelfImprovementNote | None"] = relationship()
