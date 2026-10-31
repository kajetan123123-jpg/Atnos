from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


HookVariant = Literal["curiosity", "shock", "benefit", "question", "contrast"]
TopicStatus = Literal["pending", "approved", "rejected"]


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


class TopicHookBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TopicRequest(TopicHookBaseModel):
    channel_id: str
    niche: str
    language: str = "pl"
    target_count: int = Field(default=10, ge=1, le=50)
    style_guide: str | None = None

    @field_validator("channel_id")
    @classmethod
    def safe_channel_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-_")
        if not normalized or any(char not in allowed for char in normalized):
            raise ValueError("channel_id can only contain lowercase letters, numbers, '-' and '_'.")
        return normalized

    @field_validator("niche", "language")
    @classmethod
    def non_empty_text(cls, value: str) -> str:
        normalized = " ".join(value.split()).strip()
        if not normalized:
            raise ValueError("Field cannot be empty.")
        return normalized


class RawTopic(TopicHookBaseModel):
    title: str
    source: str = "local"
    evidence: list[str] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def non_empty_title(cls, value: str) -> str:
        normalized = " ".join(value.split()).strip()
        if not normalized:
            raise ValueError("Topic title cannot be empty.")
        return normalized


class TopicScore(TopicHookBaseModel):
    searchability: float = Field(ge=0.0, le=0.25)
    shareability: float = Field(ge=0.0, le=0.25)
    simplicity: float = Field(ge=0.0, le=0.25)
    competition_gap: float = Field(ge=0.0, le=0.25)

    @property
    def total(self) -> float:
        return round(
            self.searchability + self.shareability + self.simplicity + self.competition_gap,
            3,
        )


class ScoredTopic(RawTopic):
    scores: TopicScore
    viral_score: float = Field(ge=0.0, le=1.0)
    rationale: str

    @model_validator(mode="after")
    def align_viral_score(self) -> "ScoredTopic":
        self.viral_score = round(float(self.scores.total), 3)
        return self


class Hook(TopicHookBaseModel):
    variant: HookVariant
    text: str
    opening_line: str

    @field_validator("text")
    @classmethod
    def hook_word_limit(cls, value: str) -> str:
        normalized = " ".join(value.split()).strip()
        if not normalized:
            raise ValueError("Hook text cannot be empty.")
        if len(normalized.split()) > 12:
            raise ValueError("Hook text must be max 12 words.")
        return normalized

    @field_validator("opening_line")
    @classmethod
    def opening_line_word_limit(cls, value: str) -> str:
        normalized = " ".join(value.split()).strip()
        if not normalized:
            raise ValueError("Opening line cannot be empty.")
        if len(normalized.split()) > 20:
            raise ValueError("Opening line must be max 20 words.")
        return normalized


class Topic(TopicHookBaseModel):
    id: str = Field(default_factory=lambda: new_id("topic"))
    title: str
    viral_score: float = Field(ge=0.0, le=1.0)
    rationale: str
    hooks: list[Hook] = Field(min_length=3)
    status: TopicStatus = "pending"
    scores: TopicScore


class TopicPackage(TopicHookBaseModel):
    package_id: str = Field(default_factory=lambda: new_id("package"))
    channel_id: str
    generated_at: datetime = Field(default_factory=utc_now)
    topics: list[Topic] = Field(default_factory=list)

    @property
    def pending_topics(self) -> list[Topic]:
        return [topic for topic in self.topics if topic.status == "pending"]
