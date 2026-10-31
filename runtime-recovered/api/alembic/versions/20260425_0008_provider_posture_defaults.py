"""provider posture defaults

Revision ID: 20260425_0008
Revises: 20260423_0007
Create Date: 2026-04-25 01:35:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260425_0008"
down_revision = "20260423_0007"
branch_labels = None
depends_on = None


provider_posture_enum = sa.Enum(
    "PREFER_LOCAL",
    "IMPROVE_WHEN_NEEDED",
    "ASK_BEFORE_EXTERNAL",
    "STRICT_LOCAL",
    name="providerposture",
    native_enum=False,
    length=40,
)


def upgrade() -> None:
    op.add_column(
        "execution_policies",
        sa.Column(
            "default_provider_posture",
            provider_posture_enum,
            nullable=False,
            server_default="PREFER_LOCAL",
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "provider_posture",
            provider_posture_enum,
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "provider_posture")
    op.drop_column("execution_policies", "default_provider_posture")
