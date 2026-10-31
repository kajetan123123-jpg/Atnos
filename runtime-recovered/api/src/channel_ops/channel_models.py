from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class UploadPolicy(StrEnum):
    PRIVATE_ONLY = "private_only"
    APPROVAL_REQUIRED = "approval_required"
    AUTO_PRIVATE = "auto_private"
    AUTO_PUBLIC_AFTER_APPROVAL = "auto_public_after_approval"


class AutomationLevel(StrEnum):
    MANUAL = "manual"
    ASSISTED = "assisted"
    AUTO_PRIVATE = "auto_private"
    FULL_AUTO_LIMITED = "full_auto_limited"


class ContentStatus(StrEnum):
    IDEA = "idea"
    PLANNED = "planned"
    SCRIPTED = "scripted"
    RENDERING = "rendering"
    RENDERED = "rendered"
    QA_PASSED = "qa_passed"
    QA_FAILED = "qa_failed"
    APPROVAL_PENDING = "approval_pending"
    MANUAL_UPLOAD_READY = "manual_upload_ready"
    PRIVATE_UPLOADED = "private_uploaded"
    PUBLIC_READY = "public_ready"
    PUBLISHED = "published"
    BLOCKED = "blocked"
    FAILED = "failed"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class ChannelOpsBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class ChannelProfile(ChannelOpsBaseModel):
    channel_id: str
    display_name: str
    niche: str
    language: str
    audience: str
    style_guide_path: str | None = None
    upload_policy: UploadPolicy = UploadPolicy.APPROVAL_REQUIRED
    automation_level: AutomationLevel = AutomationLevel.ASSISTED
    daily_target_count: int = Field(default=1, ge=0)
    enabled: bool = True
    youtube_oauth_token_path: str | None = None
    brand_assets_dir: str | None = None
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)

    @field_validator("channel_id", "display_name", "niche", "language", "audience")
    @classmethod
    def non_empty_text(cls, value: str) -> str:
        normalized = " ".join(value.split()).strip()
        if not normalized:
            raise ValueError("Field cannot be empty.")
        return normalized

    @field_validator("channel_id")
    @classmethod
    def safe_channel_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-_")
        if any(char not in allowed for char in normalized):
            raise ValueError("channel_id can only contain lowercase letters, numbers, '-' and '_'.")
        return normalized


class ContentItem(ChannelOpsBaseModel):
    item_id: str = Field(default_factory=lambda: f"item-{uuid4().hex[:12]}")
    channel_id: str
    topic: str
    status: ContentStatus = ContentStatus.IDEA
    source: str | None = None
    source_ref: str | None = None
    brief_path: str | None = None
    output_dir: str | None = None
    final_mp4: str | None = None
    quality_score: float | None = None
    upload_approval_id: str | None = None
    youtube_video_id: str | None = None
    analytics_summary: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)

    @field_validator("channel_id", "topic")
    @classmethod
    def non_empty_text(cls, value: str) -> str:
        normalized = " ".join(value.split()).strip()
        if not normalized:
            raise ValueError("Field cannot be empty.")
        return normalized

    def with_update(self, **updates: Any) -> "ContentItem":
        data = self.model_dump()
        data.update(updates)
        data["updated_at"] = utc_now()
        return ContentItem(**data)


def path_to_str(path: Path | str | None) -> str | None:
    if path is None:
        return None
    return str(Path(path).resolve())
