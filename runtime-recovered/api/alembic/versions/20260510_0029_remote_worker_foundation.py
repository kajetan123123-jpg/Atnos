"""remote worker foundation

Revision ID: 20260510_0029
Revises: 20260510_0028
Create Date: 2026-05-10 16:45:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260510_0029"
down_revision = "20260510_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "remote_nodes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("node_type", sa.String(length=40), nullable=False, server_default="ubuntu-worker"),
        sa.Column("platform", sa.String(length=40), nullable=False, server_default="linux"),
        sa.Column("hostname", sa.String(length=255), nullable=True),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("ssh_port", sa.Integer(), nullable=False, server_default="22"),
        sa.Column("ssh_user", sa.String(length=120), nullable=False, server_default="atnos"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="provisioning"),
        sa.Column("trust_mode", sa.String(length=24), nullable=False, server_default="trusted_auto"),
        sa.Column("transport", sa.String(length=24), nullable=False, server_default="websocket"),
        sa.Column("display_mode", sa.String(length=24), nullable=False, server_default="unknown"),
        sa.Column("browser_default_target", sa.String(length=40), nullable=False, server_default="personal_profile"),
        sa.Column("public_capabilities_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("pause_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_remote_nodes_status"), "remote_nodes", ["status"], unique=False)
    op.create_index(op.f("ix_remote_nodes_last_seen_at"), "remote_nodes", ["last_seen_at"], unique=False)

    op.create_table(
        "remote_node_credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("node_id", sa.Integer(), nullable=False),
        sa.Column("enrollment_token_hash", sa.String(length=255), nullable=True),
        sa.Column("credential_token_hash", sa.String(length=255), nullable=True),
        sa.Column("cert_fingerprint", sa.String(length=255), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rotation_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["node_id"], ["remote_nodes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_remote_node_credentials_node_id"), "remote_node_credentials", ["node_id"], unique=True)

    op.create_table(
        "remote_node_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("node_id", sa.Integer(), nullable=False),
        sa.Column("transport", sa.String(length=24), nullable=False, server_default="websocket"),
        sa.Column("remote_addr", sa.String(length=255), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(["node_id"], ["remote_nodes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_remote_node_sessions_node_id"), "remote_node_sessions", ["node_id"], unique=False)

    op.create_table(
        "remote_node_capability_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("node_id", sa.Integer(), nullable=False),
        sa.Column("cpu_percent", sa.Float(), nullable=True),
        sa.Column("load_avg_json", sa.JSON(), nullable=True),
        sa.Column("ram_total_mb", sa.Integer(), nullable=True),
        sa.Column("ram_free_mb", sa.Integer(), nullable=True),
        sa.Column("disk_free_gb", sa.Float(), nullable=True),
        sa.Column("network_online", sa.Boolean(), nullable=True),
        sa.Column("sudo_available", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ollama_ready", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("browser_ready", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("desktop_bridge_ready", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ffmpeg_ready", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("docker_ready", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("display_mode", sa.String(length=24), nullable=False, server_default="unknown"),
        sa.Column("browser_mode", sa.String(length=40), nullable=False, server_default="personal_profile"),
        sa.Column("python_version", sa.String(length=120), nullable=True),
        sa.Column("ffmpeg_version", sa.String(length=255), nullable=True),
        sa.Column("raw_payload_json", sa.JSON(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["node_id"], ["remote_nodes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_remote_node_capability_snapshots_node_id"), "remote_node_capability_snapshots", ["node_id"], unique=False)
    op.create_index(op.f("ix_remote_node_capability_snapshots_captured_at"), "remote_node_capability_snapshots", ["captured_at"], unique=False)

    op.create_table(
        "remote_execution_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("origin", sa.String(length=24), nullable=False, server_default="manual"),
        sa.Column("requested_by", sa.String(length=120), nullable=True),
        sa.Column("task_kind", sa.String(length=120), nullable=False),
        sa.Column("routing_target", sa.String(length=24), nullable=False, server_default="remote"),
        sa.Column("assigned_node_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("label", sa.String(length=160), nullable=True),
        sa.Column("request_payload_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("error_json", sa.JSON(), nullable=False),
        sa.Column("fallback_reason", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["assigned_node_id"], ["remote_nodes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_remote_execution_runs_task_kind"), "remote_execution_runs", ["task_kind"], unique=False)
    op.create_index(op.f("ix_remote_execution_runs_assigned_node_id"), "remote_execution_runs", ["assigned_node_id"], unique=False)
    op.create_index(op.f("ix_remote_execution_runs_status"), "remote_execution_runs", ["status"], unique=False)

    op.create_table(
        "remote_execution_steps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("executor", sa.String(length=24), nullable=False, server_default="worker_core"),
        sa.Column("action_kind", sa.String(length=80), nullable=False),
        sa.Column("command", sa.Text(), nullable=True),
        sa.Column("browser_target", sa.String(length=40), nullable=True),
        sa.Column("sudo", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("stdout", sa.Text(), nullable=True),
        sa.Column("stderr", sa.Text(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["remote_execution_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_remote_execution_steps_run_id"), "remote_execution_steps", ["run_id"], unique=False)

    op.create_table(
        "remote_routing_policies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("task_kind", sa.String(length=120), nullable=False),
        sa.Column("preferred_target", sa.String(length=24), nullable=False, server_default="remote"),
        sa.Column("windows_only", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("remote_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("required_capabilities_json", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_kind"),
    )
    op.create_index(op.f("ix_remote_routing_policies_task_kind"), "remote_routing_policies", ["task_kind"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_remote_routing_policies_task_kind"), table_name="remote_routing_policies")
    op.drop_table("remote_routing_policies")
    op.drop_index(op.f("ix_remote_execution_steps_run_id"), table_name="remote_execution_steps")
    op.drop_table("remote_execution_steps")
    op.drop_index(op.f("ix_remote_execution_runs_status"), table_name="remote_execution_runs")
    op.drop_index(op.f("ix_remote_execution_runs_assigned_node_id"), table_name="remote_execution_runs")
    op.drop_index(op.f("ix_remote_execution_runs_task_kind"), table_name="remote_execution_runs")
    op.drop_table("remote_execution_runs")
    op.drop_index(op.f("ix_remote_node_capability_snapshots_captured_at"), table_name="remote_node_capability_snapshots")
    op.drop_index(op.f("ix_remote_node_capability_snapshots_node_id"), table_name="remote_node_capability_snapshots")
    op.drop_table("remote_node_capability_snapshots")
    op.drop_index(op.f("ix_remote_node_sessions_node_id"), table_name="remote_node_sessions")
    op.drop_table("remote_node_sessions")
    op.drop_index(op.f("ix_remote_node_credentials_node_id"), table_name="remote_node_credentials")
    op.drop_table("remote_node_credentials")
    op.drop_index(op.f("ix_remote_nodes_last_seen_at"), table_name="remote_nodes")
    op.drop_index(op.f("ix_remote_nodes_status"), table_name="remote_nodes")
    op.drop_table("remote_nodes")
