from __future__ import annotations

from sqlalchemy import Enum, ForeignKey, ForeignKeyConstraint, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from atnos.db.base import Base, IdentityMixin, TimestampMixin
from atnos.db.models.enums import ExecutionPathFeedback, FeedbackRating, FeedbackReasonCode


class TurnFeedback(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "turn_feedback"
    __table_args__ = (
        ForeignKeyConstraint(
            ["assistant_message_id", "conversation_id"],
            ["messages.id", "messages.conversation_id"],
            ondelete="CASCADE",
            name="fk_turn_feedback_assistant_message_conversation",
        ),
        ForeignKeyConstraint(
            ["user_message_id", "conversation_id"],
            ["messages.id", "messages.conversation_id"],
            ondelete="SET NULL",
            name="fk_turn_feedback_user_message_conversation",
        ),
        UniqueConstraint("assistant_message_id", name="uq_turn_feedback_assistant_message"),
    )

    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    user_message_id: Mapped[int | None] = mapped_column(index=True, nullable=True)
    assistant_message_id: Mapped[int] = mapped_column(index=True, nullable=False)
    execution_trace_id: Mapped[int | None] = mapped_column(
        ForeignKey("execution_traces.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    rating: Mapped[FeedbackRating] = mapped_column(
        Enum(FeedbackRating, native_enum=False, length=24),
        nullable=False,
    )
    reason_code: Mapped[FeedbackReasonCode | None] = mapped_column(
        Enum(FeedbackReasonCode, native_enum=False, length=40),
        nullable=True,
    )
    execution_path_feedback: Mapped[ExecutionPathFeedback | None] = mapped_column(
        Enum(ExecutionPathFeedback, native_enum=False, length=24),
        nullable=True,
    )
    comment: Mapped[str | None] = mapped_column(Text(), nullable=True)
    source: Mapped[str] = mapped_column(String(40), default="chat_ui", nullable=False)

    conversation: Mapped["Conversation"] = relationship()
    user_message: Mapped["Message | None"] = relationship(foreign_keys=[user_message_id])
    assistant_message: Mapped["Message"] = relationship(foreign_keys=[assistant_message_id])
    execution_trace: Mapped["ExecutionTrace | None"] = relationship()
