"""capability documents and scheduling

Revision ID: 20260508_0026
Revises: 20260508_0025
Create Date: 2026-05-08 23:55:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260508_0026"
down_revision = "20260508_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("metadata_json", sa.JSON(), nullable=True))

    op.create_table(
        "scheduled_tasks",
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("prompt_template", sa.Text(), nullable=False),
        sa.Column("cron_spec", sa.String(length=120), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_status", sa.String(length=40), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_scheduled_tasks_conversation_id"), "scheduled_tasks", ["conversation_id"], unique=False)
    op.create_index(op.f("ix_scheduled_tasks_project_id"), "scheduled_tasks", ["project_id"], unique=False)
    op.create_index(op.f("ix_scheduled_tasks_user_id"), "scheduled_tasks", ["user_id"], unique=False)
    op.create_index(op.f("ix_scheduled_tasks_cron_spec"), "scheduled_tasks", ["cron_spec"], unique=False)
    op.create_index(op.f("ix_scheduled_tasks_next_run_at"), "scheduled_tasks", ["next_run_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_scheduled_tasks_next_run_at"), table_name="scheduled_tasks")
    op.drop_index(op.f("ix_scheduled_tasks_cron_spec"), table_name="scheduled_tasks")
    op.drop_index(op.f("ix_scheduled_tasks_user_id"), table_name="scheduled_tasks")
    op.drop_index(op.f("ix_scheduled_tasks_project_id"), table_name="scheduled_tasks")
    op.drop_index(op.f("ix_scheduled_tasks_conversation_id"), table_name="scheduled_tasks")
    op.drop_table("scheduled_tasks")
    op.drop_column("messages", "metadata_json")
