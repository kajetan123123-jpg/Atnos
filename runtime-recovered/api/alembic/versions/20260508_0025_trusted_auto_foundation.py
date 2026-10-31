"""trusted auto foundation

Revision ID: 20260508_0025
Revises: 20260508_0024
Create Date: 2026-05-08 21:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260508_0025"
down_revision = "20260508_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runtime_control_settings",
        sa.Column("autonomy_mode", sa.String(length=24), nullable=False, server_default="supervised"),
    )
    op.add_column(
        "runtime_control_settings",
        sa.Column("emergency_stop_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "runtime_control_settings",
        sa.Column("pause_all_jobs", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "runtime_control_settings",
        sa.Column("login_automation_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "runtime_control_settings",
        sa.Column("two_factor_automation_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "runtime_control_settings",
        sa.Column("publish_automation_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "runtime_control_settings",
        sa.Column("default_retry_budget", sa.Integer(), nullable=False, server_default="2"),
    )
    op.add_column(
        "runtime_control_settings",
        sa.Column("default_vram_floor_gb", sa.Integer(), nullable=False, server_default="4"),
    )

    op.create_table(
        "trust_grants",
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("connector_key", sa.String(length=120), nullable=False),
        sa.Column("mode", sa.String(length=24), nullable=False, server_default="blocked"),
        sa.Column("domain_allowlist_json", sa.JSON(), nullable=False),
        sa.Column("action_families_json", sa.JSON(), nullable=False),
        sa.Column("file_write_scope_json", sa.JSON(), nullable=False),
        sa.Column("login_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("two_factor_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("publish_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("browser_automation_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("connector_write_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("local_render_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("cooldown_minutes", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["account_credentials.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "account_id", "connector_key", name="uq_trust_grants_project_account_connector"),
    )
    op.create_index(op.f("ix_trust_grants_project_id"), "trust_grants", ["project_id"], unique=False)
    op.create_index(op.f("ix_trust_grants_account_id"), "trust_grants", ["account_id"], unique=False)
    op.create_index(op.f("ix_trust_grants_connector_key"), "trust_grants", ["connector_key"], unique=False)

    op.create_table(
        "credential_profiles",
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("login_identifier", sa.String(length=255), nullable=True),
        sa.Column("password_secret_ref", sa.String(length=255), nullable=True),
        sa.Column("totp_secret_ref", sa.String(length=255), nullable=True),
        sa.Column("backup_codes_secret_ref", sa.String(length=255), nullable=True),
        sa.Column("recovery_email", sa.String(length=255), nullable=True),
        sa.Column("recovery_email_secret_ref", sa.String(length=255), nullable=True),
        sa.Column("connector_mapping_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="configured"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["account_credentials.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", name="uq_credential_profiles_account"),
    )
    op.create_index(op.f("ix_credential_profiles_account_id"), "credential_profiles", ["account_id"], unique=False)

    op.create_table(
        "scheduler_jobs",
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("trigger_mode", sa.String(length=24), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("connector_key", sa.String(length=120), nullable=True),
        sa.Column("platform_key", sa.String(length=120), nullable=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cron_expression", sa.String(length=120), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_budget", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("requires_trusted_auto", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("cooldown_key", sa.String(length=160), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_scheduler_jobs_project_id"), "scheduler_jobs", ["project_id"], unique=False)
    op.create_index(op.f("ix_scheduler_jobs_job_type"), "scheduler_jobs", ["job_type"], unique=False)
    op.create_index(op.f("ix_scheduler_jobs_status"), "scheduler_jobs", ["status"], unique=False)
    op.create_index(op.f("ix_scheduler_jobs_connector_key"), "scheduler_jobs", ["connector_key"], unique=False)
    op.create_index(op.f("ix_scheduler_jobs_platform_key"), "scheduler_jobs", ["platform_key"], unique=False)
    op.create_index(op.f("ix_scheduler_jobs_scheduled_for"), "scheduler_jobs", ["scheduled_for"], unique=False)

    op.create_table(
        "execution_runs",
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("scheduler_job_id", sa.Integer(), nullable=True),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("trust_grant_id", sa.Integer(), nullable=True),
        sa.Column("run_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("connector_key", sa.String(length=120), nullable=True),
        sa.Column("platform_key", sa.String(length=120), nullable=True),
        sa.Column("risk_level", sa.String(length=24), nullable=False, server_default="medium"),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("output_json", sa.JSON(), nullable=False),
        sa.Column("artifacts_json", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["account_credentials.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scheduler_job_id"], ["scheduler_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["trust_grant_id"], ["trust_grants.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_execution_runs_project_id"), "execution_runs", ["project_id"], unique=False)
    op.create_index(op.f("ix_execution_runs_scheduler_job_id"), "execution_runs", ["scheduler_job_id"], unique=False)
    op.create_index(op.f("ix_execution_runs_account_id"), "execution_runs", ["account_id"], unique=False)
    op.create_index(op.f("ix_execution_runs_trust_grant_id"), "execution_runs", ["trust_grant_id"], unique=False)
    op.create_index(op.f("ix_execution_runs_run_type"), "execution_runs", ["run_type"], unique=False)
    op.create_index(op.f("ix_execution_runs_status"), "execution_runs", ["status"], unique=False)

    op.create_table(
        "execution_steps",
        sa.Column("execution_run_id", sa.Integer(), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("step_type", sa.String(length=64), nullable=False),
        sa.Column("step_name", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("selector_hint", sa.String(length=255), nullable=True),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("output_json", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("screenshot_path", sa.String(length=1024), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["execution_run_id"], ["execution_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_execution_steps_execution_run_id"), "execution_steps", ["execution_run_id"], unique=False)

    op.create_table(
        "media_pipeline_presets",
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("target_platform", sa.String(length=120), nullable=False, server_default="youtube"),
        sa.Column("tts_engine", sa.String(length=120), nullable=False, server_default="kokoro"),
        sa.Column("image_provider", sa.String(length=120), nullable=False, server_default="comfyui"),
        sa.Column("video_renderer", sa.String(length=120), nullable=False, server_default="moviepy-ffmpeg"),
        sa.Column("subtitle_engine", sa.String(length=120), nullable=False, server_default="faster-whisper"),
        sa.Column("prompt_style", sa.Text(), nullable=True),
        sa.Column("target_duration_sec", sa.Integer(), nullable=False, server_default="40"),
        sa.Column("vram_floor_gb", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("publish_mode", sa.String(length=32), nullable=False, server_default="trusted_auto"),
        sa.Column("cooldown_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("steps_json", sa.JSON(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_media_pipeline_presets_project_id"), "media_pipeline_presets", ["project_id"], unique=False)

    op.create_table(
        "hardware_snapshots",
        sa.Column("snapshot_type", sa.String(length=64), nullable=False, server_default="runtime"),
        sa.Column("free_vram_mb", sa.Integer(), nullable=True),
        sa.Column("used_vram_mb", sa.Integer(), nullable=True),
        sa.Column("total_vram_mb", sa.Integer(), nullable=True),
        sa.Column("free_ram_mb", sa.Integer(), nullable=True),
        sa.Column("total_ram_mb", sa.Integer(), nullable=True),
        sa.Column("free_disk_gb", sa.Integer(), nullable=True),
        sa.Column("disk_path", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="unknown"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("hardware_snapshots")
    op.drop_index(op.f("ix_media_pipeline_presets_project_id"), table_name="media_pipeline_presets")
    op.drop_table("media_pipeline_presets")
    op.drop_index(op.f("ix_execution_steps_execution_run_id"), table_name="execution_steps")
    op.drop_table("execution_steps")
    op.drop_index(op.f("ix_execution_runs_status"), table_name="execution_runs")
    op.drop_index(op.f("ix_execution_runs_run_type"), table_name="execution_runs")
    op.drop_index(op.f("ix_execution_runs_trust_grant_id"), table_name="execution_runs")
    op.drop_index(op.f("ix_execution_runs_account_id"), table_name="execution_runs")
    op.drop_index(op.f("ix_execution_runs_scheduler_job_id"), table_name="execution_runs")
    op.drop_index(op.f("ix_execution_runs_project_id"), table_name="execution_runs")
    op.drop_table("execution_runs")
    op.drop_index(op.f("ix_scheduler_jobs_scheduled_for"), table_name="scheduler_jobs")
    op.drop_index(op.f("ix_scheduler_jobs_platform_key"), table_name="scheduler_jobs")
    op.drop_index(op.f("ix_scheduler_jobs_connector_key"), table_name="scheduler_jobs")
    op.drop_index(op.f("ix_scheduler_jobs_status"), table_name="scheduler_jobs")
    op.drop_index(op.f("ix_scheduler_jobs_job_type"), table_name="scheduler_jobs")
    op.drop_index(op.f("ix_scheduler_jobs_project_id"), table_name="scheduler_jobs")
    op.drop_table("scheduler_jobs")
    op.drop_index(op.f("ix_credential_profiles_account_id"), table_name="credential_profiles")
    op.drop_table("credential_profiles")
    op.drop_index(op.f("ix_trust_grants_connector_key"), table_name="trust_grants")
    op.drop_index(op.f("ix_trust_grants_account_id"), table_name="trust_grants")
    op.drop_index(op.f("ix_trust_grants_project_id"), table_name="trust_grants")
    op.drop_table("trust_grants")

    op.drop_column("runtime_control_settings", "default_vram_floor_gb")
    op.drop_column("runtime_control_settings", "default_retry_budget")
    op.drop_column("runtime_control_settings", "publish_automation_enabled")
    op.drop_column("runtime_control_settings", "two_factor_automation_enabled")
    op.drop_column("runtime_control_settings", "login_automation_enabled")
    op.drop_column("runtime_control_settings", "pause_all_jobs")
    op.drop_column("runtime_control_settings", "emergency_stop_enabled")
    op.drop_column("runtime_control_settings", "autonomy_mode")
