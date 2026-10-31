"""durable project command task state

Revision ID: 20260803_0042
Revises: 20260717_0041
Create Date: 2026-08-03 15:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260803_0042"
down_revision = "20260717_0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_command_tasks",
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("intent", sa.String(length=48), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="planning"),
        sa.Column("phase", sa.String(length=32), nullable=False, server_default="planning"),
        sa.Column("redacted_command", sa.Text(), nullable=False),
        sa.Column("plan_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_project_command_tasks_project_id", "project_command_tasks", ["project_id"])
    op.create_index("ix_project_command_tasks_conversation_id", "project_command_tasks", ["conversation_id"])
    op.create_index("ix_project_command_tasks_intent", "project_command_tasks", ["intent"])
    op.create_index("ix_project_command_tasks_status", "project_command_tasks", ["status"])


def downgrade() -> None:
    op.drop_index("ix_project_command_tasks_status", table_name="project_command_tasks")
    op.drop_index("ix_project_command_tasks_intent", table_name="project_command_tasks")
    op.drop_index("ix_project_command_tasks_conversation_id", table_name="project_command_tasks")
    op.drop_index("ix_project_command_tasks_project_id", table_name="project_command_tasks")
    op.drop_table("project_command_tasks")
