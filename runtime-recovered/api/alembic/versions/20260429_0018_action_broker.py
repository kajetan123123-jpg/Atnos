"""account action broker preview foundation

Revision ID: 20260429_0018
Revises: 20260429_0017
Create Date: 2026-04-29 02:05:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260429_0018"
down_revision = "20260429_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_action_drafts",
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("integration_key", sa.String(length=120), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("action_type", sa.String(length=40), nullable=False),
        sa.Column("action_title", sa.String(length=200), nullable=False),
        sa.Column("action_summary", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("requires_approval", sa.Boolean(), nullable=False),
        sa.Column("preview_payload_json", sa.JSON(), nullable=False),
        sa.Column("approval_notes", sa.Text(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("audit_log_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=40), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["account_credentials.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_account_action_drafts_account_id"),
        "account_action_drafts",
        ["account_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_account_action_drafts_integration_key"),
        "account_action_drafts",
        ["integration_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_account_action_drafts_project_id"),
        "account_action_drafts",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_account_action_drafts_action_type"),
        "account_action_drafts",
        ["action_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_account_action_drafts_action_type"), table_name="account_action_drafts")
    op.drop_index(op.f("ix_account_action_drafts_project_id"), table_name="account_action_drafts")
    op.drop_index(op.f("ix_account_action_drafts_integration_key"), table_name="account_action_drafts")
    op.drop_index(op.f("ix_account_action_drafts_account_id"), table_name="account_action_drafts")
    op.drop_table("account_action_drafts")
