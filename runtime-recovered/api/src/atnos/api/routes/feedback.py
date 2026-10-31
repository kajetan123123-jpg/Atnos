from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(tags=["feedback"])

FeedbackRatingValue = Literal["positive", "negative", "neutral"]
FeedbackReasonCodeValue = Literal[
    "wrong_tone",
    "too_short",
    "too_long",
    "missed_context",
    "bad_execution_path",
    "incorrect_answer",
    "other",
]
ExecutionPathFeedbackValue = Literal["good", "bad", "unsure"]


def _load_feedback_enum_dependencies():
    from atnos.db.models.enums import ExecutionPathFeedback, FeedbackRating, FeedbackReasonCode

    return ExecutionPathFeedback, FeedbackRating, FeedbackReasonCode


def _load_turn_feedback_repository():
    from atnos.repositories.turn_feedback_repository import TurnFeedbackRepository

    return TurnFeedbackRepository


def _load_feedback_insights_dependencies():
    from atnos.services.feedback_insights_service import (
        FeedbackInsightsNotFoundError,
        FeedbackInsightsService,
    )

    return FeedbackInsightsNotFoundError, FeedbackInsightsService


def _load_feedback_suggestions_service():
    from atnos.services.feedback_improvement_suggestions_service import (
        FeedbackImprovementSuggestionsService,
    )

    return FeedbackImprovementSuggestionsService


class FeedbackBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FeedbackRequest(FeedbackBaseModel):
    conversation_id: int = Field(gt=0)
    assistant_message_id: int = Field(gt=0)
    user_message_id: int | None = Field(default=None, gt=0)
    execution_trace_id: int | None = Field(default=None, gt=0)
    rating: FeedbackRatingValue
    reason_code: FeedbackReasonCodeValue | None = None
    execution_path_feedback: ExecutionPathFeedbackValue | None = None
    comment: str | None = Field(default=None, max_length=1000)


class FeedbackResponse(BaseModel):
    feedback_id: int
    conversation_id: int
    assistant_message_id: int
    rating: str
    saved: bool


class FeedbackItem(BaseModel):
    message_id: int
    value: str
    category: str | None = None
    reason: str | None = None
    created_at: str


class ConversationFeedbackResponse(BaseModel):
    conversation_id: int
    items: list[FeedbackItem]


class ConversationFeedbackInsightsResponse(BaseModel):
    conversation_id: int
    total_feedback_count: int
    positive_count: int
    negative_count: int
    top_negative_reason: str | None = None
    execution_path_negative_count: int


class ProjectFeedbackInsightsResponse(BaseModel):
    project_id: int
    total_feedback_count: int
    positive_count: int
    negative_count: int
    top_negative_reason: str | None = None
    execution_path_negative_count: int
    feedback_conversation_count: int


class FeedbackSuggestionItem(BaseModel):
    suggestion_id: str
    severity: str
    title: str
    description: str
    source: str
    suggested_action_type: str
    target_setting: str | None = None


class ProjectFeedbackSuggestionsResponse(BaseModel):
    project_id: int
    items: list[FeedbackSuggestionItem]


