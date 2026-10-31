"""persona profile defaults

Revision ID: 20260425_0009
Revises: 20260425_0008
Create Date: 2026-04-25 02:25:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260425_0009"
down_revision = "20260425_0008"
branch_labels = None
depends_on = None


persona_profile_enum = sa.Enum(
    "STRATEGIST",
    "CRITIC",
    "EXECUTOR",
    "MENTOR",
    name="personaprofile",
    native_enum=False,
    length=24,
)


def upgrade() -> None:
    op.add_column(
        "execution_policies",
        sa.Column(
            "default_persona_profile",
            persona_profile_enum,
            nullable=False,
            server_default="STRATEGIST",
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "persona_profile",
            persona_profile_enum,
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "persona_profile")
    op.drop_column("execution_policies", "default_persona_profile")
