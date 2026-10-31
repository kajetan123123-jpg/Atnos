from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.services.agent_action_preview_service import AgentActionPreviewService


@dataclass(frozen=True, slots=True)
class ApprovalBridgeResult:
    approval_id: str
    status: str
    path: str | None = None
    preview: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "approval_id": self.approval_id,
            "status": self.status,
            "path": self.path,
            "preview": self.preview,
        }


class MonetizationApprovalBridge:
    """Routes high-risk monetization actions to the approval queue instead of executing them."""

    def __init__(self, settings: Settings, session: Session | None = None) -> None:
        self.settings = settings
        self.session = session
        self.fallback_path = settings.data_dir / "monetization" / "approval_queue.jsonl"

    def create_approval(
        self,
        *,
        action: str,
        risk_reason: str,
        payload: dict[str, Any],
    ) -> ApprovalBridgeResult:
        if self.session is not None:
            preview = AgentActionPreviewService(self.session, self.settings).create_preview(
                project_id=None,
                kind="publication",  # type: ignore[arg-type]
                title=f"Approval required: {action}",
                summary=f"Blocked high-risk monetization action: {action}",
                impact_summary=risk_reason,
                preview_payload={"action": action, "risk_reason": risk_reason, **payload},
                required_approval_level="double",
                connector_key="youtube",
                platform_key="youtube",
                risk_level="high",
            )
            return ApprovalBridgeResult(
                approval_id=str(preview.execution_run_id),
                status="awaiting_approval",
                preview=preview.to_dict(),
            )

        self.fallback_path.parent.mkdir(parents=True, exist_ok=True)
        approval_id = f"monetization-approval-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "approval_id": approval_id,
            "status": "awaiting_approval",
            "action": action,
            "risk_reason": risk_reason,
            "payload": payload,
        }
        with self.fallback_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return ApprovalBridgeResult(
            approval_id=approval_id,
            status="awaiting_approval",
            path=str(self.fallback_path.resolve()),
        )
