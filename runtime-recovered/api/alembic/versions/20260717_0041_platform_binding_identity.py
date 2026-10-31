"""durable platform resource identity and verification

Revision ID: 20260717_0041
Revises: 20260716_0040
Create Date: 2026-07-17 05:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260717_0041"
down_revision = "20260716_0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "project_app_links",
        sa.Column("resource_type", sa.String(length=64), nullable=False, server_default="account"),
    )
    op.add_column(
        "project_app_links",
        sa.Column("resource_external_id", sa.String(length=320), nullable=True),
    )
    op.add_column(
        "project_app_links",
        sa.Column("resource_label", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "project_app_links",
        sa.Column("binding_role", sa.String(length=64), nullable=False, server_default="primary"),
    )
    op.add_column(
        "project_app_links",
        sa.Column("environment", sa.String(length=24), nullable=False, server_default="production"),
    )
    op.add_column(
        "project_app_links",
        sa.Column("confirmation_policy", sa.String(length=32), nullable=False, server_default="supervised"),
    )
    op.add_column(
        "project_app_links",
        sa.Column("delegation_policy", sa.String(length=32), nullable=False, server_default="project_only"),
    )
    op.add_column(
        "project_app_links",
        sa.Column("allow_shared_account", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "project_app_links",
        sa.Column("permission_scopes_json", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "project_app_links",
        sa.Column("connection_status", sa.String(length=32), nullable=False, server_default="unverified"),
    )
    op.add_column("project_app_links", sa.Column("connection_detail", sa.Text(), nullable=True))
    op.add_column(
        "project_app_links",
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "project_app_links",
        sa.Column("verification_evidence_json", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_index(
        "ix_project_app_links_resource_external_id",
        "project_app_links",
        ["resource_external_id"],
        unique=False,
    )
    op.create_index(
        "ix_project_app_links_connection_status",
        "project_app_links",
        ["connection_status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_project_app_links_connection_status", table_name="project_app_links")
    op.drop_index("ix_project_app_links_resource_external_id", table_name="project_app_links")
    for column in (
        "verification_evidence_json",
        "last_verified_at",
        "connection_detail",
        "connection_status",
        "permission_scopes_json",
        "allow_shared_account",
        "delegation_policy",
        "confirmation_policy",
        "environment",
        "binding_role",
        "resource_label",
        "resource_external_id",
        "resource_type",
    ):
        op.drop_column("project_app_links", column)
