"""phase3 message attachment-only flag

Revision ID: 20260423_0005
Revises: 20260423_0004
Create Date: 2026-04-23 10:50:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260423_0005"
down_revision = "20260423_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("attachment_only", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("messages", "attachment_only")
