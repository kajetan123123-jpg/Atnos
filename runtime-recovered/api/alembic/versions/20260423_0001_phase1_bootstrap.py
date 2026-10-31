"""phase1 bootstrap

Revision ID: 20260423_0001
Revises:
Create Date: 2026-04-23 05:20:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260423_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bootstrap_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("key", name="uq_bootstrap_records_key"),
    )


def downgrade() -> None:
    op.drop_table("bootstrap_records")
