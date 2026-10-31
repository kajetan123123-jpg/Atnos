"""browser runtime hardening foundations

Revision ID: 20260513_0032
Revises: 20260510_0031
Create Date: 2026-05-13 11:20:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260513_0032"
down_revision = "20260510_0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runtime_control_settings",
        sa.Column("stealth_headless_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "runtime_control_settings",
        sa.Column("browser_allow_any_url", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.create_table(
        "browser_domain_whitelist",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "domain", name="uq_browser_domain_whitelist_user_domain"),
    )
    op.create_index(op.f("ix_browser_domain_whitelist_user_id"), "browser_domain_whitelist", ["user_id"], unique=False)
    op.create_index(op.f("ix_browser_domain_whitelist_domain"), "browser_domain_whitelist", ["domain"], unique=False)

    op.create_table(
        "local_file_grants",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("grant_root", sa.String(length=1024), nullable=False),
        sa.Column("mode", sa.String(length=24), nullable=False, server_default="read_only"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
        sa.Column("max_file_size_mb", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_local_file_grants_user_id"), "local_file_grants", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_local_file_grants_user_id"), table_name="local_file_grants")
    op.drop_table("local_file_grants")
    op.drop_index(op.f("ix_browser_domain_whitelist_domain"), table_name="browser_domain_whitelist")
    op.drop_index(op.f("ix_browser_domain_whitelist_user_id"), table_name="browser_domain_whitelist")
    op.drop_table("browser_domain_whitelist")
    op.drop_column("runtime_control_settings", "browser_allow_any_url")
    op.drop_column("runtime_control_settings", "stealth_headless_enabled")
