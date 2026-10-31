"""operational memory work items

Revision ID: 20260523_0033
Revises: 20260513_0032
Create Date: 2026-05-23 16:30:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260523_0033"
down_revision = "20260513_0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operational_work_items",
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("source_note_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("next_step", sa.Text(), nullable=True),
        sa.Column("status", sa.Enum("ACTIVE", "BLOCKED", "WAITING", "DONE", name="operationalworkitemstatus", native_enum=False, length=24), nullable=False, server_default="ACTIVE"),
        sa.Column("priority", sa.Enum("P0", "P1", "P2", name="operationalworkitempriority", native_enum=False, length=8), nullable=False, server_default="P1"),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_note_id"], ["self_improvement_notes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_operational_work_items_project_id"), "operational_work_items", ["project_id"], unique=False)
    op.create_index(op.f("ix_operational_work_items_source_note_id"), "operational_work_items", ["source_note_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_operational_work_items_source_note_id"), table_name="operational_work_items")
    op.drop_index(op.f("ix_operational_work_items_project_id"), table_name="operational_work_items")
    op.drop_table("operational_work_items")
