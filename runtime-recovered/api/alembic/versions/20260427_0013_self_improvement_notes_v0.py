"""self improvement notes v0

Revision ID: 20260427_0013
Revises: 20260426_0012
Create Date: 2026-04-27 17:10:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260427_0013"
down_revision = "20260426_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "self_improvement_notes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("view", sa.String(length=80), nullable=False),
        sa.Column("friction", sa.Text(), nullable=False),
        sa.Column("severity", sa.Enum("P0", "P1", "P2", name="selfimprovementseverity", native_enum=False, length=8), nullable=False),
        sa.Column("proposed_fix", sa.Text(), nullable=True),
        sa.Column("chosen_next_patch", sa.Text(), nullable=True),
        sa.Column("decision", sa.Enum("ACCEPTED", "FOLLOW_UP", "REJECTED", "UNDECIDED", name="selfimprovementdecision", native_enum=False, length=24), nullable=False, server_default="UNDECIDED"),
        sa.Column("status", sa.Enum("OPEN", "SELECTED", "CLOSED", name="selfimprovementstatus", native_enum=False, length=16), nullable=False, server_default="OPEN"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("self_improvement_notes")
