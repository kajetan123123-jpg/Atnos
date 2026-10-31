from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from agent.agent_logging import AgentJsonlLogger
from atnos.core.config import Settings
from atnos.repositories.execution_run_repository import ExecutionRunRepository
from atnos.services.agent_action_preview_service import AgentActionPreviewService
from atnos.services.agent_execution_service import AgentExecutionService
from atnos.services.agent_quality_runtime_service import AgentQualityRuntimeService
from atnos.services.browser_runtime_service import BrowserRuntimeService
from atnos.services.local_short_runtime_probe_service import LocalShortRuntimeProbeService
from atnos.services.n8n_youtube_automation_service import N8nYouTubeAutomationService
from monetization.shorts_contract import REQUIRED_SHORT_ARTIFACTS
from tools.ubuntu_control import UbuntuControlError, stop_remote_atnos_processes
from youtube.youtube_readiness import YouTubeReadinessService


ACTIVE_RUN_STATUSES = {"queued", "running"}
ERROR_RUN_STATUSES = {"failed", "blocked", "interrupted"}
ARTIFACT_EXTENSIONS = {
    ".docx",
    ".jpg",
    ".jpeg",
    ".json",
    ".md",
    ".mp4",
    ".pdf",
    ".png",
    ".pptx",
    ".srt",
    ".wav",
    ".webp",
    ".xlsx",
}


@dataclass(frozen=True, slots=True)
class AgentRuntimeStatus:
    state: str
    active_tasks: int
    pending_approvals: int
    errors: int
    last_decision_at: str | None
    last_tool_call_at: str | None


