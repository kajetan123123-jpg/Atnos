"""Typy Project Content Brain — pamięć kanału, decyzje i statusy bez wymyślonych metryk."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


PROJECT_KEYS = frozenset({"wealth_blueprint", "children_stories", "horror_stories"})

HistoryDecisionKind = Literal["new", "similar_angle", "needs_rephrase", "already_used"]
TrendDecisionKind = Literal["use", "modify", "watch", "reject"]
MetricStatus = Literal["UNKNOWN", "observed", "verified"]
ClaimClass = Literal[
    "confirmed",
    "partial",
    "opinion",
    "estimate",
    "needs_source",
    "unverifiable",
    "risky_promise",
]
CampaignRole = Literal[
    "hook",
    "education",
    "problem",
    "solution",
    "example",
    "objection",
    "offer",
    "reminder",
]
ContentPlanStage = Literal[
    "idea",
    "planned",
    "in_production",
    "qa",
    "needs_review",
    "approved",
    "ready_to_publish",
    "published",
    "failed",
    "blocked",
]
PublicationStatus = Literal[
    "IDEA",
    "PLANNED",
    "IN_PRODUCTION",
    "QA",
    "NEEDS_REVIEW",
    "APPROVED",
    "READY_TO_PUBLISH",
    "PUBLISHED",
    "FAILED",
    "BLOCKED",
]
FeedbackScope = Literal["once", "film", "production", "series", "project", "global"]
FatigueVerdict = Literal["insufficient_data", "one_weak_result", "noise", "real_decline", "unknown"]


class DecisionMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=2000)
    confidence: float = Field(ge=0, le=1)
    source: str = Field(min_length=1, max_length=200)
    decided_at: str = Field(min_length=10, max_length=64)


class HistoryDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: HistoryDecisionKind
    reason: str = Field(min_length=3, max_length=2000)
    confidence: float = Field(ge=0, le=1)
    source: str = Field(default="project_content_brain.history", min_length=1, max_length=200)
    matched_job_ids: list[str] = Field(default_factory=list, max_length=20)
    matched_topics: list[str] = Field(default_factory=list, max_length=20)
    matched_hooks: list[str] = Field(default_factory=list, max_length=20)
    matched_ctas: list[str] = Field(default_factory=list, max_length=10)
    series_key: str | None = None
    episode_number: int | None = Field(default=None, ge=1, le=10_000)
    duplication_warning: bool = False
    decided_at: str = Field(min_length=10, max_length=64)


class TrendDecisionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signal_id: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=400)
    decision: TrendDecisionKind
    brand_fit: Literal["unknown", "poor", "partial", "good"] = "unknown"
    audience_fit: Literal["unknown", "poor", "partial", "good"] = "unknown"
    value: Literal["unknown", "low", "medium", "high"] = "unknown"
    saturation: Literal["unknown", "low", "medium", "high"] = "unknown"
    original_angle: str = Field(default="", max_length=1000)
    perishability: Literal["unknown", "evergreen", "days", "hours"] = "unknown"
    metrics_status: MetricStatus = "UNKNOWN"
    reason: str = Field(min_length=3, max_length=2000)
    confidence: float = Field(ge=0, le=1)
    source: str = Field(default="operator_or_rules", min_length=1, max_length=200)
    project: str
    decided_at: str


class CommentClusterRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cluster_id: str = Field(min_length=1, max_length=80)
    project: str
    theme: str = Field(min_length=1, max_length=400)
    kind: Literal["question", "problem", "objection", "request", "other"] = "other"
    comment_count: int = Field(default=0, ge=0)
    sample_refs: list[str] = Field(default_factory=list, max_length=20)
    proposed_format: Literal["film", "faq", "sequel", "none"] = "none"
    metrics_status: MetricStatus = "UNKNOWN"
    status: Literal["observe", "ready", "rejected"] = "observe"
    reason: str = Field(default="Brak zaimportowanych komentarzy — nie wymyślaj treści.", max_length=2000)
    confidence: float = Field(default=0.0, ge=0, le=1)
    source: str = Field(default="comment_import", max_length=200)
    created_at: str


class CompetitorKnowledgeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry_id: str = Field(min_length=1, max_length=80)
    project: str
    competitor_ref: str = Field(min_length=1, max_length=240)
    gaps: list[str] = Field(default_factory=list, max_length=40)
    saturated_topics: list[str] = Field(default_factory=list, max_length=40)
    weaknesses: list[str] = Field(default_factory=list, max_length=40)
    missing_formats: list[str] = Field(default_factory=list, max_length=40)
    metrics_status: MetricStatus = "UNKNOWN"
    reason: str = Field(min_length=3, max_length=2000)
    confidence: float = Field(ge=0, le=1)
    source: str = Field(default="competitor_store", max_length=200)
    observed_at: str


class CampaignPiece(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: CampaignRole
    topic: str = Field(min_length=3, max_length=300)
    notes: str = Field(default="", max_length=2000)
    content_plan_id: str | None = None


class CampaignRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    campaign_id: str = Field(min_length=1, max_length=80)
    project: str
    name: str = Field(min_length=2, max_length=200)
    premise: str = Field(default="", max_length=2000)
    pieces: list[CampaignPiece] = Field(default_factory=list, max_length=24)
    status: Literal["draft", "active", "completed", "archived"] = "draft"
    created_at: str
    updated_at: str

    @field_validator("pieces")
    @classmethod
    def distinct_roles_when_full(cls, value: list[CampaignPiece]) -> list[CampaignPiece]:
        roles = [p.role for p in value]
        if len(roles) != len(set(roles)) and len(roles) > 1:
            # Duplikaty ról są dozwolone tylko jako jawne kontynuacje — wymagają różnych tematów.
            topics = {(p.role, p.topic.casefold()) for p in value}
            if len(topics) != len(value):
                raise ValueError("Kampania nie może powtarzać tej samej roli z tym samym tematem")
        return value


class SeriesRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    series_key: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    project: str
    name: str = Field(min_length=2, max_length=200)
    premise: str = Field(default="", max_length=2000)
    format: str = Field(default="youtube_short", max_length=80)
    next_episode_number: int = Field(default=1, ge=1, le=10_000)
    prior_topics: list[str] = Field(default_factory=list, max_length=200)
    created_at: str
    updated_at: str


class ExperimentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experiment_id: str = Field(min_length=1, max_length=80)
    project: str
    hypothesis: str = Field(min_length=5, max_length=2000)
    variable: str = Field(min_length=2, max_length=200)
    variants: list[str] = Field(min_length=2, max_length=8)
    result: str = Field(default="", max_length=2000)
    conclusion: str = Field(default="", max_length=2000)
    winner: str | None = Field(default=None, max_length=200)
    repeated: bool | None = None
    metrics_status: MetricStatus = "UNKNOWN"
    status: Literal["planned", "running", "observe", "concluded"] = "planned"
    reason: str = Field(default="Brak wyników — nie ogłaszaj zwycięzcy.", max_length=2000)
    confidence: float = Field(default=0.0, ge=0, le=1)
    source: str = Field(default="experiment_engine", max_length=200)
    created_at: str
    updated_at: str


class PublishResultRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    project: str
    metrics_status: MetricStatus = "UNKNOWN"
    views: int | None = None
    ctr: float | None = None
    retention: float | None = None
    avg_view_duration: float | None = None
    reason: str = Field(default="Brak metryk publikacji — status UNKNOWN.", max_length=2000)
    confidence: float = Field(default=0.0, ge=0, le=1)
    source: str = Field(default="publish_results", max_length=200)
    observed_at: str


class FormatFatigueRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project: str
    format_key: str = Field(min_length=1, max_length=120)
    verdict: FatigueVerdict = "insufficient_data"
    sample_size: int = Field(default=0, ge=0)
    metrics_status: MetricStatus = "UNKNOWN"
    reason: str = Field(min_length=3, max_length=2000)
    confidence: float = Field(ge=0, le=1)
    source: str = Field(default="format_fatigue", max_length=200)
    observed_at: str


class ContentPlanRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: str = Field(min_length=1, max_length=80)
    idea: str = Field(min_length=3, max_length=400)
    priority: int = Field(default=50, ge=0, le=100)
    project: str
    series_key: str | None = None
    stage: ContentPlanStage = "idea"
    planned_date: str | None = Field(default=None, max_length=32)
    channel: str = Field(default="", max_length=200)
    campaign_id: str | None = None
    campaign_role: CampaignRole | None = None
    job_id: str | None = None
    duplication_warning: bool = False
    duplication_reason: str = Field(default="", max_length=2000)
    picker_reason: str = Field(default="", max_length=2000)
    publication_status: PublicationStatus = "IDEA"
    created_at: str
    updated_at: str


class HostCharacterProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    character_id: str = Field(min_length=1, max_length=80)
    project: str
    display_name: str = Field(min_length=1, max_length=120)
    voice_notes: str = Field(default="", max_length=2000)
    visual_notes: str = Field(default="", max_length=2000)
    is_real_person: bool = False
    likeness_consent: bool | None = None
    voice_consent: bool | None = None
    reference_consent: bool | None = None
    consent_source: str = Field(default="", max_length=400)
    updated_at: str


class ProjectBrainSnapshot(BaseModel):
    """Jedno źródło wiedzy produkcyjnej dla projektu/kanału (bez wymyślonych wyników)."""

    model_config = ConfigDict(extra="forbid")
    project: str
    brand: dict = Field(default_factory=dict)
    audience: dict = Field(default_factory=dict)
    voice: dict = Field(default_factory=dict)
    visual_rules: list[str] = Field(default_factory=list)
    characters: list[HostCharacterProfile] = Field(default_factory=list)
    production_rules: list[str] = Field(default_factory=list)
    publication_history: list[dict] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list)
    feedback_scopes_supported: list[FeedbackScope] = Field(
        default_factory=lambda: ["once", "film", "production", "series", "project", "global"]
    )
    capabilities: dict = Field(default_factory=dict)
    updated_at: str


CAPABILITY_FLAGS = {
    "record_me_mode": {
        "status": "not_built",
        "note_pl": "Tryb nagrywania własnego wizerunku (teleprompter, take'i, montaż) nie jest zbudowany — tylko flaga.",
    },
    "multilingual_local_versions": {
        "status": "not_built",
        "note_pl": "Lokalne wersje wielojęzyczne (waluty, jednostki, dopasowanie kulturowe) nie są zbudowane; produkcja Wealth Blueprint jest en.",
    },
    "auto_edit_user_footage": {
        "status": "not_built",
        "note_pl": "Automatyczny montaż materiału użytkownika nie jest zbudowany.",
    },
    "live_youtube_comment_mining": {
        "status": "schema_only",
        "note_pl": "Schemat klastrów komentarzy istnieje; bez importu prawdziwych komentarzy status=observe.",
    },
    "live_competitor_crawl": {
        "status": "partial_external",
        "note_pl": "Istnieje zbieranie konkurencji w intelligence; wiedza projektowa wymaga jawnego zapisu luk.",
    },
    "live_trend_metrics": {
        "status": "schema_only",
        "note_pl": "Decyzje trendów wymagają reason/confidence; bez metryk metrics_status=UNKNOWN.",
    },
}
