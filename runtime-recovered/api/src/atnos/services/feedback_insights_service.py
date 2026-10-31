from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.db.models.conversation import Conversation
from atnos.db.models.enums import ExecutionPathFeedback, FeedbackReasonCode, FeedbackRating
from atnos.db.models.project import Project
from atnos.db.models.turn_feedback import TurnFeedback
from atnos.repositories.turn_feedback_repository import TurnFeedbackRepository


class FeedbackInsightsNotFoundError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class ConversationFeedbackInsightsResult:
    conversation_id: int
    total_feedback_count: int
    positive_count: int
    negative_count: int
    top_negative_reason: FeedbackReasonCode | None
    execution_path_negative_count: int


@dataclass(slots=True, frozen=True)
class ProjectFeedbackInsightsResult:
    project_id: int
    total_feedback_count: int
    positive_count: int
    negative_count: int
    top_negative_reason: FeedbackReasonCode | None
    execution_path_negative_count: int
    feedback_conversation_count: int


class FeedbackInsightsService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.feedback = TurnFeedbackRepository(session)

    def get_conversation_insights(
        self, conversation_id: int
    ) -> ConversationFeedbackInsightsResult:
        try:
            feedback_items = self.feedback.list_for_conversation(conversation_id)
        except ValueError as exc:
            raise FeedbackInsightsNotFoundError(str(exc)) from exc

        metrics = self._build_feedback_metrics(feedback_items)

        return ConversationFeedbackInsightsResult(
            conversation_id=conversation_id,
            total_feedback_count=metrics.total_feedback_count,
            positive_count=metrics.positive_count,
            negative_count=metrics.negative_count,
            top_negative_reason=metrics.top_negative_reason,
            execution_path_negative_count=metrics.execution_path_negative_count,
        )

    def get_project_insights(self, project_id: int) -> ProjectFeedbackInsightsResult:
        if self.session.get(Project, project_id) is None:
            raise FeedbackInsightsNotFoundError("project_id does not point to an existing project.")

        statement = (
            select(TurnFeedback)
            .join(Conversation, Conversation.id == TurnFeedback.conversation_id)
            .where(Conversation.project_id == project_id)
            .order_by(TurnFeedback.created_at.asc(), TurnFeedback.id.asc())
        )
        feedback_items = list(self.session.scalars(statement))
        metrics = self._build_feedback_metrics(feedback_items)
        feedback_conversation_count = len({item.conversation_id for item in feedback_items})

        return ProjectFeedbackInsightsResult(
            project_id=project_id,
            total_feedback_count=metrics.total_feedback_count,
            positive_count=metrics.positive_count,
            negative_count=metrics.negative_count,
            top_negative_reason=metrics.top_negative_reason,
            execution_path_negative_count=metrics.execution_path_negative_count,
            feedback_conversation_count=feedback_conversation_count,
        )

    @staticmethod
    def _resolve_unique_top_reason(
        reason_counts: dict[FeedbackReasonCode, int],
    ) -> FeedbackReasonCode | None:
        if not reason_counts:
            return None

        sorted_reasons = sorted(
            reason_counts.items(),
            key=lambda item: item[1],
            reverse=True,
        )
        top_reason, top_count = sorted_reasons[0]
        if len(sorted_reasons) > 1 and sorted_reasons[1][1] == top_count:
            return None
        return top_reason

    @classmethod
    def _build_feedback_metrics(
        cls,
        feedback_items: list[TurnFeedback],
    ) -> "_FeedbackMetrics":
        total_feedback_count = len(feedback_items)
        positive_count = sum(
            1 for item in feedback_items if item.rating == FeedbackRating.POSITIVE
        )
        negative_items = [
            item for item in feedback_items if item.rating == FeedbackRating.NEGATIVE
        ]
        negative_count = len(negative_items)
        execution_path_negative_count = sum(
            1
            for item in negative_items
            if item.execution_path_feedback == ExecutionPathFeedback.BAD
        )

        reason_counts: dict[FeedbackReasonCode, int] = {}
        for item in negative_items:
            reason = item.reason_code
            if reason is None or reason in {
                FeedbackReasonCode.OTHER,
                FeedbackReasonCode.INCORRECT_ANSWER,
            }:
                continue
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

        return _FeedbackMetrics(
            total_feedback_count=total_feedback_count,
            positive_count=positive_count,
            negative_count=negative_count,
            top_negative_reason=cls._resolve_unique_top_reason(reason_counts),
            execution_path_negative_count=execution_path_negative_count,
        )


@dataclass(slots=True, frozen=True)
class _FeedbackMetrics:
    total_feedback_count: int
    positive_count: int
    negative_count: int
    top_negative_reason: FeedbackReasonCode | None
    execution_path_negative_count: int
