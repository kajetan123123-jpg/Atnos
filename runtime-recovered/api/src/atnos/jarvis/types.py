from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import IntEnum, StrEnum
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class JarvisEvent(StrEnum):
    SCREEN_CONTEXT_UPDATED = "SCREEN_CONTEXT_UPDATED"
    VOICE_COMMAND = "VOICE_COMMAND"
    SPEAK_REQUESTED = "SPEAK_REQUESTED"
    RESOURCE_SNAPSHOT_UPDATED = "RESOURCE_SNAPSHOT_UPDATED"
    RESOURCES_LOW = "RESOURCES_LOW"
    FILE_DETECTED = "FILE_DETECTED"
    CLIPBOARD_CHANGED = "CLIPBOARD_CHANGED"
    GOAL_PLAN_UPDATED = "GOAL_PLAN_UPDATED"
    GOAL_SET_REQUESTED = "GOAL_SET_REQUESTED"
    TASK_ENQUEUED = "TASK_ENQUEUED"
    TASK_STARTED = "TASK_STARTED"
    TASK_PROGRESS = "TASK_PROGRESS"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"
    SESSION_RESTORED = "SESSION_RESTORED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    NEEDS_MANUAL_LOGIN = "NEEDS_MANUAL_LOGIN"
    MANUAL_LOGIN_STARTED = "MANUAL_LOGIN_STARTED"
    MANUAL_LOGIN_CONFIRMED = "MANUAL_LOGIN_CONFIRMED"
    MANUAL_LOGIN_SAVED = "MANUAL_LOGIN_SAVED"
    PAGE_ACQUIRED = "PAGE_ACQUIRED"
    PAGE_RELEASED = "PAGE_RELEASED"
    TREND_DETECTED = "TREND_DETECTED"


class WorkerPriority(IntEnum):
    URGENT = 0
    NORMAL = 1
    BACKGROUND = 2


@dataclass(slots=True, frozen=True)
class ScreenContext:
    active_app: str
    visible_text: str
    user_activity: str
    confidence: float
    captured_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["captured_at"] = self.captured_at.isoformat()
        return payload


@dataclass(slots=True, frozen=True)
class ResourceSnapshot:
    cpu_percent: float
    ram_free_mb: int
    disk_free_mb: int
    gpu_vram_free_mb: int | None
    captured_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["captured_at"] = self.captured_at.isoformat()
        return payload


@dataclass(slots=True, frozen=True)
class VoiceCommand:
    text: str
    duration_s: float
    captured_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["captured_at"] = self.captured_at.isoformat()
        return payload


@dataclass(slots=True, frozen=True)
class DetectedFile:
    path: str
    kind: str
    source_folder: str
    detected_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["detected_at"] = self.detected_at.isoformat()
        return payload


@dataclass(slots=True, frozen=True)
class ClipboardPayload:
    kind: str
    value: str
    changed_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["changed_at"] = self.changed_at.isoformat()
        return payload


@dataclass(slots=True, frozen=True)
class WorkerTask:
    id: str
    priority: WorkerPriority
    kind: str
    payload: dict[str, Any]
    goal_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "priority": int(self.priority),
            "kind": self.kind,
            "payload": self.payload,
            "goal_id": self.goal_id,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(slots=True, frozen=True)
class WorkerResult:
    task_id: str
    status: str
    score: int
    artifacts: dict[str, Any]
    finished_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["finished_at"] = self.finished_at.isoformat()
        return payload


@dataclass(slots=True, frozen=True)
class PageLease:
    service: str
    task_id: str
    page: Any
    context_id: str
    acquired_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "task_id": self.task_id,
            "context_id": self.context_id,
            "acquired_at": self.acquired_at.isoformat(),
        }


@dataclass(slots=True, frozen=True)
class ToolResult:
    success: bool
    reason: str | None = None
    queued: bool = False
    retry_after_s: int | None = None
    provider_key: str | None = None
    artifacts: dict[str, Any] = field(default_factory=dict)
    plain_text: str | None = None
    screenshot_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
