from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.repositories.execution_run_repository import ExecutionRunRepository
from atnos.repositories.execution_trace_repository import ExecutionTraceRepository
from atnos.repositories.local_file_grant_repository import LocalFileGrantRepository
from atnos.repositories.project_repository import ProjectRepository
from atnos.services.agent_action_preview_service import (
    AgentActionPreviewError,
    AgentActionPreviewService,
)
from atnos.services.browser_recipe_runner_service import (
    BrowserRecipeApprovalRequiredError,
    BrowserRecipeBlockedError,
    BrowserRecipeError,
    BrowserRecipeRunnerService,
)
from atnos.services.local_file_grant_service import LocalFileGrantService, LocalFileTraceContext
from atnos.services.runtime_settings_service import RuntimeSettingsService


class AgentExecutionError(RuntimeError):
    pass


class AgentExecutionService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.execution_runs = ExecutionRunRepository(session)
        self.projects = ProjectRepository(session)
        self.local_file_grants = LocalFileGrantRepository(session)
        self.execution_traces = ExecutionTraceRepository(session)
        self.preview_service = AgentActionPreviewService(session, settings)
        self.runtime_controls = RuntimeSettingsService(session, settings).snapshot()

    def start_browser_recipe(
        self,
        *,
        project_id: int | None,
        recipe: dict[str, object],
        connector_key: str | None = "browser",
        platform_key: str | None = "browser",
        risk_level: str = "medium",
    ) -> dict[str, object]:
        resolved_project_id = self._resolve_project_id(project_id)
        try:
            result = BrowserRecipeRunnerService(
                self.settings,
                session=self.session,
                runtime_controls=self.runtime_controls,
            ).run(recipe)
        except BrowserRecipeApprovalRequiredError as exc:
            preview_payload = {
                "url": str((exc.details or {}).get("url") or ""),
                "host": str((exc.details or {}).get("host") or ""),
                "recipe": dict(recipe),
            }
            preview = self.preview_service.create_preview(
                project_id=resolved_project_id,
                kind="browser_navigate",
                title="Nawigacja przegladarki wymaga approval",
                summary=str(exc),
                impact_summary="Atnos chce wejsc na domenę spoza allowlisty w trybie whitelist-only.",
                preview_payload=preview_payload,
                required_approval_level="single",
                connector_key=connector_key,
                platform_key=platform_key,
                risk_level=risk_level,
            )
            return {
                "preview": preview.to_dict(),
                "run": self.serialize_run(preview.execution_run_id),
            }
        except BrowserRecipeBlockedError as exc:
            run = self.execution_runs.create_run(
                project_id=resolved_project_id,
                scheduler_job_id=None,
                account_id=None,
                trust_grant_id=None,
                run_type="agent.browser_recipe",
                status="blocked",
                goal="Blocked browser recipe",
                connector_key=connector_key,
                platform_key=platform_key,
                risk_level=risk_level,
                input_json=dict(recipe),
                output_json={"code": exc.code, "details": dict(exc.details)},
                artifacts_json=dict(exc.artifacts),
                error_message=str(exc),
                started_at=self._now(),
                finished_at=self._now(),
            )
            self.session.flush()
            return {"preview": None, "run": self.serialize_run(run.id)}
        except BrowserRecipeError as exc:
            run = self.execution_runs.create_run(
                project_id=resolved_project_id,
                scheduler_job_id=None,
                account_id=None,
                trust_grant_id=None,
                run_type="agent.browser_recipe",
                status="failed",
                goal="Browser recipe failed",
                connector_key=connector_key,
                platform_key=platform_key,
                risk_level=risk_level,
                input_json=dict(recipe),
                output_json={"code": exc.code, "details": dict(exc.details)},
                artifacts_json=dict(exc.artifacts),
                error_message=str(exc),
                started_at=self._now(),
                finished_at=self._now(),
            )
            self.session.flush()
            return {"preview": None, "run": self.serialize_run(run.id)}

        run = self.execution_runs.create_run(
            project_id=resolved_project_id,
            scheduler_job_id=None,
            account_id=None,
            trust_grant_id=None,
            run_type="agent.browser_recipe",
            status="done",
            goal=str(recipe.get("recipe_name") or "Browser recipe"),
            connector_key=connector_key,
            platform_key=platform_key,
            risk_level=risk_level,
            input_json=dict(recipe),
            output_json={"browser_result": result},
            artifacts_json=dict(result.get("artifacts") or {}),
            error_message=None,
            started_at=self._now(),
            finished_at=self._now(),
        )
        self._persist_browser_steps(run.id, recipe, result)
        self.session.flush()
        return {"preview": None, "run": self.serialize_run(run.id)}

    def execute_approved_preview(self, run_id: int) -> dict[str, object]:
        run = self.execution_runs.get_run(run_id)
        if run is None:
            raise AgentExecutionError(f"Unknown execution run '{run_id}'.")
        if run.status not in {"approved", "waiting_for_input"}:
            raise AgentExecutionError(f"Execution run '{run_id}' is not waiting for approval.")
        preview = self.preview_service.serialize_preview(run_id)
        if run.status == "waiting_for_input":
            self.preview_service.mark_approved(run_id)
            run = self.execution_runs.get_run(run_id)
            assert run is not None

        run.status = "running"
        run.started_at = run.started_at or self._now()
        self.execution_runs.save_run(run)
        try:
            if preview.kind in {"browser_navigate", "browser_form_submit"}:
                recipe = preview.preview_payload.get("recipe")
                if not isinstance(recipe, dict):
                    url = str(preview.preview_payload.get("url") or "").strip()
                    if not url:
                        raise AgentExecutionError("Approved browser preview does not contain a runnable recipe.")
                    recipe = {
                        "recipe_name": preview.kind,
                        "steps": [{"type": "navigate", "url": url}],
                    }
                browser_result = BrowserRecipeRunnerService(
                    self.settings,
                    session=self.session,
                    runtime_controls=self.runtime_controls,
                ).run(recipe)
                run.status = "done"
                run.output_json = {
                    **dict(run.output_json or {}),
                    "approval_status": "approved",
                    "browser_result": browser_result,
                }
                run.artifacts_json = dict(browser_result.get("artifacts") or {})
                run.finished_at = self._now()
                self.execution_runs.save_run(run)
                self._persist_browser_steps(run.id, recipe, browser_result)
            elif preview.kind in {"file_write", "file_create"}:
                self._execute_file_write_preview(run, preview.preview_payload)
            elif preview.kind == "file_delete":
                self._execute_file_delete_preview(run, preview.preview_payload)
            else:
                raise AgentExecutionError(f"Unsupported preview kind '{preview.kind}'.")
        except Exception as exc:
            run.status = "failed"
            run.error_message = str(exc)
            run.finished_at = self._now()
            self.execution_runs.save_run(run)
            raise
        self.session.flush()
        return self.serialize_run(run.id)

    def list_agent_runs(self, *, limit: int = 20) -> list[dict[str, object]]:
        runs = []
        for item in self.execution_runs.list_runs(limit=limit * 2):
            if str(item.run_type).startswith("agent."):
                runs.append(self.serialize_run(item.id))
            if len(runs) >= limit:
                break
        return runs

    def get_latest_agent_run(self) -> dict[str, object] | None:
        runs = self.list_agent_runs(limit=1)
        return runs[0] if runs else None

    def pause(self, run_id: int) -> dict[str, object]:
        run = self._require_agent_run(run_id)
        if run.status in {"done", "failed", "blocked", "interrupted"}:
            raise AgentExecutionError(f"Execution run '{run_id}' can no longer be paused.")
        output_json = dict(run.output_json or {})
        output_json["pause_requested"] = True
        run.output_json = output_json
        run.status = "paused"
        self.execution_runs.save_run(run)
        self.session.flush()
        return self.serialize_run(run.id)

    def interrupt(self, run_id: int) -> dict[str, object]:
        run = self._require_agent_run(run_id)
        if run.status in {"done", "failed", "blocked", "interrupted"}:
            raise AgentExecutionError(f"Execution run '{run_id}' can no longer be interrupted.")
        output_json = dict(run.output_json or {})
        output_json["interrupt_requested"] = True
        run.output_json = output_json
        run.status = "interrupted"
        run.finished_at = self._now()
        self.execution_runs.save_run(run)
        self.session.flush()
        return self.serialize_run(run.id)

    def resume(self, run_id: int) -> dict[str, object]:
        run = self._require_agent_run(run_id)
        if run.status != "paused":
            raise AgentExecutionError(f"Execution run '{run_id}' is not paused.")
        output_json = dict(run.output_json or {})
        output_json.pop("pause_requested", None)
        run.output_json = output_json
        run.status = "running"
        run.finished_at = None
        run.started_at = run.started_at or self._now()
        self.execution_runs.save_run(run)
        self.session.flush()
        return self.serialize_run(run.id)

    def serialize_run(self, run_id: int) -> dict[str, object]:
        run = self.execution_runs.get_run(run_id)
        if run is None:
            raise AgentExecutionError(f"Unknown execution run '{run_id}'.")
        project = self.projects.get(run.project_id)
        steps = [
            {
                "execution_step_id": step.id,
                "step_index": step.step_index,
                "step_type": step.step_type,
                "step_name": step.step_name,
                "status": step.status,
                "selector_hint": step.selector_hint,
                "error_message": step.error_message,
                "screenshot_path": step.screenshot_path,
                "started_at": step.started_at.isoformat() if step.started_at is not None else None,
                "finished_at": step.finished_at.isoformat() if step.finished_at is not None else None,
            }
            for step in self.execution_runs.list_steps(run.id)
        ]
        preview = None
        if str(run.run_type).startswith("agent.preview."):
            try:
                preview = self.preview_service.serialize_preview(run.id).to_dict()
            except AgentActionPreviewError:
                preview = None
        return {
            "execution_run_id": run.id,
            "project_id": run.project_id,
            "project_name": project.name if project is not None else None,
            "scheduler_job_id": run.scheduler_job_id,
            "account_id": run.account_id,
            "trust_grant_id": run.trust_grant_id,
            "run_type": run.run_type,
            "status": run.status,
            "goal": run.goal,
            "connector_key": run.connector_key,
            "platform_key": run.platform_key,
            "risk_level": run.risk_level,
            "input_json": dict(run.input_json or {}),
            "output_json": dict(run.output_json or {}),
            "artifacts_json": dict(run.artifacts_json or {}),
            "error_message": run.error_message,
            "started_at": run.started_at.isoformat() if run.started_at is not None else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at is not None else None,
            "steps": steps,
            "preview": preview,
        }

    def _execute_file_write_preview(
        self,
        run,
        preview_payload: dict[str, object],
    ) -> None:
        grant_id = int(preview_payload.get("grant_id") or 0)
        relative_path = str(preview_payload.get("path") or "").strip()
        content = str(preview_payload.get("content") or "")
        grant_record = self.local_file_grants.get(grant_id)
        if grant_record is None:
            raise AgentExecutionError(f"Unknown local file grant '{grant_id}'.")
        service = LocalFileGrantService.from_grant_record(
            grant_record,
            trace_context=LocalFileTraceContext(project_id=run.project_id),
            trace_repository=self.execution_traces,
        )
        step = self._ensure_single_step(
            run.id,
            step_type=str(preview_payload.get("kind") or "file_write"),
            step_name=f"Write {relative_path}",
            selector_hint=relative_path or None,
            input_json=dict(preview_payload),
        )
        step.status = "running"
        step.started_at = self._now()
        self.execution_runs.save_step(step)
        written = service.write_files_atomically({relative_path: content})
        step.status = "done"
        step.output_json = {"written_paths": written}
        step.finished_at = self._now()
        self.execution_runs.save_step(step)
        run.status = "done"
        run.output_json = {
            **dict(run.output_json or {}),
            "approval_status": "approved",
            "written_paths": written,
        }
        run.finished_at = self._now()
        self.execution_runs.save_run(run)

    def _execute_file_delete_preview(
        self,
        run,
        preview_payload: dict[str, object],
    ) -> None:
        grant_id = int(preview_payload.get("grant_id") or 0)
        relative_path = str(preview_payload.get("path") or "").strip()
        grant_record = self.local_file_grants.get(grant_id)
        if grant_record is None:
            raise AgentExecutionError(f"Unknown local file grant '{grant_id}'.")
        service = LocalFileGrantService.from_grant_record(
            grant_record,
            trace_context=LocalFileTraceContext(project_id=run.project_id),
            trace_repository=self.execution_traces,
        )
        step = self._ensure_single_step(
            run.id,
            step_type="file_delete",
            step_name=f"Delete {relative_path}",
            selector_hint=relative_path or None,
            input_json=dict(preview_payload),
        )
        step.status = "running"
        step.started_at = self._now()
        self.execution_runs.save_step(step)
        deleted = service.delete_paths([relative_path])
        step.status = "done"
        step.output_json = {"deleted_paths": deleted}
        step.finished_at = self._now()
        self.execution_runs.save_step(step)
        run.status = "done"
        run.output_json = {
            **dict(run.output_json or {}),
            "approval_status": "approved",
            "deleted_paths": deleted,
        }
        run.finished_at = self._now()
        self.execution_runs.save_run(run)

    def _persist_browser_steps(
        self,
        run_id: int,
        recipe: dict[str, object],
        result: dict[str, object],
    ) -> None:
        raw_steps = recipe.get("steps")
        raw_results = result.get("results")
        if not isinstance(raw_steps, list):
            raw_steps = []
        mapped_results = raw_results if isinstance(raw_results, list) else []
        for index, raw_step in enumerate(raw_steps):
            step_payload = raw_step if isinstance(raw_step, dict) else {}
            step_result = (
                mapped_results[index]
                if index < len(mapped_results) and isinstance(mapped_results[index], dict)
                else {}
            )
            self.execution_runs.create_step(
                execution_run_id=run_id,
                step_index=index,
                step_type=str(step_payload.get("type") or "browser_step"),
                step_name=str(step_payload.get("type") or "browser_step"),
                status="done",
                selector_hint=(
                    str(step_payload.get("selector"))
                    if step_payload.get("selector") is not None
                    else None
                ),
                input_json=dict(step_payload),
                output_json=dict(step_result),
                error_message=None,
                screenshot_path=(
                    str(step_result.get("path"))
                    if isinstance(step_result, dict) and str(step_payload.get("type")) == "screenshot" and step_result.get("path")
                    else None
                ),
                started_at=self._now(),
                finished_at=self._now(),
            )

    def _ensure_single_step(
        self,
        run_id: int,
        *,
        step_type: str,
        step_name: str,
        selector_hint: str | None,
        input_json: dict[str, object],
    ):
        steps = self.execution_runs.list_steps(run_id)
        if steps:
            return steps[0]
        return self.execution_runs.create_step(
            execution_run_id=run_id,
            step_index=0,
            step_type=step_type,
            step_name=step_name,
            status="queued",
            selector_hint=selector_hint,
            input_json=input_json,
            output_json={},
            error_message=None,
            screenshot_path=None,
            started_at=None,
            finished_at=None,
        )

    def _require_agent_run(self, run_id: int):
        run = self.execution_runs.get_run(run_id)
        if run is None or not str(run.run_type).startswith("agent."):
            raise AgentExecutionError(f"Unknown agent execution run '{run_id}'.")
        return run

    def _resolve_project_id(self, project_id: int | None) -> int:
        if project_id is not None:
            project = self.projects.get(project_id)
            if project is None:
                raise AgentExecutionError(f"Unknown project '{project_id}'.")
            return project.id
        projects = self.projects.list_all()
        if not projects:
            raise AgentExecutionError("At least one project is required before starting agent execution.")
        return projects[0].id

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)
