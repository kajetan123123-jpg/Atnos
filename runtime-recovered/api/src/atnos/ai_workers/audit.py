from __future__ import annotations

from typing import Any

from atnos.ai_workers.redaction import redact_payload


def build_audit_payload(*, event: str, worker_key: str | None, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "event": event,
        "worker_key": worker_key,
        "payload": redact_payload(payload),
    }
