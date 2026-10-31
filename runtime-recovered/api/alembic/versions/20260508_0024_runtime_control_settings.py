"""runtime control settings

Revision ID: 20260508_0024
Revises: 20260501_0023
Create Date: 2026-05-08 16:45:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260508_0024"
down_revision = "20260501_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runtime_control_settings",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("browser_control_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("external_actions_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("scheduler_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("oauth_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("voice_stt_engine", sa.String(length=40), nullable=False, server_default="browser-speech"),
        sa.Column("voice_stt_model_path", sa.String(length=1024), nullable=True),
        sa.Column("voice_stt_device", sa.String(length=40), nullable=False, server_default="cpu"),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(
        op.f("ix_runtime_control_settings_user_id"),
        "runtime_control_settings",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_runtime_control_settings_user_id"), table_name="runtime_control_settings")
    op.drop_table("runtime_control_settings")
