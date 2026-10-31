from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from atnos.db.base import Base, IdentityMixin, TimestampMixin


class SchedulerJob(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "scheduler_jobs"

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    trigger_mode: Mapped[str] = mapped_column(String(24), nullable=False, default="manual")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued", index=True)
    connector_key: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    platform_key: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    cron_expression: Mapped[str | None] = mapped_column(String(120), nullable=True)
    payload_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    retry_count: Mapped[int] = mapped_column(nullable=False, default=0)
    retry_budget: Mapped[int] = mapped_column(nullable=False, default=2)
    requires_trusted_auto: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    cooldown_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text(), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
