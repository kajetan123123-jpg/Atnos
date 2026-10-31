"""phase3 external egress policy

Revision ID: 20260423_0006
Revises: 20260423_0005
Create Date: 2026-04-23 11:05:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260423_0006"
down_revision = "20260423_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "execution_policies",
        sa.Column("allow_external_attachment_metadata", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "execution_policies",
        sa.Column("allow_external_attachment_content", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "execution_policies",
        sa.Column("max_external_attachment_text_chars", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("execution_policies", "max_external_attachment_text_chars")
    op.drop_column("execution_policies", "allow_external_attachment_content")
    op.drop_column("execution_policies", "allow_external_attachment_metadata")
