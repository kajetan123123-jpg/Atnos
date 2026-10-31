from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from atnos.db.base import Base, IdentityMixin, TimestampMixin


class ExecutionRun(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "execution_runs"

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scheduler_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("scheduler_jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("account_credentials.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    trust_grant_id: Mapped[int | None] = mapped_column(
        ForeignKey("trust_grants.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    run_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued", index=True)
    goal: Mapped[str] = mapped_column(Text(), nullable=False)
    connector_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    platform_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    risk_level: Mapped[str] = mapped_column(String(24), nullable=False, default="medium")
    input_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    output_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    artifacts_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text(), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExecutionStep(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "execution_steps"

    execution_run_id: Mapped[int] = mapped_column(
        ForeignKey("execution_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_index: Mapped[int] = mapped_column(nullable=False)
    step_type: Mapped[str] = mapped_column(String(64), nullable=False)
    step_name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    selector_hint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    output_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text(), nullable=True)
    screenshot_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