class AgentControlPanelService:
    def __init__(self, settings: Settings, session: Session | None = None) -> None:
        self.settings = settings
        self.session = session
        self.logger = AgentJsonlLogger(settings)

    def status(self) -> dict[str, Any]:
        active_runs = self._active_runs()
        pending_approvals = self.pending_approvals()
        recent = self.recent_logs(limit=12)
        errors = self._collect_errors(active_runs=active_runs, recent_logs=recent)
        runtime_status = AgentRuntimeStatus(
            state=self._resolve_agent_state(active_runs, pending_approvals, errors),
            active_tasks=len(active_runs),
            pending_approvals=len(pending_approvals),
            errors=len(errors),
            last_decision_at=_last_timestamp(recent["decisions"]),
            last_tool_call_at=_last_timestamp(recent["tool_calls"]),
        )
        return {
            "agent": asdict(runtime_status),
            "active_tasks": [self._serialize_run(run) for run in active_runs],
            "ubuntu": self._ubuntu_status(),
            "browser": self._browser_status(),
            "short_pipeline": self._short_pipeline_status(),
            "youtube_automation": self._youtube_automation_status(),
            "monetization_factory": self._monetization_factory_status(),
            "quality_runtime": self._quality_runtime_status(),
            "recent_artifacts": self.recent_artifacts(limit=10),
            "pending_approvals": pending_approvals,
            "errors": errors[:10],
            "logs": self._log_locations(),
        }

    def recent_logs(self, *, limit: int = 20) -> dict[str, list[dict[str, Any]]]:
        return {
            "decisions": _read_recent_jsonl(self.settings.agent_decisions_log_path, limit=limit),
            "tool_calls": _read_recent_jsonl(self.settings.agent_tool_calls_log_path, limit=limit),
        }

    def tools(self) -> dict[str, Any]:
        return {
            "tools": [
                {
                    "key": "ubuntu_control",
                    "label": "Ubuntu SSH sandbox",
                    "status": "configured" if self.settings.atnos_ubuntu_host and self.settings.atnos_ubuntu_key else "not_configured",
                    "risk_level": "high",
                    "log_path": str(self.settings.agent_tool_calls_log_path),
                    "remote_log_path": f"{self.settings.atnos_ubuntu_workdir.rstrip('/')}/logs/commands.log",
                    "actions": [
                        "check_ubuntu_connection",
                        "create_system_report",
                        "bootstrap_workspace",
                        "run_ubuntu_command",
                        "install_package",
                        "run_project_command",
                        "stop_remote_atnos_processes",
                    ],
                },
                {
                    "key": "browser_operator",
                    "label": "Browser operator",
                    "status": "enabled" if self.settings.browser_enabled else "disabled",
                    "risk_level": "medium",
                    "log_path": str(self.settings.browser_log_path),
                    "actions": ["open_page", "read_page", "screenshot", "paste_prompt"],
                },
                {
                    "key": "monetization_shorts_factory",
                    "label": "Monetization Shorts Factory",
                    "status": self._monetization_factory_status()["status"],
                    "risk_level": "medium",
                    "log_path": str(self.settings.data_dir / "logs" / "monetization_init.jsonl"),
                    "actions": [
                        "initialize_framework",
                        "validate_pre_render_contract",
                        "safe_private_upload_readiness",
                        "learning_handoff",
                    ],
                },
                {
                    "key": "content_short",
                    "label": "Local short pipeline",
                    "status": "enabled" if self.settings.atnos_local_shorts_enabled else "disabled",
                    "risk_level": "medium",
                    "log_path": str(self.settings.data_dir / "logs" / "short_pipeline.jsonl"),
                    "actions": ["script", "tts_piper", "subtitles", "thumbnail", "render_ffmpeg", "qa"],
                },
                {
                    "key": "quality_runtime",
                    "label": "Agent quality runtime",
                    "status": "enabled",
                    "risk_level": "low",
                    "log_path": str(self.settings.data_dir / "quality-runs"),
                    "actions": ["record_quality_run", "latest_runs", "quality_status"],
                },
                {
                    "key": "agent_memory",
                    "label": "Agent memory + Obsidian writer",
                    "status": "enabled",
                    "risk_level": "low",
                    "log_path": str(self.settings.data_dir / "logs" / "agent_memory.jsonl"),
                    "actions": ["write_agent_task_log", "write_decision", "search_recent_agent_logs"],
                },
                {
                    "key": "llm_provider_router",
                    "label": "AI provider router",
                    "status": "enabled",
                    "risk_level": "low",
                    "log_path": str(self.settings.agent_decisions_log_path),
                    "actions": [
                        "ollama_local",
                        "openai_cloud",
                        "anthropic_cloud",
                        "gemini_cloud",
                        "task_aware_routing",
                        "strict_local_policy",
                    ],
                },
                {
                    "key": "github_connector",
                    "label": "GitHub connector",
                    "status": "configured" if self.settings.oauth_enabled and self.settings.oauth_github_client_id else "needs_oauth",
                    "risk_level": "medium",
                    "log_path": str(self.settings.agent_tool_calls_log_path),
                    "actions": ["requested_reviews", "pull_requests", "issues", "supervised_write"],
                },
                {
                    "key": "linear_connector",
                    "label": "Linear connector",
                    "status": "account_required",
                    "risk_level": "low",
                    "log_path": str(self.settings.agent_tool_calls_log_path),
                    "actions": ["list_my_tasks", "list_overdue_tasks", "list_today_due", "active_sprint_summary"],
                },
                {
                    "key": "gmail_calendar_connectors",
                    "label": "Gmail + Google Calendar connectors",
                    "status": "configured" if self.settings.oauth_enabled and self.settings.oauth_google_client_id else "needs_oauth",
                    "risk_level": "medium",
                    "log_path": str(self.settings.agent_tool_calls_log_path),
                    "actions": ["gmail_read", "calendar_read", "calendar_write_preview", "approval_required_write"],
                },
                {
                    "key": "youtube_connector",
                    "label": "YouTube private upload + analytics",
                    "status": "configured" if self.settings.oauth_enabled and self.settings.oauth_google_client_id else "needs_oauth",
                    "risk_level": "high",
                    "log_path": str(self.settings.data_dir / "logs" / "youtube_uploads.jsonl"),
                    "actions": ["private_upload_preview", "private_upload_after_approval", "analytics_read"],
                },
                {
                    "key": "n8n_youtube_automation",
                    "label": "n8n YouTube channel ops",
                    "status": "enabled" if self.settings.n8n_enabled else "disabled",
                    "risk_level": "high",
                    "log_path": str(self.settings.data_dir / "logs" / "n8n_youtube_automation.jsonl"),
                    "actions": [
                        "scan_ready_shorts",
                        "daily_channel_ops",
                        "create_private_upload_approval",
                        "sync_analytics",
                    ],
                },
                {
                    "key": "screen_capture_visual_qa",
                    "label": "Screen capture + visual QA",
                    "status": "enabled",
                    "risk_level": "medium",
                    "log_path": str(self.settings.visual_qa_log_path),
                    "actions": ["capture_screenshot", "record_screen", "visual_qa_review", "codex_handoff"],
                },
                {
                    "key": "document_file_tools",
                    "label": "Documents, files and artifacts",
                    "status": "enabled",
                    "risk_level": "low",
                    "log_path": str(self.settings.agent_tool_calls_log_path),
                    "actions": ["upload", "read_file", "analyze_pdf", "write_markdown", "attach_artifact"],
                },
            ]
        }

    def pending_approvals(self) -> list[dict[str, Any]]:
        if self.session is None:
            return []
        service = AgentActionPreviewService(self.session, self.settings)
        preview = service.get_pending_preview()
        return [preview.to_dict()] if preview is not None else []

    def approve_action(self, run_id: int) -> dict[str, Any]:
        if self.session is None:
            raise RuntimeError("Database session is required to approve an action.")
        run = AgentExecutionService(self.session, self.settings).execute_approved_preview(run_id)
        self.logger.log_decision(
            "agent_action_approved",
            {"run_id": run_id, "run": run},
        )
        return {"ok": True, "run": run}

    def reject_action(self, run_id: int, *, reason: str | None = None) -> dict[str, Any]:
        if self.session is None:
            raise RuntimeError("Database session is required to reject an action.")
        preview = AgentActionPreviewService(self.session, self.settings).mark_rejected(run_id, reason=reason)
        self.logger.log_decision(
            "agent_action_rejected",
            {"run_id": run_id, "reason": reason, "preview": preview.to_dict()},
        )
        return {"ok": True, "preview": preview.to_dict()}

    def emergency_stop(self) -> dict[str, Any]:
        interrupted_runs = self._interrupt_active_runs()
        ubuntu_result: dict[str, Any]
        try:
            ubuntu_result = stop_remote_atnos_processes(settings=self.settings)
            ubuntu_ok = int(ubuntu_result.get("exit_code") or 0) == 0
        except UbuntuControlError as exc:
            ubuntu_ok = False
            ubuntu_result = {"status": "ubuntu_stop_failed", "error": str(exc)}
        except Exception as exc:
            ubuntu_ok = False
            ubuntu_result = {"status": "ubuntu_stop_failed", "error": f"{type(exc).__name__}: {exc}"}

        payload = {
            "event": "agent_emergency_stop",
            "interrupted_runs": interrupted_runs,
            "ubuntu_stop": {"ok": ubuntu_ok, "result": ubuntu_result},
            "message": "Emergency stop requested. No data deletion was performed.",
        }
        self.logger.log_decision("agent_emergency_stop", payload)
        return {"ok": True, **payload}

    def recent_artifacts(self, *, limit: int = 10) -> list[dict[str, Any]]:
        roots = [
            self.settings.shorts_output_dir,
            self.settings.data_dir / "atnos-outputs",
            self.settings.browser_screenshot_dir,
            self.settings.data_dir / "memory",
        ]
        candidates: list[Path] = []
        for root in roots:
            if not root.exists() or not root.is_dir():
                continue
            for path in root.rglob("*"):
                if path.is_file() and path.suffix.lower() in ARTIFACT_EXTENSIONS:
                    candidates.append(path.resolve())
        candidates = sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True)
        return [self._serialize_artifact(path) for path in candidates[:limit]]

    def _active_runs(self) -> list[Any]:
        if self.session is None:
            return []
        return [
            run
            for run in ExecutionRunRepository(self.session).list_runs(limit=24)
            if str(run.status) in ACTIVE_RUN_STATUSES
        ]

    def _interrupt_active_runs(self) -> list[dict[str, Any]]:
        if self.session is None:
            return []
        repository = ExecutionRunRepository(self.session)
        now = datetime.now(UTC)
        interrupted: list[dict[str, Any]] = []
        for run in repository.list_runs(limit=100):
            if str(run.status) not in ACTIVE_RUN_STATUSES:
                continue
            run.status = "interrupted"
            run.error_message = "Emergency stop requested by user."
            run.finished_at = now
            output_json = dict(run.output_json or {})
            output_json["emergency_stop_requested"] = True
            output_json["emergency_stop_at"] = now.isoformat()
            run.output_json = output_json
            repository.save_run(run)
            for step in repository.list_steps(run.id):
                if str(step.status) in ACTIVE_RUN_STATUSES:
                    step.status = "interrupted"
                    step.error_message = "Emergency stop requested by user."
                    step.finished_at = now
                    repository.save_step(step)
            interrupted.append(self._serialize_run(run))
        if interrupted:
            self.session.flush()
        return interrupted

    def _collect_errors(self, *, active_runs: list[Any], recent_logs: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []
        if self.session is not None:
            for run in ExecutionRunRepository(self.session).list_runs(limit=24):
                if str(run.status) in ERROR_RUN_STATUSES or run.error_message:
                    errors.append(
                        {
                            "source": "execution_run",
                            "status": str(run.status),
                            "message": run.error_message or run.goal,
                            "timestamp": run.finished_at.isoformat() if run.finished_at else run.updated_at.isoformat(),
                        }
                    )
        for source, items in recent_logs.items():
            for item in items:
                if item.get("error") or item.get("success") is False or str(item.get("status") or "").lower() in ERROR_RUN_STATUSES:
                    errors.append(
                        {
                            "source": source,
                            "status": str(item.get("status") or "error"),
                            "message": str(item.get("error") or item.get("event") or item.get("tool_name") or "unknown error"),
                            "timestamp": str(item.get("timestamp") or ""),
                        }
                    )
        return sorted(errors, key=lambda item: str(item.get("timestamp") or ""), reverse=True)

    def _ubuntu_status(self) -> dict[str, Any]:
        key_path = self.settings.atnos_ubuntu_key
        key_ready = bool(key_path and key_path.exists() and key_path.is_file())
        configured = bool(self.settings.atnos_ubuntu_host and key_ready)
        return {
            "configured": configured,
            "ready": configured,
            "host": self.settings.atnos_ubuntu_host,
            "user": self.settings.atnos_ubuntu_user,
            "workdir": self.settings.atnos_ubuntu_workdir,
            "key_configured": key_path is not None,
            "key_exists": key_ready,
            "sudo_allowed": self.settings.atnos_ubuntu_allow_sudo,
            "status_reason": (
                "Ubuntu SSH config is present. Live commands run only through the Ubuntu skill or emergency stop."
                if configured
                else "Ubuntu SSH is not fully configured."
            ),
            "remote_log_path": f"{self.settings.atnos_ubuntu_workdir.rstrip('/')}/logs/commands.log",
        }

    def _browser_status(self) -> dict[str, Any]:
        state = BrowserRuntimeService(self.settings).describe()
        return state.to_dict()

    def _short_pipeline_status(self) -> dict[str, Any]:
        return LocalShortRuntimeProbeService(self.settings).snapshot().to_dict()

    def _youtube_automation_status(self) -> dict[str, Any]:
        status = N8nYouTubeAutomationService(self.settings).status()
        enabled = bool(status.get("enabled")) and str(status.get("mode") or "") != "disabled"
        docker_status = str(status.get("n8n_docker_status") or "unknown")
        fallback_available = bool(status.get("fallback_runner_available"))
        workflow_status = str(status.get("workflow_templates_status") or "unknown")
        can_run_without_docker = enabled and fallback_available and workflow_status == "ready"
        return {
            **status,
            "ready": enabled and (docker_status == "running" or can_run_without_docker),
            "status_reason": (
                f"n8n Docker: {docker_status}. Fallback runner: {'available' if fallback_available else 'missing'}."
                if enabled
                else "n8n channel ops are disabled."
            ),
            "can_run_without_docker": can_run_without_docker,
        }

    def _quality_runtime_status(self) -> dict[str, Any]:
        return AgentQualityRuntimeService(self.settings).status()

    def _log_locations(self) -> dict[str, str]:
        return {
            "agent_decisions": str(self.settings.agent_decisions_log_path),
            "tool_calls": str(self.settings.agent_tool_calls_log_path),
            "browser_actions": str(self.settings.browser_log_path),
            "agent_memory": str(self.settings.data_dir / "logs" / "agent_memory.jsonl"),
            "short_pipeline": str(self.settings.data_dir / "logs" / "short_pipeline.jsonl"),
            "n8n_youtube_automation": str(self.settings.data_dir / "logs" / "n8n_youtube_automation.jsonl"),
            "monetization": str(self.settings.data_dir / "logs" / "monetization_init.jsonl"),
            "quality_runs": str(self.settings.data_dir / "quality-runs"),
        }

    def _monetization_factory_status(self) -> dict[str, Any]:
        status_path = self.settings.data_dir / "monetization" / "initialization_status.json"
        payload: dict[str, Any] = {}
        if status_path.exists():
            try:
                raw = json.loads(status_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    payload = raw
            except (OSError, json.JSONDecodeError):
                payload = {"status": "invalid_status_file"}
        youtube_status = YouTubeReadinessService(self.settings).check(run_probe=False).status
        status_value = str(payload.get("status") or "not_initialized")
        state_machine_ready = bool(payload.get("state_machine_ready", status_value == "initialized"))
        channel_ops_ready = bool(payload.get("channel_ops_ready", status_value == "initialized"))
        shorts_contract_ready = bool(payload.get("shorts_contract_ready", True))
        approval_gate_ready = bool(payload.get("approval_gate_ready", True))
        private_upload_only = bool(payload.get("private_upload_only", True))
        ready = all((state_machine_ready, channel_ops_ready, shorts_contract_ready, approval_gate_ready))
        if status_value == "not_initialized":
            ready = False
        if youtube_status in {"missing_client_secret", "missing_token", "invalid_token"}:
            reason = (
                "Factory can create local packages. YouTube OAuth is not ready, so it stops at manual_upload_ready."
            )
        elif youtube_status in {"configured", "ready"}:
            reason = "Factory can create private-upload-ready packages; upload still requires approval."
        else:
            reason = "Factory status is available, but YouTube readiness is unknown."
        return {
            "status": status_value,
            "ready": ready,
            "revenue_vector": str(payload.get("revenue_vector") or "automated_shorts_factory"),
            "state_machine_ready": state_machine_ready,
            "channel_ops_ready": channel_ops_ready,
            "shorts_contract_ready": shorts_contract_ready,
            "approval_gate_ready": approval_gate_ready,
            "youtube_readiness_status": str(youtube_status),
            "private_upload_only": private_upload_only,
            "manual_upload_ready_supported": True,
            "missing_requirements": list(payload.get("missing_requirements") or []),
            "required_artifacts": list(REQUIRED_SHORT_ARTIFACTS),
            "status_path": str(status_path.resolve()),
            "log_path": str(payload.get("log_path") or (self.settings.data_dir / "logs" / "monetization_init.jsonl")),
            "created_at": payload.get("created_at"),
            "status_reason": reason,
        }

    def _serialize_run(self, run: Any) -> dict[str, Any]:
        return {
            "execution_run_id": run.id,
            "project_id": run.project_id,
            "run_type": run.run_type,
            "status": run.status,
            "goal": run.goal,
            "risk_level": run.risk_level,
            "connector_key": run.connector_key,
            "platform_key": run.platform_key,
            "error_message": run.error_message,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        }

    def _serialize_artifact(self, path: Path) -> dict[str, Any]:
        stat = path.stat()
        try:
            relative = path.relative_to(self.settings.data_dir.resolve()).as_posix()
        except ValueError:
            relative = path.as_posix()
        return {
            "path": path.as_posix(),
            "storage_path": relative,
            "filename": path.name,
            "extension": path.suffix.lower(),
            "size_bytes": stat.st_size,
            "updated_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
        }

    @staticmethod
    def _resolve_agent_state(active_runs: list[Any], pending_approvals: list[dict[str, Any]], errors: list[dict[str, Any]]) -> str:
        if pending_approvals:
            return "awaiting_human"
        if active_runs:
            return "executing"
        if errors:
            return "needs_attention"
        return "idle"


def _read_recent_jsonl(path: Path, *, limit: int) -> list[dict[str, Any]]:
    if not path.exists() or not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    items: list[dict[str, Any]] = []
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = {"timestamp": None, "event": "malformed_log_line", "raw": stripped[:500]}
        if isinstance(parsed, dict):
            items.append(parsed)
        if len(items) >= limit:
            break
    return items


def _last_timestamp(items: list[dict[str, Any]]) -> str | None:
    for item in items:
        timestamp = item.get("timestamp")
        if timestamp is not None:
            return str(timestamp)
    return None
