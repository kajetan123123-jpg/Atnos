from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PlanStatus = Literal["planned", "in_production", "review", "approved", "uploaded"]
Recommendation = Literal["approve", "fix", "reject"]
ReviewStatus = Literal["awaiting_approval", "approved_for_upload", "rejected", "uploaded"]


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


class ShortProductionBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApprovedHook(ShortProductionBaseModel):
    variant: str
    text: str
    opening_line: str

    @field_validator("variant", "text", "opening_line")
    @classmethod
    def non_empty_text(cls, value: str) -> str:
        normalized = " ".join(value.split()).strip()
        if not normalized:
            raise ValueError("Field cannot be empty.")
        return normalized


class PlanRequest(ShortProductionBaseModel):
    topic_id: str
    channel_id: str
    approved_hook: ApprovedHook


class ScriptPlan(ShortProductionBaseModel):
    hook: str
    opening_line: str
    body: list[str] = Field(min_length=4)
    cta: str
    total_words: int = Field(ge=1)
    estimated_duration_s: int = Field(ge=1)

    @field_validator("body")
    @classmethod
    def body_line_word_limit(cls, value: list[str]) -> list[str]:
        for line in value:
            if len(line.split()) > 20:
                raise ValueError("Each body line must be max 20 words.")
        return value


class NarrationPlan(ShortProductionBaseModel):
    tool: Literal["elevenlabs"] = "elevenlabs"
    voice_id: str
    speed: float = Field(default=1.0, gt=0.0, le=2.0)
    output_file: str


class VisualSegment(ShortProductionBaseModel):
    segment_id: int
    script_lines: list[int] = Field(min_length=1)
    tool: Literal["runway", "facelessshorts", "stock"]
    prompt: str
    duration_s: int = Field(ge=1)
    style_notes: str


class ThumbnailPlan(ShortProductionBaseModel):
    headline: str
    visual_concept: str
    color_palette: list[str] = Field(min_length=2)

    @field_validator("headline")
    @classmethod
    def headline_max_three_words(cls, value: str) -> str:
        normalized = " ".join(value.upper().split()).strip()
        if len(normalized.split()) > 3:
            raise ValueError("Thumbnail headline must be max 3 words.")
        return normalized


class SubtitlesPlan(ShortProductionBaseModel):
    style: str
    auto_generate: bool = True


class ShortAssetPlan(ShortProductionBaseModel):
    plan_id: str = Field(default_factory=lambda: new_id("plan"))
    channel_id: str
    topic_id: str
    created_at: datetime = Field(default_factory=utc_now)
    script: ScriptPlan
    narration: NarrationPlan
    visuals: list[VisualSegment] = Field(min_length=1)
    thumbnail: ThumbnailPlan
    subtitles: SubtitlesPlan
    tool_sequence: list[str] = Field(min_length=1)
    estimated_production_time_min: int = Field(ge=1)
    status: PlanStatus = "planned"

    @model_validator(mode="after")
    def visibility_safety(self) -> "ShortAssetPlan":
        forbidden = {"youtube_public", "public_upload", "auto_public"}
        if any(str(item).casefold() in forbidden for item in self.tool_sequence):
            raise ValueError("ShortAssetPlan cannot include public upload steps.")
        return self


class ProductionFiles(ShortProductionBaseModel):
    short_file_path: str
    narration_file_path: str
    thumbnail_file_path: str


class QualityChecks(ShortProductionBaseModel):
    hook_present: bool
    hook_on_screen: bool
    duration_ok: bool
    audio_clear: bool
    subtitles_synced: bool
    no_black_frames: bool
    thumbnail_readable: bool
    cta_present: bool
    brand_consistent: bool
    no_copyrighted_audio: bool

    @property
    def failed_count(self) -> int:
        return sum(1 for value in self.model_dump().values() if value is False)

    @property
    def all_pass(self) -> bool:
        return self.failed_count == 0


class QualityScores(ShortProductionBaseModel):
    hook_strength: float = Field(ge=0.0, le=1.0)
    pacing: float = Field(ge=0.0, le=1.0)
    visual_quality: float = Field(ge=0.0, le=1.0)
    overall: float = Field(ge=0.0, le=1.0)


class QualityReview(ShortProductionBaseModel):
    qa_id: str = Field(default_factory=lambda: new_id("qa"))
    plan_id: str
    reviewed_at: datetime = Field(default_factory=utc_now)
    checks: QualityChecks
    scores: QualityScores
    issues: list[str] = Field(default_factory=list)
    recommendation: Recommendation
    fix_instructions: list[str] = Field(default_factory=list)


class UploadMetadata(ShortProductionBaseModel):
    title: str
    description: str
    tags: list[str] = Field(default_factory=list)
    scheduled_time: datetime | None = None
    visibility: Literal["private"] = "private"


class ReviewPackage(ShortProductionBaseModel):
    review_id: str = Field(default_factory=lambda: new_id("review"))
    channel_id: str
    plan_id: str
    short_file_path: str
    thumbnail_file_path: str
    qa: QualityReview
    upload_metadata: UploadMetadata
    status: ReviewStatus = "awaiting_approval"
