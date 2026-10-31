"""jarvis ambient foundation

Revision ID: 20260510_0028
Revises: 20260510_0027
Create Date: 2026-05-10 13:10:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260510_0028"
down_revision = "20260510_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("execution_policies", recreate="auto") as batch_op:
        batch_op.add_column(
            sa.Column(
                "jarvis_address_as",
                sa.String(length=24),
                nullable=False,
                server_default="none",
            )
        )

    with op.batch_alter_table("runtime_control_settings", recreate="auto") as batch_op:
        batch_op.add_column(
            sa.Column(
                "ambient_listening_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "ambient_wake_word",
                sa.String(length=120),
                nullable=False,
                server_default="Atnos",
            )
        )
        batch_op.add_column(sa.Column("ambient_mic_device", sa.String(length=255), nullable=True))
        batch_op.add_column(
            sa.Column(
                "ambient_muted",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "ambient_context_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "voice_replies_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "voice_reply_volume",
                sa.Float(),
                nullable=False,
                server_default="0.8",
            )
        )
        batch_op.add_column(sa.Column("voice_reply_voice_id", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("last_user_interaction_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("last_briefing_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f("fk_runtime_control_settings_voice_reply_voice_id_voice_profiles"),
            "voice_profiles",
            ["voice_reply_voice_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_table(
        "decision_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("made_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_summary", sa.Text(), nullable=True),
        sa.Column("evidence_message_ids", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_decision_log_conversation_id"), "decision_log", ["conversation_id"], unique=False)
    op.create_index(op.f("ix_decision_log_project_id"), "decision_log", ["project_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_decision_log_project_id"), table_name="decision_log")
    op.drop_index(op.f("ix_decision_log_conversation_id"), table_name="decision_log")
    op.drop_table("decision_log")

    with op.batch_alter_table("runtime_control_settings", recreate="auto") as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_runtime_control_settings_voice_reply_voice_id_voice_profiles"),
            type_="foreignkey",
        )
        batch_op.drop_column("last_briefing_at")
        batch_op.drop_column("last_user_interaction_at")
        batch_op.drop_column("voice_reply_voice_id")
        batch_op.drop_column("voice_reply_volume")
        batch_op.drop_column("voice_replies_enabled")
        batch_op.drop_column("ambient_context_enabled")
        batch_op.drop_column("ambient_muted")
        batch_op.drop_column("ambient_mic_device")
        batch_op.drop_column("ambient_wake_word")
        batch_op.drop_column("ambient_listening_enabled")

    with op.batch_alter_table("execution_policies", recreate="auto") as batch_op:
        batch_op.drop_column("jarvis_address_as")
