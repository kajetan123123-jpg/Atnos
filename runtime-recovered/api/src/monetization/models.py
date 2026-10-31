from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat()


class RevenueVector(StrEnum):
    AUTOMATED_SHORTS_FACTORY = "automated_shorts_factory"


class ExecutionPhase(StrEnum):
    PLAN = "PLAN"
    EXECUTE = "EXECUTE"
    VERIFY = "VERIFY"
    RECOVER = "RECOVER"
    COMMIT = "COMMIT"


class RiskState(StrEnum):
    SAFE = "safe"
    HIGH_RISK = "high_risk"
    APPROVAL_REQUIRED = "approval_required"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class MonetizationRunEvent:
    phase: ExecutionPhase
    status: str
    message: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_timestamp)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["phase"] = self.phase.value
        return payload


@dataclass(slots=True)
class MonetizationRun:
    task: str
    run_id: str = field(default_factory=lambda: f"monetization-{uuid4().hex[:12]}")
    revenue_vector: RevenueVector = RevenueVector.AUTOMATED_SHORTS_FACTORY
    status: str = "planned"
    risk_state: RiskState = RiskState.SAFE
    events: list[MonetizationRunEvent] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    fallback_provider: str | None = None
    approval_id: str | None = None
    created_at: str = field(default_factory=utc_timestamp)
    updated_at: str = field(default_factory=utc_timestamp)

    def record(
        self,
        phase: ExecutionPhase,
        *,
        status: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.events.append(
            MonetizationRunEvent(
                phase=phase,
                status=status,
                message=message,
                payload=payload or {},
            )
        )
        self.updated_at = utc_timestamp()

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task": self.task,
            "revenue_vector": self.revenue_vector.value,
            "status": self.status,
            "risk_state": self.risk_state.value,
            "events": [event.to_dict() for event in self.events],
            "artifacts": dict(self.artifacts),
            "errors": list(self.errors),
            "fallback_provider": self.fallback_provider,
            "approval_id": self.approval_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class InitializationStatus:
    status: str
    revenue_vector: RevenueVector
    state_machine_ready: bool
    channel_ops_ready: bool
    shorts_contract_ready: bool
    approval_gate_ready: bool
    youtube_readiness_status: str
    private_upload_only: bool
    missing_requirements: tuple[str, ...]
    log_path: str
    created_at: str = field(default_factory=utc_timestamp)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "revenue_vector": self.revenue_vector.value,
            "state_machine_ready": self.state_machine_ready,
            "channel_ops_ready": self.channel_ops_ready,
            "shorts_contract_ready": self.shorts_contract_ready,
            "approval_gate_ready": self.approval_gate_ready,
            "youtube_readiness_status": self.youtube_readiness_status,
            "private_upload_only": self.private_upload_only,
            "missing_requirements": list(self.missing_requirements),
            "log_path": self.log_path,
            "created_at": self.created_at,
        }
