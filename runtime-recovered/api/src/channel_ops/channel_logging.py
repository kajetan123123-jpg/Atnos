from __future__ import annotations

from pathlib import Path
from typing import Any

from atnos.core.logging import append_redacted_jsonl

from .channel_models import utc_now


class ChannelOpsLogger:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.touch(exist_ok=True)

    def log(self, event: str, payload: dict[str, Any] | None = None) -> None:
        record = {
            "timestamp": utc_now(),
            "event": event,
            "payload": payload or {},
        }
        append_redacted_jsonl(self.log_path, record)
