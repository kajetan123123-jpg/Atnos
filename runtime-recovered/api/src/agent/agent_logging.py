from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from atnos.core.config import Settings
from atnos.core.logging import append_redacted_jsonl


def _json_default(value: object) -> str:
    if isinstance(value, Path):
        return str(value)
    return str(value)


class AgentJsonlLogger:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.decisions_path = settings.agent_decisions_log_path
        self.tool_calls_path = settings.agent_tool_calls_log_path
        self.decisions_path.parent.mkdir(parents=True, exist_ok=True)
        self.tool_calls_path.parent.mkdir(parents=True, exist_ok=True)

    def log_decision(self, event: str, payload: dict[str, Any]) -> None:
        self._write_jsonl(self.decisions_path, {"event": event, **payload})

    def log_tool_call(self, payload: dict[str, Any]) -> None:
        self._write_jsonl(self.tool_calls_path, payload)

    def _write_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            **payload,
        }
        append_redacted_jsonl(path, record, default=_json_default)
