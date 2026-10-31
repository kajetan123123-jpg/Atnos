from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from atnos.core.config import Settings

logger = logging.getLogger(__name__)


class SlackWebhookNotifier:
    """Small, non-blocking-by-policy Slack webhook adapter for production events."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def notify_pipeline_event(self, event: dict[str, Any]) -> dict[str, Any]:
        if not self._should_send(event):
            return {"status": "skipped"}
        text = self._format_pipeline_event(event)
        return self.send_text(text)

    def send_text(self, text: str) -> dict[str, Any]:
        if not self.settings.slack_notifications_enabled:
            return {"status": "disabled"}
        webhook_url = (self.settings.slack_webhook_url or "").strip()
        if not webhook_url:
            return {"status": "not_configured"}
        try:
            safe_text = _redact_sensitive(text)
            response = httpx.post(webhook_url, json={"text": safe_text[:3000]}, timeout=5.0)
            response.raise_for_status()
        except Exception as exc:
            safe_message = _redact_sensitive(str(exc))
            logger.warning("Slack webhook notification failed: %s", safe_message)
            return {"status": "error", "message": safe_message}
        return {"status": "sent"}

    @staticmethod
    def _should_send(event: dict[str, Any]) -> bool:
        step = str(event.get("step") or "").strip()
        status = str(event.get("status") or "").strip()
        return (
            status in {"error", "qa_passed", "qa_failed", "uploaded_private", "connection_required", "quota_exceeded"}
            or step in {"done", "private_upload", "analytics"}
        )

    @staticmethod
    def _format_pipeline_event(event: dict[str, Any]) -> str:
        step = str(event.get("step") or "pipeline")
        status = str(event.get("status") or "updated")
        job_id = str(event.get("job_id") or "-")
        message = str(event.get("message") or "").strip()
        timestamp = str(event.get("timestamp") or "")
        prefix = ":white_check_mark:" if status in {"qa_passed", "uploaded_private", "completed"} else ":warning:"
        if status in {"error", "qa_failed", "connection_required", "quota_exceeded"}:
            prefix = ":x:"
        parts = [
            f"{prefix} Atnos Content Machine",
            f"Job: `{job_id}`",
            f"Krok: `{step}`",
            f"Status: `{status}`",
        ]
        if message:
            parts.append(f"Wiadomość: {message}")
        if timestamp:
            parts.append(f"Czas: {timestamp}")
        return _redact_sensitive("\n".join(parts))


_SLACK_WEBHOOK_RE = re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/_-]+")
_SLACK_TEST_WEBHOOK_RE = re.compile(r"https://hooks\.slack\.test/services/[A-Za-z0-9/_-]+")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b("
    r"api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|client[_-]?secret|"
    r"token|secret|password|haslo|hasło"
    r")\b\s*[:=]\s*([^&\s,;]+)"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")


def _redact_sensitive(value: str) -> str:
    """Redact webhook URLs and obvious token/secret values from operator-facing text."""
    redacted = _SLACK_WEBHOOK_RE.sub("[redacted-slack-webhook]", value)
    redacted = _SLACK_TEST_WEBHOOK_RE.sub("[redacted-slack-webhook]", redacted)
    redacted = _SECRET_ASSIGNMENT_RE.sub(r"\1=[redacted]", redacted)
    redacted = _BEARER_RE.sub("Bearer [redacted]", redacted)
    return redacted
