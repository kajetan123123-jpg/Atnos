"""automation control plane core v1

Revision ID: 20260430_0022
Revises: 20260430_0021
Create Date: 2026-04-30 23:58:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260430_0022"
down_revision = "20260430_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "connected_account_controls",
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("integration_key", sa.String(length=120), nullable=False),
        sa.Column("connector_key", sa.String(length=120), nullable=True),
        sa.Column("connection_status", sa.String(length=32), nullable=False),
        sa.Column("permission_mode", sa.String(length=32), nullable=False),
        sa.Column("automation_level", sa.String(length=24), nullable=False),
        sa.Column("allowed_capabilities_json", sa.JSON(), nullable=False),
        sa.Column("automation_policy_json", sa.JSON(), nullable=False),
        sa.Column("risk_level", sa.String(length=24), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("warnings_json", sa.JSON(), nullable=False),
        sa.Column("audit_log_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["account_credentials.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_connected_account_controls_account_id"),
        "connected_account_controls",
        ["account_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_connected_account_controls_project_id"),
        "connected_account_controls",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_connected_account_controls_integration_key"),
        "connected_account_controls",
        ["integration_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_connected_account_controls_connector_key"),
        "connected_account_controls",
        ["connector_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_connected_account_controls_connector_key"), table_name="connected_account_controls")
    op.drop_index(op.f("ix_connected_account_controls_integration_key"), table_name="connected_account_controls")
    op.drop_index(op.f("ix_connected_account_controls_project_id"), table_name="connected_account_controls")
    op.drop_index(op.f("ix_connected_account_controls_account_id"), table_name="connected_account_controls")
    op.drop_table("connected_account_controls")
