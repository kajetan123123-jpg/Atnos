from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class YouTubeUploadMetadata:
    title: str
    description: str
    tags: tuple[str, ...] = ()
    privacy: str = "private"
    category_id: str = "28"
    default_language: str = "en"
    default_audio_language: str = "en"

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "description": self.description,
            "tags": list(self.tags),
            "privacy": self.privacy,
            "category_id": self.category_id,
            "default_language": self.default_language,
            "default_audio_language": self.default_audio_language,
        }


@dataclass(frozen=True, slots=True)
class YouTubeUploadPreview:
    local_file: Path
    metadata: YouTubeUploadMetadata
    thumbnail_path: Path | None = None
    captions_path: Path | None = None
    requires_approval: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "local_file": str(self.local_file),
            "thumbnail_path": str(self.thumbnail_path) if self.thumbnail_path else None,
            "captions_path": str(self.captions_path) if self.captions_path else None,
            "requires_approval": self.requires_approval,
            "metadata": self.metadata.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class YouTubeUploadResult:
    video_id: str
    title: str
    description: str
    privacy: str
    local_file: Path
    youtube_url: str
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "description": self.description,
            "privacy": self.privacy,
            "local_file": str(self.local_file),
            "timestamp": self.timestamp,
            "youtube_url": self.youtube_url,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class YouTubeAnalyticsResult:
    video_id: str
    start_date: str
    end_date: str
    metrics: dict[str, int | float | None]
    status: str = "ready"

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_id": self.video_id,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "status": self.status,
            "metrics": dict(self.metrics),
        }
