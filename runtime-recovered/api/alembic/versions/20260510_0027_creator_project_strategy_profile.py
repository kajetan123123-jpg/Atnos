"""creator project strategy profile

Revision ID: 20260510_0027
Revises: 20260508_0026
Create Date: 2026-05-10 09:55:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260510_0027"
down_revision = "20260508_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("creator_projects", sa.Column("strategy_profile_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("creator_projects", "strategy_profile_json")
