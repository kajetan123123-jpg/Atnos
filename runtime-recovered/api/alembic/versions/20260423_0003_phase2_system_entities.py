"""phase2 system entities

Revision ID: 20260423_0003
Revises: 20260423_0002
Create Date: 2026-04-23 10:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260423_0003"
down_revision = "20260423_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("handle", sa.String(length=120), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("locale", sa.String(length=32), nullable=False, server_default="pl-PL"),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Europe/Warsaw"),
        sa.Column("is_owner", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("display_name <> ''", name="ck_users_display_name_not_empty"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_handle"), "users", ["handle"], unique=True)
    op.create_index(
        "uq_users_owner_true",
        "users",
        ["is_owner"],
        unique=True,
        sqlite_where=sa.text("is_owner = 1"),
    )

    op.create_table(
        "providers",
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("vendor", sa.String(length=80), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("transport", sa.String(length=32), nullable=False),
        sa.Column("auth_mode", sa.String(length=24), nullable=False, server_default="none"),
        sa.Column("endpoint_url", sa.Text(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("local_first", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("supports_chat", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("supports_vision", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("supports_audio_in", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("supports_audio_out", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("supports_image_generation", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_providers_key"), "providers", ["key"], unique=True)

    op.create_table(
        "model_configs",
        sa.Column("provider_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=160), nullable=False),
        sa.Column("label", sa.String(length=160), nullable=False),
        sa.Column("task", sa.String(length=32), nullable=False),
        sa.Column("quality_tier", sa.String(length=16), nullable=False, server_default="standard"),
        sa.Column("context_window", sa.Integer(), nullable=True),
        sa.Column("max_output_tokens", sa.Integer(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("supports_tools", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("supports_vision", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_model_configs_key"), "model_configs", ["key"], unique=True)
    op.create_index(op.f("ix_model_configs_provider_id"), "model_configs", ["provider_id"], unique=False)

    op.create_table(
        "plugins",
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False, server_default="builtin"),
        sa.Column("permission_policy", sa.String(length=24), nullable=False, server_default="ask"),
        sa.Column("requires_confirmation", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("config_json", sa.JSON(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_plugins_key"), "plugins", ["key"], unique=True)

    op.create_table(
        "execution_policies",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("automation_level", sa.String(length=24), nullable=False, server_default="assisted"),
        sa.Column("verification_mode", sa.String(length=24), nullable=False, server_default="smart"),
        sa.Column("allow_local_file_read", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("allow_local_file_write", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("allow_web_read", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("allow_external_fallback", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("prefer_local_llm", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("max_daily_cost", sa.Float(), nullable=True),
        sa.Column("max_monthly_cost", sa.Float(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("max_daily_cost IS NULL OR max_daily_cost >= 0", name="ck_execution_policies_daily_cost"),
        sa.CheckConstraint("max_monthly_cost IS NULL OR max_monthly_cost >= 0", name="ck_execution_policies_monthly_cost"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )

    op.create_table(
        "voice_profiles",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("voice_key", sa.String(length=120), nullable=False),
        sa.Column("provider_mode", sa.String(length=24), nullable=False, server_default="remote"),
        sa.Column("style", sa.String(length=80), nullable=True),
        sa.Column("language", sa.String(length=24), nullable=False, server_default="pl-PL"),
        sa.Column("speaking_rate", sa.String(length=24), nullable=True),
        sa.Column("supports_realtime", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auto_read_responses", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["provider_id"], ["providers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_voice_profiles_provider_id"), "voice_profiles", ["provider_id"], unique=False)
    op.create_index(op.f("ix_voice_profiles_user_id"), "voice_profiles", ["user_id"], unique=False)
    op.create_index(
        "uq_voice_profiles_default_true",
        "voice_profiles",
        ["is_default"],
        unique=True,
        sqlite_where=sa.text("is_default = 1"),
    )

    op.create_table(
        "user_style_preferences",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("response_length", sa.String(length=16), nullable=False, server_default="medium"),
        sa.Column("tone", sa.String(length=24), nullable=False, server_default="natural"),
        sa.Column("structure", sa.String(length=24), nullable=False, server_default="mixed"),
        sa.Column("criticality_level", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("supportiveness_level", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("professional_default", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("criticality_level BETWEEN 1 AND 5", name="ck_user_style_preferences_criticality_range"),
        sa.CheckConstraint("supportiveness_level BETWEEN 1 AND 5", name="ck_user_style_preferences_supportiveness_range"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("user_style_preferences")
    op.drop_index("uq_voice_profiles_default_true", table_name="voice_profiles")
    op.drop_index(op.f("ix_voice_profiles_user_id"), table_name="voice_profiles")
    op.drop_index(op.f("ix_voice_profiles_provider_id"), table_name="voice_profiles")
    op.drop_table("voice_profiles")
    op.drop_table("execution_policies")
    op.drop_index(op.f("ix_plugins_key"), table_name="plugins")
    op.drop_table("plugins")
    op.drop_index(op.f("ix_model_configs_provider_id"), table_name="model_configs")
    op.drop_index(op.f("ix_model_configs_key"), table_name="model_configs")
    op.drop_table("model_configs")
    op.drop_index(op.f("ix_providers_key"), table_name="providers")
    op.drop_table("providers")
    op.drop_index("uq_users_owner_true", table_name="users")
    op.drop_index(op.f("ix_users_handle"), table_name="users")
    op.drop_table("users")