@router.post("/feedback", response_model=FeedbackResponse, status_code=status.HTTP_201_CREATED)
def create_feedback(request: Request, payload: FeedbackRequest) -> FeedbackResponse:
    TurnFeedbackRepository = _load_turn_feedback_repository()
    ExecutionPathFeedback, FeedbackRating, FeedbackReasonCode = _load_feedback_enum_dependencies()
    database = request.app.state.database

    with database.session_scope() as session:
        repository = TurnFeedbackRepository(session)
        try:
            feedback = repository.create_or_update(
                conversation_id=payload.conversation_id,
                assistant_message_id=payload.assistant_message_id,
                user_message_id=payload.user_message_id,
                execution_trace_id=payload.execution_trace_id,
                rating=FeedbackRating(payload.rating),
                reason_code=FeedbackReasonCode(payload.reason_code) if payload.reason_code is not None else None,
                execution_path_feedback=ExecutionPathFeedback(payload.execution_path_feedback)
                if payload.execution_path_feedback is not None
                else None,
                comment=payload.comment.strip() if payload.comment else None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        return FeedbackResponse(
            feedback_id=feedback.id,
            conversation_id=feedback.conversation_id,
            assistant_message_id=feedback.assistant_message_id,
            rating=_enum_value(feedback.rating),
            saved=True,
        )


@router.get("/feedback/conversations/{conversation_id}", response_model=ConversationFeedbackResponse)
def list_conversation_feedback(request: Request, conversation_id: int) -> ConversationFeedbackResponse:
    TurnFeedbackRepository = _load_turn_feedback_repository()
    database = request.app.state.database

    with database.session_scope() as session:
        repository = TurnFeedbackRepository(session)
        try:
            feedback_items = repository.list_for_conversation(conversation_id)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

        return ConversationFeedbackResponse(
            conversation_id=conversation_id,
            items=[
                FeedbackItem(
                    message_id=feedback.assistant_message_id,
                    value=_enum_value(feedback.rating),
                    category=_enum_value(feedback.execution_path_feedback),
                    reason=_enum_value(feedback.reason_code),
                    created_at=feedback.created_at.isoformat(),
                )
                for feedback in feedback_items
            ],
        )


@router.get(
    "/feedback/conversations/{conversation_id}/insights",
    response_model=ConversationFeedbackInsightsResponse,
)
def get_conversation_feedback_insights(
    request: Request, conversation_id: int
) -> ConversationFeedbackInsightsResponse:
    FeedbackInsightsNotFoundError, FeedbackInsightsService = _load_feedback_insights_dependencies()
    database = request.app.state.database

    with database.session_scope() as session:
        service = FeedbackInsightsService(session)
        try:
            insights = service.get_conversation_insights(conversation_id)
        except FeedbackInsightsNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

        return ConversationFeedbackInsightsResponse(
            conversation_id=insights.conversation_id,
            total_feedback_count=insights.total_feedback_count,
            positive_count=insights.positive_count,
            negative_count=insights.negative_count,
            top_negative_reason=_enum_value(insights.top_negative_reason),
            execution_path_negative_count=insights.execution_path_negative_count,
        )


@router.get(
    "/feedback/projects/{project_id}/insights",
    response_model=ProjectFeedbackInsightsResponse,
)
def get_project_feedback_insights(
    request: Request, project_id: int
) -> ProjectFeedbackInsightsResponse:
    FeedbackInsightsNotFoundError, FeedbackInsightsService = _load_feedback_insights_dependencies()
    database = request.app.state.database

    with database.session_scope() as session:
        service = FeedbackInsightsService(session)
        try:
            insights = service.get_project_insights(project_id)
        except FeedbackInsightsNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

        return ProjectFeedbackInsightsResponse(
            project_id=insights.project_id,
            total_feedback_count=insights.total_feedback_count,
            positive_count=insights.positive_count,
            negative_count=insights.negative_count,
            top_negative_reason=_enum_value(insights.top_negative_reason),
            execution_path_negative_count=insights.execution_path_negative_count,
            feedback_conversation_count=insights.feedback_conversation_count,
        )


def _enum_value(value) -> str | None:
    if value is None:
        return None
    enum_value = getattr(value, "value", None)
    return enum_value if enum_value is not None else str(value)


@router.get(
    "/feedback/projects/{project_id}/suggestions",
    response_model=ProjectFeedbackSuggestionsResponse,
)
def get_project_feedback_suggestions(
    request: Request, project_id: int
) -> ProjectFeedbackSuggestionsResponse:
    FeedbackInsightsNotFoundError, _ = _load_feedback_insights_dependencies()
    FeedbackImprovementSuggestionsService = _load_feedback_suggestions_service()
    database = request.app.state.database

    with database.session_scope() as session:
        service = FeedbackImprovementSuggestionsService(session)
        try:
            suggestions = service.get_project_suggestions(project_id)
        except FeedbackInsightsNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

        return ProjectFeedbackSuggestionsResponse(
            project_id=suggestions.project_id,
            items=[
                FeedbackSuggestionItem(
                    suggestion_id=item.suggestion_id,
                    severity=item.severity,
                    title=item.title,
                    description=item.description,
                    source=item.source,
                    suggested_action_type=item.suggested_action_type,
                    target_setting=item.target_setting,
                )
                for item in suggestions.items
            ],
        )
