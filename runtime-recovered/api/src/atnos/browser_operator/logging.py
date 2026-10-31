from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from atnos.browser_operator.models import BrowserAction
from atnos.core.config import Settings
from atnos.core.logging import append_redacted_jsonl


def write_browser_action_log(
    settings: Settings,
    *,
    url: str,
    action: BrowserAction,
    result: str,
    screenshot_path: str | None = None,
) -> Path:
    payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        "url": url,
        "action_type": action.action_type,
        "target": action.target,
        "reason": action.reason,
        "risk_level": action.risk_level,
        "result": result,
        "screenshot_path": screenshot_path,
    }
    log_path = settings.browser_log_path
    assert log_path is not None
    append_redacted_jsonl(log_path, payload)
    return log_path


def write_browser_failure_log(
    settings: Settings,
    *,
    site: str,
    task: str,
    failed_step: str,
    reason: str,
    screenshot_path: str | None = None,
) -> Path:
    payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        "site": site,
        "task": task,
        "failed_step": failed_step,
        "reason": reason,
        "screenshot": screenshot_path,
    }
    log_path = (settings.app_root / "data" / "browser" / "browser_failures.jsonl").resolve()
    append_redacted_jsonl(log_path, payload)
    return log_path
