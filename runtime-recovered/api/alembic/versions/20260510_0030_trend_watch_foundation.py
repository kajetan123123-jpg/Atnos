"""trend watch foundation

Revision ID: 20260510_0030
Revises: 20260510_0029
Create Date: 2026-05-10 21:25:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260510_0030"
down_revision = "20260510_0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runtime_control_settings",
        sa.Column("trend_watch_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "runtime_control_settings",
        sa.Column("trend_watch_query", sa.String(length=255), nullable=False, server_default="short video hooks"),
    )
    op.add_column(
        "runtime_control_settings",
        sa.Column("trend_watch_region", sa.String(length=16), nullable=False, server_default="PL"),
    )
    op.add_column(
        "runtime_control_settings",
        sa.Column("trend_watch_interval_minutes", sa.Integer(), nullable=False, server_default="180"),
    )
    op.add_column(
        "runtime_control_settings",
        sa.Column("trend_watch_last_run_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "trend_watch_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("topic_query", sa.String(length=255), nullable=False, server_default="short video hooks"),
        sa.Column("region_code", sa.String(length=16), nullable=False, server_default="PL"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="ready"),
        sa.Column("source_mix_json", sa.JSON(), nullable=False),
        sa.Column("top_signals_json", sa.JSON(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("evaluation_notes_json", sa.JSON(), nullable=False),
        sa.Column("note_path", sa.String(length=1024), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_trend_watch_snapshots_project_id"), "trend_watch_snapshots", ["project_id"], unique=False)
    op.create_index(op.f("ix_trend_watch_snapshots_collected_at"), "trend_watch_snapshots", ["collected_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_trend_watch_snapshots_collected_at"), table_name="trend_watch_snapshots")
    op.drop_index(op.f("ix_trend_watch_snapshots_project_id"), table_name="trend_watch_snapshots")
    op.drop_table("trend_watch_snapshots")
    op.drop_column("runtime_control_settings", "trend_watch_last_run_at")
    op.drop_column("runtime_control_settings", "trend_watch_interval_minutes")
    op.drop_column("runtime_control_settings", "trend_watch_region")
    op.drop_column("runtime_control_settings", "trend_watch_query")
    op.drop_column("runtime_control_settings", "trend_watch_enabled")
