from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


WorkerStatus = Literal[
    "ready",
    "handoff_only",
    "disabled",
    "not_configured",
    "login_required",
    "runtime_unavailable",
    "blocked_by_policy",
    "error",
]


@dataclass(frozen=True, slots=True)
class WorkerStatusSnapshot:
    worker_key: str
    label: str
    status: WorkerStatus
    ready: bool
    reason: str
    capabilities: tuple[str, ...] = ()
    allowed_task_types: tuple[str, ...] = ()
    requires_browser: bool = False
    safety_mode: str = "supervised_high_trust"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_key": self.worker_key,
            "label": self.label,
            "status": self.status,
            "ready": self.ready,
            "reason": self.reason,
            "capabilities": list(self.capabilities),
            "allowed_task_types": list(self.allowed_task_types),
            "requires_browser": self.requires_browser,
            "safety_mode": self.safety_mode,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class WorkerExecutionResult:
    worker_key: str
    status: str
    summary: str
    result: dict[str, Any]
    blocked_reason: str | None = None
