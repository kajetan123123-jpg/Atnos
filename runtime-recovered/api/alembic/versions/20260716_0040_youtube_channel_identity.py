"""youtube channel identity and publishing policy

Revision ID: 20260716_0040
Revises: 20260716_0039
Create Date: 2026-07-16 22:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260716_0040"
down_revision = "20260716_0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "youtube_channel_identities",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("account_binding_key", sa.String(length=320), nullable=False),
        sa.Column("youtube_channel_id", sa.String(length=128), nullable=True),
        sa.Column("channel_handle", sa.String(length=160), nullable=True),
        sa.Column("channel_url", sa.String(length=1024), nullable=True),
        sa.Column("display_name", sa.String(length=240), nullable=True),
        sa.Column("owner_label", sa.String(length=240), nullable=True),
        sa.Column("language_code", sa.String(length=24), nullable=True),
        sa.Column("target_market", sa.String(length=80), nullable=True),
        sa.Column("niche", sa.Text(), nullable=True),
        sa.Column("oauth_connection_id", sa.Integer(), sa.ForeignKey("oauth_connections.id", ondelete="SET NULL"), nullable=True),
        sa.Column("identity_status", sa.String(length=40), nullable=False, server_default="insufficient_data"),
        sa.Column("owner_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("owner_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("manifest_status", sa.String(length=40), nullable=False, server_default="draft"),
        sa.Column("manifest_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publishing_policy_json", sa.JSON(), nullable=False),
        sa.Column("publishing_policy_status", sa.String(length=40), nullable=False, server_default="draft"),
        sa.Column("publishing_policy_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_evidence_json", sa.JSON(), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", name="uq_youtube_channel_identities_project_id"),
        sa.UniqueConstraint("account_binding_key", name="uq_youtube_channel_identities_account_binding_key"),
        sa.UniqueConstraint("youtube_channel_id", name="uq_youtube_channel_identities_youtube_channel_id"),
    )
    op.create_index(op.f("ix_youtube_channel_identities_project_id"), "youtube_channel_identities", ["project_id"], unique=False)
    op.create_index(op.f("ix_youtube_channel_identities_oauth_connection_id"), "youtube_channel_identities", ["oauth_connection_id"], unique=False)
    op.create_index(op.f("ix_youtube_channel_identities_identity_status"), "youtube_channel_identities", ["identity_status"], unique=False)
    op.create_index(op.f("ix_youtube_channel_identities_manifest_status"), "youtube_channel_identities", ["manifest_status"], unique=False)
    op.create_index(op.f("ix_youtube_channel_identities_publishing_policy_status"), "youtube_channel_identities", ["publishing_policy_status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_youtube_channel_identities_publishing_policy_status"), table_name="youtube_channel_identities")
    op.drop_index(op.f("ix_youtube_channel_identities_manifest_status"), table_name="youtube_channel_identities")
    op.drop_index(op.f("ix_youtube_channel_identities_identity_status"), table_name="youtube_channel_identities")
    op.drop_index(op.f("ix_youtube_channel_identities_oauth_connection_id"), table_name="youtube_channel_identities")
    op.drop_index(op.f("ix_youtube_channel_identities_project_id"), table_name="youtube_channel_identities")
    op.drop_table("youtube_channel_identities")
