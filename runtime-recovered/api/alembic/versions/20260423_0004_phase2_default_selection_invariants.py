"""phase2 default selection invariants

Revision ID: 20260423_0004
Revises: 20260423_0003
Create Date: 2026-04-23 10:35:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260423_0004"
down_revision = "20260423_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_model_configs_provider_task_default_true",
        "model_configs",
        ["provider_id", "task"],
        unique=True,
        sqlite_where=sa.text("is_default = 1"),
    )
    op.drop_index("uq_voice_profiles_default_true", table_name="voice_profiles")
    op.create_index(
        "uq_voice_profiles_user_default_true",
        "voice_profiles",
        ["user_id"],
        unique=True,
        sqlite_where=sa.text("is_default = 1"),
    )


def downgrade() -> None:
    op.drop_index("uq_voice_profiles_user_default_true", table_name="voice_profiles")
    op.create_index(
        "uq_voice_profiles_default_true",
        "voice_profiles",
        ["is_default"],
        unique=True,
        sqlite_where=sa.text("is_default = 1"),
    )
    op.drop_index("uq_model_configs_provider_task_default_true", table_name="model_configs")
