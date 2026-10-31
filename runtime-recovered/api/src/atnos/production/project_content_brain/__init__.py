"""Project Content Brain — wspólna pamięć kanału i decyzje produkcyjne."""
from __future__ import annotations

from atnos.production.project_content_brain.fact_classification import (
    attach_classifications_to_qa,
    classify_script_claims,
)
from atnos.production.project_content_brain.history import (
    advance_series_topic,
    ensure_series_for_order,
    evaluate_history,
    plan_from_order,
    pick_next_plan,
    warn_near_duplicate_plans,
)
from atnos.production.project_content_brain.publication_status import resolve_publication_status
from atnos.production.project_content_brain.store import ProjectContentBrainStore, ensure_schema
from atnos.production.project_content_brain.types import (
    CAPABILITY_FLAGS,
    CampaignPiece,
    CampaignRecord,
    CommentClusterRecord,
    CompetitorKnowledgeRecord,
    ContentPlanRecord,
    ExperimentRecord,
    FormatFatigueRecord,
    HistoryDecision,
    HostCharacterProfile,
    ProjectBrainSnapshot,
    PublishResultRecord,
    SeriesRecord,
    TrendDecisionRecord,
)

__all__ = [
    "CAPABILITY_FLAGS",
    "CampaignPiece",
    "CampaignRecord",
    "CommentClusterRecord",
    "CompetitorKnowledgeRecord",
    "ContentPlanRecord",
    "ExperimentRecord",
    "FormatFatigueRecord",
    "HistoryDecision",
    "HostCharacterProfile",
    "ProjectBrainSnapshot",
    "ProjectContentBrainStore",
    "PublishResultRecord",
    "SeriesRecord",
    "TrendDecisionRecord",
    "advance_series_topic",
    "attach_classifications_to_qa",
    "classify_script_claims",
    "ensure_schema",
    "ensure_series_for_order",
    "evaluate_history",
    "pick_next_plan",
    "plan_from_order",
    "resolve_publication_status",
    "warn_near_duplicate_plans",
]
