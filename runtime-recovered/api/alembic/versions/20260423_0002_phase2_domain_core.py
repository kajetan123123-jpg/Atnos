"""phase2 domain core

Revision ID: 20260423_0002
Revises: 20260423_0001
Create Date: 2026-04-23 09:05:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260423_0002"
down_revision = "20260423_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "role_profiles",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("system_prompt", sa.Text(), nullable=True),
        sa.Column("tone", sa.String(length=60), nullable=True),
        sa.Column("response_style", sa.String(length=60), nullable=True),
        sa.Column("criticality_level", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("empathy_level", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("firmness_level", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("scope", sa.String(length=32), nullable=False, server_default="global"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("criticality_level BETWEEN 1 AND 5", name="ck_role_profiles_criticality_range"),
        sa.CheckConstraint("empathy_level BETWEEN 1 AND 5", name="ck_role_profiles_empathy_range"),
        sa.CheckConstraint("firmness_level BETWEEN 1 AND 5", name="ck_role_profiles_firmness_range"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_role_profiles_slug"), "role_profiles", ["slug"], unique=True)
    op.create_index(
        "uq_role_profiles_default_true",
        "role_profiles",
        ["is_default"],
        unique=True,
        sqlite_where=sa.text("is_default = 1"),
    )

    op.create_table(
        "projects",
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_projects_slug"), "projects", ["slug"], unique=True)

    op.create_table(
        "conversations",
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("role_profile_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("active_work_mode", sa.String(length=40), nullable=False, server_default="auto"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["role_profile_id"], ["role_profiles.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "project_id", name="uq_conversations_id_project"),
    )
    op.create_index(op.f("ix_conversations_project_id"), "conversations", ["project_id"], unique=False)
    op.create_index(op.f("ix_conversations_role_profile_id"), "conversations", ["role_profile_id"], unique=False)

    op.create_table(
        "messages",
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("parent_message_id", sa.Integer(), nullable=True),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="created"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_format", sa.String(length=24), nullable=False, server_default="markdown"),
        sa.Column("prompt_improved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", "sequence_number", name="uq_messages_conversation_sequence"),
        sa.UniqueConstraint("id", "conversation_id", name="uq_messages_id_conversation"),
    )
    op.create_index(op.f("ix_messages_conversation_id"), "messages", ["conversation_id"], unique=False)

    op.create_table(
        "attachments",
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="upload"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="available"),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=120), nullable=True),
        sa.Column("file_extension", sa.String(length=24), nullable=True),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("checksum", sa.String(length=128), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["message_id", "conversation_id"],
            ["messages.id", "messages.conversation_id"],
            ondelete="CASCADE",
            name="fk_attachments_message_conversation",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "project_id IS NOT NULL OR conversation_id IS NOT NULL OR message_id IS NOT NULL",
            name="ck_attachments_has_owner",
        ),
        sa.CheckConstraint(
            "("
            "project_id IS NOT NULL AND conversation_id IS NULL AND message_id IS NULL"
            ") OR ("
            "project_id IS NULL AND conversation_id IS NOT NULL AND message_id IS NULL"
            ") OR ("
            "project_id IS NULL AND conversation_id IS NOT NULL AND message_id IS NOT NULL"
            ")",
            name="ck_attachments_owner_pattern",
        ),
    )
    op.create_index(op.f("ix_attachments_conversation_id"), "attachments", ["conversation_id"], unique=False)
    op.create_index(op.f("ix_attachments_message_id"), "attachments", ["message_id"], unique=False)
    op.create_index(op.f("ix_attachments_project_id"), "attachments", ["project_id"], unique=False)

    op.create_table(
        "execution_traces",
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("trace_kind", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("step_name", sa.String(length=120), nullable=False),
        sa.Column("provider_key", sa.String(length=120), nullable=True),
        sa.Column("plugin_key", sa.String(length=120), nullable=True),
        sa.Column("model_key", sa.String(length=120), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("input_summary", sa.Text(), nullable=True),
        sa.Column("output_summary", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["message_id", "conversation_id"],
            ["messages.id", "messages.conversation_id"],
            ondelete="CASCADE",
            name="fk_execution_traces_message_conversation",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "project_id IS NOT NULL OR conversation_id IS NOT NULL OR message_id IS NOT NULL",
            name="ck_execution_traces_has_owner",
        ),
        sa.CheckConstraint(
            "("
            "project_id IS NOT NULL AND conversation_id IS NULL AND message_id IS NULL"
            ") OR ("
            "project_id IS NULL AND conversation_id IS NOT NULL AND message_id IS NULL"
            ") OR ("
            "project_id IS NULL AND conversation_id IS NOT NULL AND message_id IS NOT NULL"
            ")",
            name="ck_execution_traces_owner_pattern",
        ),
    )
    op.create_index(op.f("ix_execution_traces_conversation_id"), "execution_traces", ["conversation_id"], unique=False)
    op.create_index(op.f("ix_execution_traces_message_id"), "execution_traces", ["message_id"], unique=False)
    op.create_index(op.f("ix_execution_traces_project_id"), "execution_traces", ["project_id"], unique=False)

    op.create_table(
        "project_memories",
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("source_conversation_id", sa.Integer(), nullable=True),
        sa.Column("source_message_id", sa.Integer(), nullable=True),
        sa.Column("memory_kind", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.String(length=32), nullable=False, server_default="medium"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["source_message_id", "source_conversation_id"],
            ["messages.id", "messages.conversation_id"],
            name="fk_project_memories_message_conversation",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "source_message_id IS NULL OR source_conversation_id IS NOT NULL",
            name="ck_project_memories_message_requires_conversation",
        ),
    )
    op.create_index(op.f("ix_project_memories_project_id"), "project_memories", ["project_id"], unique=False)

    op.drop_table("bootstrap_records")


def downgrade() -> None:
    op.create_table(
        "bootstrap_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_bootstrap_records_key"),
    )

    op.drop_index(op.f("ix_project_memories_project_id"), table_name="project_memories")
    op.drop_table("project_memories")

    op.drop_index(op.f("ix_execution_traces_project_id"), table_name="execution_traces")
    op.drop_index(op.f("ix_execution_traces_message_id"), table_name="execution_traces")
    op.drop_index(op.f("ix_execution_traces_conversation_id"), table_name="execution_traces")
    op.drop_table("execution_traces")

    op.drop_index(op.f("ix_attachments_project_id"), table_name="attachments")
    op.drop_index(op.f("ix_attachments_message_id"), table_name="attachments")
    op.drop_index(op.f("ix_attachments_conversation_id"), table_name="attachments")
    op.drop_table("attachments")

    op.drop_index(op.f("ix_messages_conversation_id"), table_name="messages")
    op.drop_table("messages")

    op.drop_index(op.f("ix_conversations_role_profile_id"), table_name="conversations")
    op.drop_index(op.f("ix_conversations_project_id"), table_name="conversations")
    op.drop_table("conversations")

    op.drop_index(op.f("ix_projects_slug"), table_name="projects")
    op.drop_table("projects")

    op.drop_index("uq_role_profiles_default_true", table_name="role_profiles")
    op.drop_index(op.f("ix_role_profiles_slug"), table_name="role_profiles")
    op.drop_table("role_profiles")
