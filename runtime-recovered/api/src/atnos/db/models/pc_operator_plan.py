from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from atnos.db.base import Base, IdentityMixin, TimestampMixin


class PCOperatorPlan(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "pc_operator_plans"

    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    goal: Mapped[str] = mapped_column(Text(), nullable=False)
    proposed_steps_json: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    target_surface: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    requires_approval: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=True)
    blocked_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
