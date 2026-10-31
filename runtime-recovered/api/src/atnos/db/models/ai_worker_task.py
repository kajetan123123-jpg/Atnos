from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atnos.db.base import Base, IdentityMixin, TimestampMixin


class AiWorkerTask(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "ai_worker_tasks"

    task_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    priority: Mapped[str] = mapped_column(String(24), nullable=False, default="normal")
    requested_by: Mapped[str] = mapped_column(String(120), nullable=False, default="atnos")
    worker_key: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    privacy_mode: Mapped[str] = mapped_column(String(40), nullable=False, default="strict_local")
    parent_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_worker_tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    task_group: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    task_role: Mapped[str] = mapped_column(String(40), nullable=False, default="single")
    sequence_index: Mapped[int] = mapped_column(nullable=False, default=0)
    requires_review: Mapped[bool] = mapped_column(default=False, nullable=False)
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unverified")
    input_summary: Mapped[str | None] = mapped_column(Text(), nullable=True)
    redacted_prompt: Mapped[str] = mapped_column(Text(), nullable=False)
    payload_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    result_summary: Mapped[str | None] = mapped_column(Text(), nullable=True)
    result_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text(), nullable=True)
    blocked_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    approval_required: Mapped[bool] = mapped_column(default=False, nullable=False)
    trace_id: Mapped[int | None] = mapped_column(
        ForeignKey("execution_traces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    runs: Mapped[list["AiWorkerRun"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    parent: Mapped["AiWorkerTask | None"] = relationship(
        remote_side="AiWorkerTask.id",
        back_populates="children",
    )
    children: Mapped[list["AiWorkerTask"]] = relationship(
        back_populates="parent",
        order_by="AiWorkerTask.sequence_index",
    )


class AiWorkerRun(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "ai_worker_runs"

    task_id: Mapped[int] = mapped_column(
        ForeignKey("ai_worker_tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    worker_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(nullable=True)
    result_summary: Mapped[str | None] = mapped_column(Text(), nullable=True)
    result_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text(), nullable=True)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)

    task: Mapped[AiWorkerTask] = relationship(back_populates="runs")
