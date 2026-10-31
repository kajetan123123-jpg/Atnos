"""conversation settings overrides

Revision ID: 20260425_0010
Revises: 20260425_0009
Create Date: 2026-04-25 20:15:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260425_0010"
down_revision = "20260425_0009"
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
        "conversations",
        sa.Column("response_style_override", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("persona_profile_override", persona_profile_enum, nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("provider_posture_override", provider_posture_enum, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "provider_posture_override")
    op.drop_column("conversations", "persona_profile_override")
    op.drop_column("conversations", "response_style_override")
