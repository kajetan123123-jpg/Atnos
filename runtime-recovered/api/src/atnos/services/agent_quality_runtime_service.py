from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import re
from typing import Any, Literal

from atnos.core.config import Settings


QualityCheckStatus = Literal["pass", "warn", "fail"]
QualityVerdict = Literal["DONE_VERIFIED", "DONE_NEEDS_REVIEW", "FAILED_WITH_REASON"]

SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password|passwd|authorization)\s*[:=]\s*['\"]?[^'\"\s]+"),
    re.compile(r"(?i)(sk-[A-Za-z0-9_\-]{12,})"),
    re.compile(r"(?i)(xox[baprs]-[A-Za-z0-9\-]{12,})"),
    re.compile(r"(?i)(gh[pousr]_[A-Za-z0-9_]{12,})"),
    re.compile(r"(?i)(C:\\Users\\[^\\\s]+\\.ssh\\[^\\\s]+)"),
]


@dataclass(frozen=True, slots=True)
class QualityCheck:
    name: str
    status: QualityCheckStatus
    evidence: str
    score: float | None = None
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "evidence": self.evidence,
            "score": self.score,
            "required": self.required,
        }


class AgentQualityRuntimeService:
    """Records quality gates for agent work without pretending success.

    This service is intentionally small and file-backed. Atnos can call it after
    larger tasks to produce a stable, inspectable verdict with evidence.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.runs_dir = settings.data_dir / "quality-runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def record_run(
        self,
        *,
        task: str,
        area: str,
        checks: list[QualityCheck],
        artifacts: list[str] | None = None,
        notes: str | None = None,
        min_score: float | None = None,
    ) -> dict[str, Any]:
        if not checks:
            raise ValueError("At least one quality check is required.")

        created_at = datetime.now(UTC)
        run_id = f"{created_at.strftime('%Y%m%dT%H%M%SZ')}_{_slugify(area)}"
        score = _average_score(checks)
        threshold = min_score if min_score is not None else 7.0
        verdict = self._resolve_verdict(checks, score=score, min_score=threshold)
        safe_task = _sanitize_text(task)
        safe_area = _sanitize_text(area)
        safe_notes = _sanitize_text(notes or "")
        safe_artifacts = [_sanitize_text(item) for item in (artifacts or [])]
        safe_checks = [
            QualityCheck(
                name=_sanitize_text(check.name),
                status=check.status,
                evidence=_sanitize_text(check.evidence),
                score=check.score,
                required=check.required,
            )
            for check in checks
        ]

        json_path = self.runs_dir / f"{run_id}.json"
        report_path = self.runs_dir / f"{run_id}.md"
        payload: dict[str, Any] = {
            "run_id": run_id,
            "created_at": created_at.isoformat(),
            "task": safe_task,
            "area": safe_area,
            "status": verdict,
            "score": score,
            "min_score": threshold,
            "checks": [check.to_dict() for check in safe_checks],
            "artifacts": safe_artifacts,
            "notes": safe_notes,
            "json_path": json_path.as_posix(),
            "report_path": report_path.as_posix(),
        }

        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        report_path.write_text(_render_markdown(payload), encoding="utf-8")
        self._log_decision(
            "agent_quality_run_recorded",
            {
                "run_id": run_id,
                "area": safe_area,
                "status": verdict,
                "score": score,
                "report_path": report_path.as_posix(),
            },
        )
        return payload

    def latest_runs(self, *, limit: int = 5) -> list[dict[str, Any]]:
        candidates = sorted(self.runs_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        runs: list[dict[str, Any]] = []
        for path in candidates[: max(0, limit)]:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                runs.append(payload)
        return runs

    def status(self) -> dict[str, Any]:
        latest = self.latest_runs(limit=1)
        latest_run = latest[0] if latest else None
        return {
            "ready": True,
            "runs_dir": self.runs_dir.as_posix(),
            "latest_run": _summarize_run(latest_run) if latest_run else None,
            "status_reason": (
                "Quality runtime is recording verified task outcomes."
                if latest_run
                else "Quality runtime is ready; no quality run has been recorded yet."
            ),
        }

    @staticmethod
    def _resolve_verdict(
        checks: list[QualityCheck],
        *,
        score: float,
        min_score: float,
    ) -> QualityVerdict:
        if any(check.status == "fail" and check.required for check in checks):
            return "FAILED_WITH_REASON"
        if any(check.status == "warn" for check in checks) or score < min_score:
            return "DONE_NEEDS_REVIEW"
        return "DONE_VERIFIED"

    def _log_decision(self, event: str, payload: dict[str, Any]) -> None:
        path = self.settings.agent_decisions_log_path
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": event,
            **payload,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _summarize_run(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not payload:
        return None
    checks = payload.get("checks")
    return {
        "run_id": payload.get("run_id"),
        "created_at": payload.get("created_at"),
        "task": payload.get("task"),
        "area": payload.get("area"),
        "status": payload.get("status"),
        "score": payload.get("score"),
        "report_path": payload.get("report_path"),
        "json_path": payload.get("json_path"),
        "evidence_count": len(checks) if isinstance(checks, list) else 0,
    }


def _average_score(checks: list[QualityCheck]) -> float:
    scored = [float(check.score) for check in checks if check.score is not None]
    if not scored:
        status_scores = {"pass": 10.0, "warn": 6.5, "fail": 0.0}
        scored = [status_scores[check.status] for check in checks]
    return round(sum(scored) / len(scored), 2)


def _render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Agent Quality Run",
        "",
        f"- run_id: `{payload['run_id']}`",
        f"- status: `{payload['status']}`",
        f"- score: `{payload['score']}` / 10",
        f"- area: `{payload['area']}`",
        "",
        "## Task",
        str(payload["task"]),
        "",
        "## Checks",
    ]
    for check in payload["checks"]:
        score = check["score"] if check["score"] is not None else "n/a"
        lines.append(f"- `{check['status']}` {check['name']} ({score}/10): {check['evidence']}")
    artifacts = payload.get("artifacts") or []
    if artifacts:
        lines.extend(["", "## Artifacts"])
        lines.extend(f"- `{artifact}`" for artifact in artifacts)
    if payload.get("notes"):
        lines.extend(["", "## Notes", str(payload["notes"])])
    return "\n".join(lines) + "\n"


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:80] or "quality-run"


def _sanitize_text(value: str) -> str:
    cleaned = str(value)
    for pattern in SECRET_PATTERNS:
        cleaned = pattern.sub(_redact_match, cleaned)
    return cleaned


def _redact_match(match: re.Match[str]) -> str:
    candidate = match.group(1) if match.lastindex else ""
    if re.fullmatch(r"(?i)(api[_-]?key|token|secret|password|passwd|authorization)", candidate or ""):
        return f"{candidate}=[REDACTED]"
    return "secret=[REDACTED]"
