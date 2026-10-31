"""Pending destructive-action approvals for Atnos chat tool loop."""
from __future__ import annotations

import json
import secrets
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from atnos.core.config import Settings


@dataclass(slots=True)
class PendingChatApproval:
    approval_id: str
    conversation_id: int
    tool_name: str
    arguments: dict[str, Any]
    summary: str
    reason: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PendingChatApproval":
        return cls(
            approval_id=str(payload["approval_id"]),
            conversation_id=int(payload["conversation_id"]),
            tool_name=str(payload["tool_name"]),
            arguments=dict(payload.get("arguments") or {}),
            summary=str(payload.get("summary") or ""),
            reason=str(payload.get("reason") or ""),
            created_at=str(payload.get("created_at") or ""),
        )


class ChatApprovalStore:
    def __init__(self, settings: Settings) -> None:
        self.root = Path(settings.data_dir).expanduser().resolve() / "chat_approvals"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, conversation_id: int) -> Path:
        return self.root / f"conversation-{int(conversation_id)}.json"

    def create(
        self,
        *,
        conversation_id: int,
        tool_name: str,
        arguments: dict[str, Any],
        summary: str,
        reason: str,
    ) -> PendingChatApproval:
        pending = PendingChatApproval(
            approval_id=secrets.token_urlsafe(12),
            conversation_id=int(conversation_id),
            tool_name=tool_name,
            arguments=dict(arguments),
            summary=summary,
            reason=reason,
            created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )
        self._path(conversation_id).write_text(
            json.dumps(pending.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return pending

    def get(self, conversation_id: int) -> PendingChatApproval | None:
        path = self._path(conversation_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        return PendingChatApproval.from_dict(payload)

    def clear(self, conversation_id: int) -> None:
        path = self._path(conversation_id)
        path.unlink(missing_ok=True)
