"""account credentials metadata foundation

Revision ID: 20260429_0016
Revises: 20260428_0015
Create Date: 2026-04-29 00:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260429_0016"
down_revision = "20260428_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_credentials",
        sa.Column("label", sa.String(length=160), nullable=False),
        sa.Column("provider_key", sa.String(length=120), nullable=False),
        sa.Column("account_identifier", sa.String(length=240), nullable=False),
        sa.Column("secret_type", sa.String(length=40), nullable=False),
        sa.Column("storage_backend", sa.String(length=40), nullable=False),
        sa.Column("secret_ref", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("allowed_capabilities_json", sa.JSON(), nullable=False),
        sa.Column("linked_integration_key", sa.String(length=120), nullable=True),
        sa.Column("linked_project_id", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("last_rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["linked_project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_account_credentials_provider_key"), "account_credentials", ["provider_key"], unique=False)
    op.create_index(
        op.f("ix_account_credentials_linked_integration_key"),
        "account_credentials",
        ["linked_integration_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_account_credentials_linked_project_id"),
        "account_credentials",
        ["linked_project_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_account_credentials_linked_project_id"), table_name="account_credentials")
    op.drop_index(op.f("ix_account_credentials_linked_integration_key"), table_name="account_credentials")
    op.drop_index(op.f("ix_account_credentials_provider_key"), table_name="account_credentials")
    op.drop_table("account_credentials")
