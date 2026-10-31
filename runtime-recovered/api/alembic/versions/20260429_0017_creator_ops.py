"""creator operations orchestrator foundation

Revision ID: 20260429_0017
Revises: 20260429_0016
Create Date: 2026-04-29 01:15:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260429_0017"
down_revision = "20260429_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "creator_projects",
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("project_type", sa.String(length=48), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("persona_type", sa.String(length=40), nullable=False),
        sa.Column("disclosure_policy", sa.String(length=40), nullable=False),
        sa.Column("niche", sa.String(length=240), nullable=True),
        sa.Column("target_audience", sa.String(length=240), nullable=True),
        sa.Column("monetization_goal", sa.String(length=240), nullable=True),
        sa.Column("content_boundaries", sa.Text(), nullable=True),
        sa.Column("safety_boundaries", sa.Text(), nullable=True),
        sa.Column("platforms_json", sa.JSON(), nullable=False),
        sa.Column("linked_accounts_json", sa.JSON(), nullable=False),
        sa.Column("linked_integrations_json", sa.JSON(), nullable=False),
        sa.Column("operating_mode", sa.String(length=32), nullable=False),
        sa.Column("approval_policy", sa.String(length=32), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_creator_projects_project_type"), "creator_projects", ["project_type"], unique=False)

    op.create_table(
        "creator_work_items",
        sa.Column("creator_project_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("work_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("risk_level", sa.String(length=24), nullable=False),
        sa.Column("approval_required", sa.Boolean(), nullable=False),
        sa.Column("platform", sa.String(length=80), nullable=True),
        sa.Column("draft_payload_json", sa.JSON(), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("next_action", sa.String(length=240), nullable=True),
        sa.Column("safety_flags_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["creator_project_id"], ["creator_projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_creator_work_items_creator_project_id"),
        "creator_work_items",
        ["creator_project_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_creator_work_items_creator_project_id"), table_name="creator_work_items")
    op.drop_table("creator_work_items")
    op.drop_index(op.f("ix_creator_projects_project_type"), table_name="creator_projects")
    op.drop_table("creator_projects")
