"""content calendar planning layer

Revision ID: 20260430_0020
Revises: 20260429_0019
Create Date: 2026-04-30 00:50:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260430_0020"
down_revision = "20260429_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "content_calendar_items",
        sa.Column("creator_project_id", sa.Integer(), nullable=False),
        sa.Column("work_item_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("platform", sa.String(length=80), nullable=True),
        sa.Column("content_type", sa.String(length=80), nullable=False),
        sa.Column("planned_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("risk_level", sa.String(length=24), nullable=False),
        sa.Column("approval_required", sa.Boolean(), nullable=False),
        sa.Column("linked_app_link_id", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["creator_project_id"], ["creator_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["linked_app_link_id"], ["project_app_links.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["work_item_id"], ["creator_work_items.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_content_calendar_items_creator_project_id"),
        "content_calendar_items",
        ["creator_project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_content_calendar_items_work_item_id"),
        "content_calendar_items",
        ["work_item_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_content_calendar_items_linked_app_link_id"),
        "content_calendar_items",
        ["linked_app_link_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_content_calendar_items_planned_date"),
        "content_calendar_items",
        ["planned_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_content_calendar_items_planned_date"), table_name="content_calendar_items")
    op.drop_index(op.f("ix_content_calendar_items_linked_app_link_id"), table_name="content_calendar_items")
    op.drop_index(op.f("ix_content_calendar_items_work_item_id"), table_name="content_calendar_items")
    op.drop_index(op.f("ix_content_calendar_items_creator_project_id"), table_name="content_calendar_items")
    op.drop_table("content_calendar_items")
