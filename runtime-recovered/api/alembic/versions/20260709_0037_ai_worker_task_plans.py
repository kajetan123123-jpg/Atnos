"""ai worker task plans

Revision ID: 20260709_0037
Revises: 20260708_0036
Create Date: 2026-07-09 12:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260709_0037"
down_revision = "20260708_0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_worker_tasks", sa.Column("parent_task_id", sa.Integer(), nullable=True))
    op.add_column("ai_worker_tasks", sa.Column("task_group", sa.String(length=120), nullable=True))
    op.add_column("ai_worker_tasks", sa.Column("task_role", sa.String(length=40), nullable=False, server_default="single"))
    op.add_column("ai_worker_tasks", sa.Column("sequence_index", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ai_worker_tasks", sa.Column("requires_review", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column(
        "ai_worker_tasks",
        sa.Column("verification_status", sa.String(length=32), nullable=False, server_default="unverified"),
    )
    op.create_index(op.f("ix_ai_worker_tasks_parent_task_id"), "ai_worker_tasks", ["parent_task_id"], unique=False)
    op.create_index(op.f("ix_ai_worker_tasks_task_group"), "ai_worker_tasks", ["task_group"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_ai_worker_tasks_task_group"), table_name="ai_worker_tasks")
    op.drop_index(op.f("ix_ai_worker_tasks_parent_task_id"), table_name="ai_worker_tasks")
    op.drop_column("ai_worker_tasks", "verification_status")
    op.drop_column("ai_worker_tasks", "requires_review")
    op.drop_column("ai_worker_tasks", "sequence_index")
    op.drop_column("ai_worker_tasks", "task_role")
    op.drop_column("ai_worker_tasks", "task_group")
    op.drop_column("ai_worker_tasks", "parent_task_id")
