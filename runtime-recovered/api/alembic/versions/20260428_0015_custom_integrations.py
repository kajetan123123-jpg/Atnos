"""custom integrations registry

Revision ID: 20260428_0015
Revises: 20260428_0014
Create Date: 2026-04-28 22:45:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260428_0015"
down_revision = "20260428_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_integrations",
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("capabilities_json", sa.JSON(), nullable=False),
        sa.Column("permission_level", sa.String(length=32), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("setup_mode", sa.String(length=40), nullable=False),
        sa.Column("setup_requirements_json", sa.JSON(), nullable=False),
        sa.Column("missing_requirements_json", sa.JSON(), nullable=False),
        sa.Column("next_action_label", sa.String(length=120), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("has_adapter", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_custom_integrations_key"), "custom_integrations", ["key"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_custom_integrations_key"), table_name="custom_integrations")
    op.drop_table("custom_integrations")
