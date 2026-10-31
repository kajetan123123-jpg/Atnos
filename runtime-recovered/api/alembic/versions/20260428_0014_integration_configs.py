"""integration configs

Revision ID: 20260428_0014
Revises: 20260427_0013
Create Date: 2026-04-28 21:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260428_0014"
down_revision = "20260427_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "integration_configs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("integration_key", sa.String(length=80), nullable=False),
        sa.Column("local_path", sa.String(length=1024), nullable=True),
        sa.Column("export_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("integration_key"),
    )


def downgrade() -> None:
    op.drop_table("integration_configs")
