"""feedback layer

Revision ID: 20260423_0007
Revises: 20260423_0006
Create Date: 2026-04-24 20:25:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260423_0007"
down_revision = "20260423_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "turn_feedback",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("user_message_id", sa.Integer(), nullable=True),
        sa.Column("assistant_message_id", sa.Integer(), nullable=False),
        sa.Column("execution_trace_id", sa.Integer(), nullable=True),
        sa.Column("rating", sa.Enum("POSITIVE", "NEGATIVE", "NEUTRAL", name="feedbackrating", native_enum=False, length=24), nullable=False),
        sa.Column("reason_code", sa.Enum("WRONG_TONE", "TOO_SHORT", "TOO_LONG", "MISSED_CONTEXT", "BAD_EXECUTION_PATH", "INCORRECT_ANSWER", "OTHER", name="feedbackreasoncode", native_enum=False, length=40), nullable=True),
        sa.Column("execution_path_feedback", sa.Enum("GOOD", "BAD", "UNSURE", name="executionpathfeedback", native_enum=False, length=24), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=False, server_default="chat_ui"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["execution_trace_id"], ["execution_traces.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["assistant_message_id", "conversation_id"],
            ["messages.id", "messages.conversation_id"],
            ondelete="CASCADE",
            name="fk_turn_feedback_assistant_message_conversation",
        ),
        sa.ForeignKeyConstraint(
            ["user_message_id", "conversation_id"],
            ["messages.id", "messages.conversation_id"],
            ondelete="SET NULL",
            name="fk_turn_feedback_user_message_conversation",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("assistant_message_id", name="uq_turn_feedback_assistant_message"),
    )
    op.create_index(op.f("ix_turn_feedback_conversation_id"), "turn_feedback", ["conversation_id"], unique=False)
    op.create_index(op.f("ix_turn_feedback_user_message_id"), "turn_feedback", ["user_message_id"], unique=False)
    op.create_index(op.f("ix_turn_feedback_assistant_message_id"), "turn_feedback", ["assistant_message_id"], unique=False)
    op.create_index(op.f("ix_turn_feedback_execution_trace_id"), "turn_feedback", ["execution_trace_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_turn_feedback_execution_trace_id"), table_name="turn_feedback")
    op.drop_index(op.f("ix_turn_feedback_assistant_message_id"), table_name="turn_feedback")
    op.drop_index(op.f("ix_turn_feedback_user_message_id"), table_name="turn_feedback")
    op.drop_index(op.f("ix_turn_feedback_conversation_id"), table_name="turn_feedback")
    op.drop_table("turn_feedback")
