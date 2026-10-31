from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from atnos.db.models.enums import FeedbackReasonCode
from atnos.services.feedback_insights_service import (
    FeedbackInsightsNotFoundError,
    FeedbackInsightsService,
    ProjectFeedbackInsightsResult,
)


@dataclass(slots=True, frozen=True)
class FeedbackImprovementSuggestion:
    suggestion_id: str
    severity: str
    title: str
    description: str
    source: str
    suggested_action_type: str
    target_setting: str | None = None


@dataclass(slots=True, frozen=True)
class ProjectFeedbackImprovementSuggestionsResult:
    project_id: int
    items: list[FeedbackImprovementSuggestion]


class FeedbackImprovementSuggestionsService:
    def __init__(self, session: Session) -> None:
        self.insights = FeedbackInsightsService(session)

    def get_project_suggestions(
        self,
        project_id: int,
    ) -> ProjectFeedbackImprovementSuggestionsResult:
        insights = self.insights.get_project_insights(project_id)

        return ProjectFeedbackImprovementSuggestionsResult(
            project_id=project_id,
            items=self._build_project_suggestions(insights),
        )

    @staticmethod
    def _build_project_suggestions(
        insights: ProjectFeedbackInsightsResult,
    ) -> list[FeedbackImprovementSuggestion]:
        suggestions: list[FeedbackImprovementSuggestion] = []

        if insights.negative_count == 0:
            return suggestions

        top_reason = insights.top_negative_reason
        if top_reason == FeedbackReasonCode.TOO_LONG:
            suggestions.append(
                FeedbackImprovementSuggestion(
                    suggestion_id="project-feedback-too-long",
                    severity="medium",
                    title="Sprobuj bardziej zwięzlych odpowiedzi",
                    description=(
                        "W tym projekcie negatywny feedback najczesciej wskazuje zbyt dlugie "
                        "odpowiedzi. Warto ustawic bardziej zwiezly styl odpowiedzi."
                    ),
                    source="project_feedback",
                    suggested_action_type="review_response_style",
                    target_setting="response_style",
                )
            )
        elif top_reason == FeedbackReasonCode.MISSED_CONTEXT:
            suggestions.append(
                FeedbackImprovementSuggestion(
                    suggestion_id="project-feedback-missed-context",
                    severity="medium",
                    title="Wzmocnij uzycie kontekstu projektu",
                    description=(
                        "Ten projekt zbiera sygnaly, ze odpowiedzi pomijaja ustalenia i stan "
                        "prac. Warto przejrzec uzycie kontekstu projektu i memory review."
                    ),
                    source="project_feedback",
                    suggested_action_type="review_project_context",
                    target_setting="memory",
                )
            )
        elif top_reason == FeedbackReasonCode.WRONG_TONE:
            suggestions.append(
                FeedbackImprovementSuggestion(
                    suggestion_id="project-feedback-wrong-tone",
                    severity="low",
                    title="Dopasuj styl wspolpracy",
                    description=(
                        "W tym projekcie ton odpowiedzi bywa oceniany jako nietrafiony. "
                        "Warto dopracowac response style albo wybrac lepiej pasujaca persone."
                    ),
                    source="project_feedback",
                    suggested_action_type="review_style_or_persona",
                    target_setting="response_style",
                )
            )

        if insights.execution_path_negative_count > 0:
            suggestions.append(
                FeedbackImprovementSuggestion(
                    suggestion_id="project-feedback-execution-path",
                    severity="medium",
                    title="Przejrzyj execution posture",
                    description=(
                        "Czesc negatywnego feedbacku wskazuje nietrafiona sciezke wykonania. "
                        "Warto sprawdzic posture local/cloud oraz granice permissions."
                    ),
                    source="project_feedback",
                    suggested_action_type="review_execution_posture",
                    target_setting="providers",
                )
            )

        return suggestions


__all__ = [
    "FeedbackImprovementSuggestion",
    "FeedbackImprovementSuggestionsService",
    "FeedbackInsightsNotFoundError",
    "ProjectFeedbackImprovementSuggestionsResult",
]
