from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.repositories.execution_run_repository import ExecutionRunRepository
from atnos.repositories.project_repository import ProjectRepository


PreviewKind = Literal[
    "browser_navigate",
    "browser_form_submit",
    "file_write",
    "file_create",
    "file_delete",
    "publication",
    "api_billing",
    "secret_access",
    "main_pc_action",
]


class AgentActionPreviewError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AgentActionPreviewView:
    execution_run_id: int
    project_id: int
    kind: PreviewKind
    title: str
    summary: str
    impact_summary: str
    required_approval_level: Literal["single", "double"]
    preview_payload: dict[str, object]
    status: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class AgentActionPreviewService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.execution_runs = ExecutionRunRepository(session)
        self.projects = ProjectRepository(session)

    def create_preview(
        self,
        *,
        project_id: int | None,
        kind: PreviewKind,
        title: str,
        summary: str,
        impact_summary: str,
        preview_payload: dict[str, object],
        required_approval_level: Literal["single", "double"] = "single",
        connector_key: str | None = None,
        platform_key: str | None = None,
        risk_level: str = "medium",
    ) -> AgentActionPreviewView:
        resolved_project_id = self._resolve_project_id(project_id)
        now = self._now()
        run = self.execution_runs.create_run(
            project_id=resolved_project_id,
            scheduler_job_id=None,
            account_id=None,
            trust_grant_id=None,
            run_type=f"agent.preview.{kind}",
            status="waiting_for_input",
            goal=summary,
            connector_key=connector_key,
            platform_key=platform_key,
            risk_level=risk_level,
            input_json={
                "preview_kind": kind,
                "preview_payload": dict(preview_payload),
            },
            output_json={
                "preview": {
                    "kind": kind,
                    "title": title,
                    "summary": summary,
                    "impact_summary": impact_summary,
                    "required_approval_level": required_approval_level,
                    "preview_payload": dict(preview_payload),
                },
                "approval_required": True,
                "approval_status": "pending",
            },
            artifacts_json={},
            error_message=None,
            started_at=None,
            finished_at=None,
        )
        self.execution_runs.create_step(
            execution_run_id=run.id,
            step_index=0,
            step_type=kind,
            step_name=title,
            status="waiting_for_input",
            selector_hint=None,
            input_json=dict(preview_payload),
            output_json={},
            error_message=None,
            screenshot_path=None,
            started_at=None,
            finished_at=None,
        )
        self.session.flush()
        return self.serialize_preview(run.id)

    def get_pending_preview(self) -> AgentActionPreviewView | None:
        for run in self.execution_runs.list_runs(limit=24):
            if not str(run.run_type).startswith("agent.preview."):
                continue
            if run.status == "waiting_for_input":
                return self.serialize_preview(run.id)
        return None

    def serialize_preview(self, run_id: int) -> AgentActionPreviewView:
        run = self.execution_runs.get_run(run_id)
        if run is None or not str(run.run_type).startswith("agent.preview."):
            raise AgentActionPreviewError(f"Unknown agent action preview '{run_id}'.")
        preview = dict(run.output_json or {}).get("preview")
        if not isinstance(preview, dict):
            raise AgentActionPreviewError(f"Execution run '{run_id}' does not contain preview metadata.")
        return AgentActionPreviewView(
            execution_run_id=run.id,
            project_id=run.project_id,
            kind=str(preview.get("kind") or "").strip().lower(),  # type: ignore[arg-type]
            title=str(preview.get("title") or "Agent preview"),
            summary=str(preview.get("summary") or run.goal),
            impact_summary=str(preview.get("impact_summary") or ""),
            required_approval_level=str(preview.get("required_approval_level") or "single"),  # type: ignore[arg-type]
            preview_payload=(
                dict(preview.get("preview_payload") or {})
                if isinstance(preview.get("preview_payload"), dict)
                else {}
            ),
            status=str(run.status),
            created_at=run.created_at.isoformat() if run.created_at is not None else "",
            updated_at=run.updated_at.isoformat() if run.updated_at is not None else "",
        )

    def mark_approved(self, run_id: int) -> AgentActionPreviewView:
        run = self._require_preview_run(run_id)
        output_json = dict(run.output_json or {})
        output_json["approval_status"] = "approved"
        output_json["approved_at"] = self._now().isoformat()
        run.output_json = output_json
        run.status = "approved"
        self.execution_runs.save_run(run)
        for step in self.execution_runs.list_steps(run.id):
            if step.status == "waiting_for_input":
                step.status = "approved"
                self.execution_runs.save_step(step)
        self.session.flush()
        return self.serialize_preview(run.id)

    def mark_rejected(self, run_id: int, *, reason: str | None = None) -> AgentActionPreviewView:
        run = self._require_preview_run(run_id)
        output_json = dict(run.output_json or {})
        output_json["approval_status"] = "rejected"
        if reason:
            output_json["rejection_reason"] = reason
        run.output_json = output_json
        run.status = "blocked"
        run.error_message = reason or "Preview rejected by user."
        run.finished_at = self._now()
        self.execution_runs.save_run(run)
        for step in self.execution_runs.list_steps(run.id):
            if step.status == "waiting_for_input":
                step.status = "blocked"
                step.error_message = run.error_message
                step.finished_at = self._now()
                self.execution_runs.save_step(step)
        self.session.flush()
        return self.serialize_preview(run.id)

    def _require_preview_run(self, run_id: int):
        run = self.execution_runs.get_run(run_id)
        if run is None or not str(run.run_type).startswith("agent.preview."):
            raise AgentActionPreviewError(f"Unknown agent action preview '{run_id}'.")
        return run

    def _resolve_project_id(self, project_id: int | None) -> int:
        if project_id is not None:
            project = self.projects.get(project_id)
            if project is None:
                raise AgentActionPreviewError(f"Unknown project '{project_id}'.")
            return project.id
        projects = self.projects.list_all()
        if not projects:
            raise AgentActionPreviewError("At least one project is required before creating a preview.")
        return projects[0].id

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)
