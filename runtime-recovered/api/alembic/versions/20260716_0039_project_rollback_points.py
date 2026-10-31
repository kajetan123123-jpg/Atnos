"""project rollback points

Revision ID: 20260716_0039
Revises: 20260716_0038
Create Date: 2026-07-16 19:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260716_0039"
down_revision = "20260716_0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rollback_points",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action_draft_id", sa.Integer(), sa.ForeignKey("account_action_drafts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("execution_trace_id", sa.Integer(), sa.ForeignKey("execution_traces.id", ondelete="SET NULL"), nullable=True),
        sa.Column("label", sa.String(length=240), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="created"),
        sa.Column("target_root", sa.Text(), nullable=False),
        sa.Column("manifest_path", sa.Text(), nullable=False),
        sa.Column("changes_json", sa.JSON(), nullable=False),
        sa.Column("verification_json", sa.JSON(), nullable=False),
        sa.Column("failure_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(op.f("ix_rollback_points_project_id"), "rollback_points", ["project_id"], unique=False)
    op.create_index(op.f("ix_rollback_points_action_draft_id"), "rollback_points", ["action_draft_id"], unique=False)
    op.create_index(op.f("ix_rollback_points_execution_trace_id"), "rollback_points", ["execution_trace_id"], unique=False)
    op.create_index(op.f("ix_rollback_points_status"), "rollback_points", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_rollback_points_status"), table_name="rollback_points")
    op.drop_index(op.f("ix_rollback_points_execution_trace_id"), table_name="rollback_points")
    op.drop_index(op.f("ix_rollback_points_action_draft_id"), table_name="rollback_points")
    op.drop_index(op.f("ix_rollback_points_project_id"), table_name="rollback_points")
    op.drop_table("rollback_points")
