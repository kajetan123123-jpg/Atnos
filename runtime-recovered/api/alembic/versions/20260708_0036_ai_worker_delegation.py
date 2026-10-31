"""ai worker delegation foundation

Revision ID: 20260708_0036
Revises: 20260607_0035
Create Date: 2026-07-08 12:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260708_0036"
down_revision = "20260607_0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_worker_tasks",
        sa.Column("task_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("priority", sa.String(length=24), nullable=False, server_default="normal"),
        sa.Column("requested_by", sa.String(length=120), nullable=False, server_default="atnos"),
        sa.Column("worker_key", sa.String(length=120), nullable=True),
        sa.Column("privacy_mode", sa.String(length=40), nullable=False, server_default="strict_local"),
        sa.Column("input_summary", sa.Text(), nullable=True),
        sa.Column("redacted_prompt", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("approval_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("trace_id", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["trace_id"], ["execution_traces.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ai_worker_tasks_status"), "ai_worker_tasks", ["status"], unique=False)
    op.create_index(op.f("ix_ai_worker_tasks_task_type"), "ai_worker_tasks", ["task_type"], unique=False)
    op.create_index(op.f("ix_ai_worker_tasks_trace_id"), "ai_worker_tasks", ["trace_id"], unique=False)
    op.create_index(op.f("ix_ai_worker_tasks_worker_key"), "ai_worker_tasks", ["worker_key"], unique=False)

    op.create_table(
        "ai_worker_runs",
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("worker_key", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["ai_worker_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ai_worker_runs_task_id"), "ai_worker_runs", ["task_id"], unique=False)
    op.create_index(op.f("ix_ai_worker_runs_worker_key"), "ai_worker_runs", ["worker_key"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_ai_worker_runs_worker_key"), table_name="ai_worker_runs")
    op.drop_index(op.f("ix_ai_worker_runs_task_id"), table_name="ai_worker_runs")
    op.drop_table("ai_worker_runs")
    op.drop_index(op.f("ix_ai_worker_tasks_worker_key"), table_name="ai_worker_tasks")
    op.drop_index(op.f("ix_ai_worker_tasks_trace_id"), table_name="ai_worker_tasks")
    op.drop_index(op.f("ix_ai_worker_tasks_task_type"), table_name="ai_worker_tasks")
    op.drop_index(op.f("ix_ai_worker_tasks_status"), table_name="ai_worker_tasks")
    op.drop_table("ai_worker_tasks")
