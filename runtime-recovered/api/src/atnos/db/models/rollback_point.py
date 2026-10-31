from __future__ import annotations

"""Trwaly punkt cofniecia dla kontrolowanej zmiany w projekcie."""

from sqlalchemy import ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atnos.db.base import Base, IdentityMixin, TimestampMixin


class RollbackPoint(IdentityMixin, TimestampMixin, Base):
    """Manifest plikow sprzed bezpiecznej zmiany wraz z wynikiem weryfikacji."""

    __tablename__ = "rollback_points"

    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    action_draft_id: Mapped[int | None] = mapped_column(
        ForeignKey("account_action_drafts.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    execution_trace_id: Mapped[int | None] = mapped_column(
        ForeignKey("execution_traces.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    label: Mapped[str] = mapped_column(String(240), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created", index=True)
    target_root: Mapped[str] = mapped_column(Text(), nullable=False)
    manifest_path: Mapped[str] = mapped_column(Text(), nullable=False)
    changes_json: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False, default=list)
    verification_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    failure_detail: Mapped[str | None] = mapped_column(Text(), nullable=True)

    project: Mapped["Project | None"] = relationship(back_populates="rollback_points")
