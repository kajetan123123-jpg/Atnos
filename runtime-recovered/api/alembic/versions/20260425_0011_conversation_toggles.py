"""conversation toggles

Revision ID: 20260425_0011
Revises: 20260425_0010
Create Date: 2026-04-25 23:05:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260425_0011"
down_revision = "20260425_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("memory_enabled_override", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("file_enabled_override", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("internet_enabled_override", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "internet_enabled_override")
    op.drop_column("conversations", "file_enabled_override")
    op.drop_column("conversations", "memory_enabled_override")
