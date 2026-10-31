from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.db.models.conversation import Conversation
from atnos.db.models.enums import (
    ExecutionPathFeedback,
    FeedbackRating,
    FeedbackReasonCode,
    MessageRole,
)
from atnos.db.models.execution_trace import ExecutionTrace
from atnos.db.models.message import Message
from atnos.db.models.turn_feedback import TurnFeedback


class TurnFeedbackRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_or_update(
        self,
        *,
        conversation_id: int,
        assistant_message_id: int,
        rating: FeedbackRating,
        user_message_id: int | None = None,
        execution_trace_id: int | None = None,
        reason_code: FeedbackReasonCode | None = None,
        execution_path_feedback: ExecutionPathFeedback | None = None,
        comment: str | None = None,
        source: str = "chat_ui",
    ) -> TurnFeedback:
        self._validate_target(
            conversation_id=conversation_id,
            assistant_message_id=assistant_message_id,
            user_message_id=user_message_id,
            execution_trace_id=execution_trace_id,
        )

        statement = select(TurnFeedback).where(
            TurnFeedback.assistant_message_id == assistant_message_id
        )
        feedback = self.session.scalar(statement)

        if feedback is None:
            feedback = TurnFeedback(
                conversation_id=conversation_id,
                assistant_message_id=assistant_message_id,
                user_message_id=user_message_id,
            )
            self.session.add(feedback)

        feedback.rating = rating
        feedback.reason_code = reason_code
        feedback.execution_path_feedback = execution_path_feedback
        feedback.execution_trace_id = execution_trace_id
        feedback.comment = comment
        feedback.source = source
        self.session.flush()
        return feedback

    def list_for_conversation(self, conversation_id: int) -> list[TurnFeedback]:
        if self.session.get(Conversation, conversation_id) is None:
            raise ValueError("conversation_id does not point to an existing conversation.")

        statement = (
            select(TurnFeedback)
            .where(TurnFeedback.conversation_id == conversation_id)
            .order_by(TurnFeedback.created_at.asc(), TurnFeedback.id.asc())
        )
        return list(self.session.scalars(statement))

    def _validate_target(
        self,
        *,
        conversation_id: int,
        assistant_message_id: int,
        user_message_id: int | None,
        execution_trace_id: int | None,
    ) -> None:
        assistant_message = self.session.get(Message, assistant_message_id)
        if assistant_message is None:
            raise ValueError("assistant_message_id does not point to an existing message.")
        if assistant_message.conversation_id != conversation_id:
            raise ValueError("assistant_message_id does not belong to conversation_id.")
        if assistant_message.role != MessageRole.ASSISTANT:
            raise ValueError("assistant_message_id must point to an assistant message.")

        if user_message_id is not None:
            user_message = self.session.get(Message, user_message_id)
            if user_message is None:
                raise ValueError("user_message_id does not point to an existing message.")
            if user_message.conversation_id != conversation_id:
                raise ValueError("user_message_id does not belong to conversation_id.")
            if user_message.role != MessageRole.USER:
                raise ValueError("user_message_id must point to a user message.")

        if execution_trace_id is not None:
            execution_trace = self.session.get(ExecutionTrace, execution_trace_id)
            if execution_trace is None:
                raise ValueError("execution_trace_id does not point to an existing trace.")
            if execution_trace.conversation_id != conversation_id:
                raise ValueError("execution_trace_id does not belong to conversation_id.")
