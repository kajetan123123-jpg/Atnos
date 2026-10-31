"""model runtime alias

Revision ID: 20260426_0012
Revises: 20260425_0011
Create Date: 2026-04-26 01:05:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260426_0012"
down_revision = "20260425_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "model_configs",
        sa.Column("runtime_model_name", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("model_configs", "runtime_model_name")
