"""oauth foundation v1

Revision ID: 20260501_0023
Revises: 20260430_0022
Create Date: 2026-05-01 12:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260501_0023"
down_revision = "20260430_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_connections",
        sa.Column("connector_key", sa.String(length=120), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("scopes_json", sa.JSON(), nullable=False),
        sa.Column("state_hash", sa.String(length=128), nullable=True),
        sa.Column("code_verifier_ref", sa.String(length=255), nullable=True),
        sa.Column("token_ref", sa.String(length=255), nullable=True),
        sa.Column("account_id", sa.Integer(), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("audit_log_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["account_credentials.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_oauth_connections_account_id"), "oauth_connections", ["account_id"], unique=False)
    op.create_index(op.f("ix_oauth_connections_connector_key"), "oauth_connections", ["connector_key"], unique=False)
    op.create_index(op.f("ix_oauth_connections_state_hash"), "oauth_connections", ["state_hash"], unique=False)
    op.create_index(op.f("ix_oauth_connections_status"), "oauth_connections", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_oauth_connections_status"), table_name="oauth_connections")
    op.drop_index(op.f("ix_oauth_connections_state_hash"), table_name="oauth_connections")
    op.drop_index(op.f("ix_oauth_connections_connector_key"), table_name="oauth_connections")
    op.drop_index(op.f("ix_oauth_connections_account_id"), table_name="oauth_connections")
    op.drop_table("oauth_connections")
