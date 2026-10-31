"""pc operator planning v1

Revision ID: 20260430_0021
Revises: 20260430_0020
Create Date: 2026-04-30 05:05:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260430_0021"
down_revision = "20260430_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pc_operator_plans",
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("proposed_steps_json", sa.JSON(), nullable=False),
        sa.Column("target_surface", sa.String(length=32), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("requires_approval", sa.Boolean(), nullable=False),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_pc_operator_plans_project_id"), "pc_operator_plans", ["project_id"], unique=False)
    op.create_index(op.f("ix_pc_operator_plans_status"), "pc_operator_plans", ["status"], unique=False)
    op.create_index(op.f("ix_pc_operator_plans_target_surface"), "pc_operator_plans", ["target_surface"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_pc_operator_plans_target_surface"), table_name="pc_operator_plans")
    op.drop_index(op.f("ix_pc_operator_plans_status"), table_name="pc_operator_plans")
    op.drop_index(op.f("ix_pc_operator_plans_project_id"), table_name="pc_operator_plans")
    op.drop_table("pc_operator_plans")
