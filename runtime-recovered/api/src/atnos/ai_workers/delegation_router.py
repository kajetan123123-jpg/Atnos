from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from atnos.ai_workers.browser_ai_worker import BROWSER_WORKERS, BrowserAiWorker
from atnos.ai_workers.codex_worker import CODEX_WORKER_KEY, CodexWorker
from atnos.ai_workers.models import WorkerExecutionResult, WorkerStatusSnapshot
from atnos.ai_workers.ollama_worker import OllamaWorker
from atnos.ai_workers.execution_liveness import execution_liveness
from atnos.ai_workers.redaction import redact_payload, redact_text
from atnos.ai_workers.task_planner import TaskPlanner
from atnos.core.config import Settings
from atnos.db.models.enums import (
    ExecutionTraceKind,
    ExecutionTraceStatus,
    ProjectMemoryKind,
    ProjectMemoryPriority,
)
from atnos.db.models.project import Project, ProjectMemory
from atnos.repositories.ai_worker_repository import AiWorkerRepository
from atnos.repositories.execution_trace_repository import ExecutionTraceRepository
from atnos.services.runtime_settings_service import RuntimeSettingsService


HIGH_RISK_TOKENS = (
    "public upload",
    "publish",
    "opublikuj",
    "wrzuc publicznie",
    "trade",
    "live trading",
    "real trading",
    "place order",
    "buy",
    "sell",
    "kup",
    "sprzedaj",
    "payment",
    "checkout",
    "card",
    "karta",
    "oauth",
    "2fa",
    "totp",
    "haslo",
    "hasło",
    "password",
    "delete account",
    "zmien konto",
)

VALID_PRIVACY_MODES = {"strict_local", "allow_browser_ai", "allow_cloud_api"}
VALID_PRIORITIES = {"low", "normal", "high"}


@dataclass(frozen=True, slots=True)
class DelegateTaskInput:
    task_type: str
    prompt: str
    context: dict[str, Any]
    preferred_worker: str | None
    allowed_workers: tuple[str, ...]
    privacy_mode: str
    priority: str
    requested_by: str
    requires_approval_for_result: bool
    acceptance_criteria: tuple[str, ...] = ()
    project_id: int | None = None


@dataclass(frozen=True, slots=True)
class DelegatePlanInput(DelegateTaskInput):
    max_steps: int = 6


class AiDelegationRouter:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.repository = AiWorkerRepository(session)
        self.execution_traces = ExecutionTraceRepository(session)
        self.runtime_controls = RuntimeSettingsService(session, settings).snapshot()
        self.ollama = OllamaWorker(settings)
        self.codex = CodexWorker(settings)
        self.planner = TaskPlanner()

    async def status(self) -> dict[str, Any]:
        worker_statuses = [await self.ollama.status(), await self.codex.status()]
        worker_statuses.extend(
            BrowserAiWorker(
                self.settings,
                worker_key=worker_key,
                runtime_controls=self.runtime_controls,
            ).status()
            for worker_key in BROWSER_WORKERS
        )
        return {
            "mode": "supervised_high_trust",
            "workers": [item.to_dict() for item in worker_statuses],
            "guardrails": {
                "high_risk_actions_blocked": True,
                "redaction_enabled": True,
                "browser_ai_submit_policy": "supervised_handoff_only",
                "runtime_paused": bool(self.runtime_controls.pause_all_jobs),
                "emergency_stop": bool(self.runtime_controls.emergency_stop_enabled),
            },
        }

    async def probe(self, worker_key: str) -> dict[str, Any]:
        return (await self._worker_status(worker_key)).to_dict()

    async def delegate(self, payload: DelegateTaskInput) -> dict[str, Any]:
        self._validate_input(payload)
        project = self._validated_project(payload.project_id)
        redacted_prompt = redact_text(payload.prompt)
        redacted_context = redact_payload(payload.context)
        execution_contract = self._build_execution_contract(
            payload,
            objective=redacted_prompt,
            context=redacted_context if isinstance(redacted_context, dict) else {},
            max_iterations=1,
        )
        task = self.repository.create_task(
            task_type=payload.task_type,
            redacted_prompt=redacted_prompt,
            worker_key=payload.preferred_worker,
            privacy_mode=payload.privacy_mode,
            priority=payload.priority,
            requested_by=payload.requested_by,
            input_summary=_summary(redacted_prompt),
            payload_json={
                "context": redacted_context,
                "allowed_workers": list(payload.allowed_workers),
                "requires_approval_for_result": payload.requires_approval_for_result,
                "execution_contract": execution_contract,
                "project_id": project.id if project is not None else None,
            },
            approval_required=payload.requires_approval_for_result,
        )
        trace = self._create_delegation_trace(
            task=task,
            project=project,
            worker_key=payload.preferred_worker,
        )

        risk_reason = self._high_risk_reason(payload)
        if risk_reason:
            self.repository.block_task(
                task,
                reason=risk_reason,
                trace_id=trace.id if trace is not None else None,
            )
            self._finalize_delegation_trace(
                task=task,
                trace=trace,
                status=ExecutionTraceStatus.SKIPPED,
                output_summary=risk_reason,
            )
            return self._serialize_task(task)

        if payload.task_type.strip() == "capability_request":
            return await self._run_capability_request_task(task=task, trace=trace, project=project)

        from atnos.integrations.capability_workflow_service import is_capability_content_task

        if is_capability_content_task(
            payload.task_type,
            redacted_prompt,
            redacted_context if isinstance(redacted_context, dict) else {},
        ):
            return await self._run_capability_workflow_task(
                payload=payload,
                task=task,
                trace=trace,
                project=project,
                redacted_prompt=redacted_prompt,
                redacted_context=redacted_context if isinstance(redacted_context, dict) else {},
            )

        worker_key = await self._select_worker(payload)
        status = await self._worker_status(worker_key)
        if not status.ready and status.status != "handoff_only" and worker_key != "ollama":
            self.repository.block_task(
                task,
                reason=status.reason,
                trace_id=trace.id if trace is not None else None,
            )
            self._finalize_delegation_trace(
                task=task,
                trace=trace,
                status=ExecutionTraceStatus.SKIPPED,
                output_summary=status.reason,
            )
            return self._serialize_task(task)
        if not status.ready and worker_key == "ollama":
            reason = f"Ollama unavailable: {status.reason}"
            self.repository.block_task(
                task,
                reason=reason,
                trace_id=trace.id if trace is not None else None,
            )
            self._finalize_delegation_trace(
                task=task,
                trace=trace,
                status=ExecutionTraceStatus.SKIPPED,
                output_summary=reason,
            )
            return self._serialize_task(task)

        run = self.repository.start_task(task, worker_key=worker_key)
        if trace is not None:
            trace.provider_key = worker_key
        # Do not hold a SQLite write transaction while a local/cloud model runs.
        # Long LLM calls otherwise block concurrent reads/writes and surface as 500s.
        self.session.commit()
        try:
            result = await self._run_worker_persistently(
                worker_key=worker_key,
                task_type=payload.task_type,
                prompt=redacted_prompt,
                context=redacted_context if isinstance(redacted_context, dict) else {},
                timeout_s=int(execution_contract["max_duration_s"]),
            )
            if result.status == "blocked":
                self._block_started_task(
                    task,
                    run,
                    reason=result.blocked_reason or result.summary,
                    result_json=result.result,
                    trace_id=trace.id if trace is not None else None,
                )
            elif result.status == "pending_external":
                self.repository.mark_pending_external(
                    task,
                    run,
                    result_summary=result.summary,
                    result_json=result.result,
                )
            elif result.status == "failed":
                # A worker-reported failure must never be presented as a completed
                # task. This is especially important for isolated Codex proposals:
                # a failed independent test rejects the proposal.
                self.repository.fail_task(
                    task,
                    run,
                    error_message=result.summary,
                    verification_status=_verification_status_from_result(result.result, fallback="failed"),
                    result_json=result.result,
                )
            else:
                self.repository.complete_task(
                    task,
                    run,
                    result_summary=result.summary,
                    result_json=result.result,
                    verification_status=_verification_status_from_result(
                        result.result,
                        fallback=(
                            "awaiting_human_review"
                            if task.requires_review or task.approval_required
                            else "completed"
                        ),
                    ),
                    trace_id=trace.id if trace is not None else None,
                )
        except Exception as exc:
            self.repository.fail_task(
                task,
                run,
                error_message=f"{type(exc).__name__}: {exc}",
                trace_id=trace.id if trace is not None else None,
            )
        self._finalize_task_side_effects(task=task, trace=trace, project=project)
        return self._serialize_task(task)

    def review_codex_task(
        self,
        task_id: int,
        *,
        decision: str,
        reviewer: str,
        note: str,
    ) -> dict[str, Any] | None:
        """Zatwierdza lub odrzuca wynik, ale nigdy nie stosuje diffu."""
        task = self.repository.get_task(task_id)
        if task is None:
            return None
        if decision not in {"approve", "reject"}:
            raise ValueError("Codex review decision must be 'approve' or 'reject'.")
        result = self._reviewable_codex_result(task)
        if task.status == "rolled_back":
            raise ValueError("A rolled-back Codex proposal cannot be reviewed again.")
        if decision == "approve":
            if task.status == "approved":
                return self._serialize_task(task)
            if task.status != "completed":
                raise ValueError(f"Codex task cannot be approved from status '{task.status}'.")
            if result.get("execution_mode") != "isolated_worktree_patch_proposal":
                raise ValueError("Only an isolated Codex patch proposal can be approved.")
            verification = result.get("verification")
            if not isinstance(verification, dict) or verification.get("status") != "passed":
                raise ValueError("Codex proposal cannot be approved without passed independent verification.")
            if result.get("prompt_equals_result") is not False:
                raise ValueError("Codex proposal cannot be approved without proof that prompt and result differ.")
            if not result.get("diff_path") or not result.get("diff_sha256"):
                raise ValueError("Codex proposal cannot be approved without captured diff evidence.")
            phases = _lifecycle_phases(result)
            if "verified" not in phases or "result_received" not in phases:
                raise ValueError("Codex proposal lifecycle does not contain independent verification evidence.")
        elif task.status not in {"completed", "approved"}:
            raise ValueError(f"Codex task cannot be rejected from status '{task.status}'.")

        reviewed = self.repository.review_codex_result(
            task,
            decision=decision,
            reviewer=reviewer,
            note=note,
        )
        return self._serialize_task(reviewed)

    def rollback_codex_proposal(
        self,
        task_id: int,
        *,
        reviewer: str,
        reason: str,
    ) -> dict[str, Any] | None:
        """Unieważnia propozycję bez dotykania aktywnego worktree."""
        task = self.repository.get_task(task_id)
        if task is None:
            return None
        if task.status == "rolled_back":
            return self._serialize_task(task)
        result = self._reviewable_codex_result(task)
        if result.get("execution_mode") != "isolated_worktree_patch_proposal":
            raise ValueError("Only an isolated Codex patch proposal can be rolled back.")
        rollback = result.get("rollback")
        if not isinstance(rollback, dict) or rollback.get("main_worktree_modified") is not False:
            raise ValueError("Safe proposal rollback requires evidence that the main worktree was not modified.")
        rolled_back = self.repository.rollback_codex_proposal(
            task,
            reviewer=reviewer,
            reason=reason,
        )
        return self._serialize_task(rolled_back)

    @staticmethod
    def _reviewable_codex_result(task: Any) -> dict[str, Any]:
        if task.worker_key != CODEX_WORKER_KEY:
            raise ValueError("Review lifecycle is available only for Codex tasks.")
        if not isinstance(task.result_json, dict) or not task.result_json:
            raise ValueError("Codex task has no received result to review.")
        if task.result_json.get("proposal_state") == "rolled_back":
            raise ValueError("Codex proposal has already been rolled back.")
        return task.result_json

    def delegate_plan(self, payload: DelegatePlanInput) -> dict[str, Any]:
        self._validate_input(payload)
        project = self._validated_project(payload.project_id)
        redacted_prompt = redact_text(payload.prompt)
        redacted_context = redact_payload(payload.context)
        root_contract = self._build_execution_contract(
            payload,
            objective=redacted_prompt,
            context=redacted_context if isinstance(redacted_context, dict) else {},
            max_iterations=payload.max_steps,
        )
        root = self.repository.create_task(
            task_type=payload.task_type,
            redacted_prompt=redacted_prompt,
            worker_key="planner",
            privacy_mode=payload.privacy_mode,
            priority=payload.priority,
            requested_by=payload.requested_by,
            input_summary=_summary(redacted_prompt),
            payload_json={
                "context": redacted_context,
                "allowed_workers": list(payload.allowed_workers),
                "requires_approval_for_result": payload.requires_approval_for_result,
                "max_steps": payload.max_steps,
                "execution_contract": root_contract,
                "project_id": project.id if project is not None else None,
            },
            approval_required=payload.requires_approval_for_result,
            task_role="planner",
            verification_status="planned",
        )

        risk_reason = self._high_risk_reason(payload)
        if risk_reason:
            self.repository.block_task(root, reason=risk_reason)
            return self._serialize_task(root)

        plan = self.planner.build_plan(
            task_type=payload.task_type,
            prompt=redacted_prompt,
            context=redacted_context if isinstance(redacted_context, dict) else {},
            privacy_mode=payload.privacy_mode,
            preferred_worker=payload.preferred_worker,
            allowed_workers=payload.allowed_workers,
            max_steps=max(1, min(payload.max_steps, 50)),
        )
        self.repository.mark_planned(
            root,
            task_group=plan.task_group,
            result_summary=plan.summary,
            result_json=plan.to_dict(),
        )

        for subtask in plan.subtasks:
            child_contract = self._build_child_contract(root_contract, subtask)
            self.repository.create_task(
                task_type=subtask.task_type,
                redacted_prompt=subtask.prompt,
                worker_key=subtask.worker_key,
                privacy_mode=subtask.privacy_mode,
                priority=payload.priority,
                requested_by=payload.requested_by,
                input_summary=_summary(subtask.title),
                payload_json={
                    "context": redact_payload(subtask.context),
                    "plan_root_id": root.id,
                    "task_group": plan.task_group,
                    "execution_contract": child_contract,
                    "project_id": project.id if project is not None else None,
                },
                approval_required=subtask.requires_review,
                parent_task_id=root.id,
                task_group=plan.task_group,
                task_role=subtask.task_role,
                sequence_index=subtask.sequence_index,
                requires_review=subtask.requires_review,
                verification_status="queued_for_review" if subtask.requires_review else "planned",
            )
        planned = self._serialize_task(self.repository.get_task(root.id) or root)
        if not self._should_auto_execute_plan(payload):
            return planned

        # Project Command is synchronous and runs outside an asyncio loop. Execute
        # its safe local plan now instead of returning a plan that no worker ever
        # consumes. If the worker is not currently ready, keep the durable plan
        # queued rather than converting runtime unavailability into task failure.
        # Other callers retain the explicit plan/run separation.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self.session.commit()
            worker_status = asyncio.run(self._worker_status("ollama"))
            if not worker_status.ready:
                return planned
            executed = asyncio.run(self.run_safe_plan(root.id))
            return executed or planned

        self.repository.update_plan_progress(
            root,
            status="blocked",
            result_summary="Safe plan was not started because delegation was called from an active event loop.",
            result_json={
                "executed_child_ids": [],
                "remaining_child_ids": [child.id for child in root.children],
                "stop_reason": "active_event_loop_requires_async_run_safe_plan",
                "execution_policy": "safe_local_only",
            },
            verification_status="blocked_technical",
        )
        return self._serialize_task(self.repository.get_task(root.id) or root)

    def list_tasks(
        self,
        *,
        status: str | None = None,
        parent_task_id: int | None = None,
        roots_only: bool = False,
        query: str | None = None,
        conversation_id: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return [
            self._serialize_task(task)
            for task in self.repository.list_tasks(
                status=status,
                parent_task_id=parent_task_id,
                roots_only=roots_only,
                query=query,
                conversation_id=conversation_id,
                limit=limit,
            )
        ]

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        task = self.repository.get_task(task_id)
        return None if task is None else self._serialize_task(task)

    def cancel_task(self, task_id: int) -> dict[str, Any] | None:
        task = self.repository.get_task(task_id)
        if task is None:
            return None
        if task.status not in {"queued", "running"}:
            return self._serialize_task(task)
        return self._serialize_task(self.repository.cancel_task(task))

    async def run_task(self, task_id: int) -> dict[str, Any] | None:
        task = self.repository.get_task(task_id)
        if task is None:
            return None
        if isinstance(task.payload_json, dict) and task.payload_json.get('execution_scope') == 'hold_only':
            raise ValueError('Zadanie jest zapisane jako hold_only. Nie ma zgody na wykonanie; samo odczytanie lub uruchomienie ogólnego workera nie zmienia zakresu.')
        if task.children:
            raise ValueError("Planner/root tasks cannot run directly. Run one child task at a time.")
        if task.status == "completed":
            return self._serialize_task(task)
        stored_payload = task.payload_json if isinstance(task.payload_json, dict) else {}
        retry_text_review = (task.status == 'blocked' and not task.runs
            and str(task.blocked_reason or '').startswith('AI delegation blocked a high-risk action.')
            and self._is_text_only_review(task.task_type, task.worker_key, stored_payload.get('allowed_workers'),
                task.privacy_mode, stored_payload.get('requires_approval_for_result'))
            and self._stored_task_risk_reason(task) is None)
        if task.status not in {"queued", "planned", "failed", "incomplete"} and not retry_text_review:
            raise ValueError(f"Task {task.id} cannot be run from status '{task.status}'.")

        payload = task.payload_json if isinstance(task.payload_json, dict) else {}
        raw_project_id = payload.get("project_id")
        project_id = int(raw_project_id) if isinstance(raw_project_id, int) and not isinstance(raw_project_id, bool) else None
        project = self._validated_project(project_id)
        trace = self._create_delegation_trace(
            task=task,
            project=project,
            worker_key=task.worker_key,
        )
        risk_reason = self._stored_task_risk_reason(task)
        if risk_reason:
            self.repository.block_task(
                task,
                reason=risk_reason,
                trace_id=trace.id if trace is not None else None,
            )
            self._finalize_delegation_trace(
                task=task,
                trace=trace,
                status=ExecutionTraceStatus.SKIPPED,
                output_summary=risk_reason,
            )
            return self._serialize_task(task)

        worker_key = task.worker_key or "ollama"
        if worker_key == "planner":
            worker_key = "ollama"
        if worker_key == "capability_runtime" or task.task_type == "capability_workflow":
            return await self._complete_capability_workflow_child(task=task, trace=trace, project=project)
        if worker_key == 'capability_router' or task.task_type == 'capability_request':
            return await self._run_capability_request_task(task=task, trace=trace, project=project)

        status = await self._worker_status(worker_key)
        if not status.ready:
            reason = f"{status.label or worker_key} unavailable: {status.reason}"
            self.repository.block_task(
                task,
                reason=reason,
                trace_id=trace.id if trace is not None else None,
            )
            self._finalize_delegation_trace(
                task=task,
                trace=trace,
                status=ExecutionTraceStatus.SKIPPED,
                output_summary=reason,
            )
            return self._serialize_task(task)

        context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
        execution_contract = (
            payload.get("execution_contract")
            if isinstance(payload.get("execution_contract"), dict)
            else {}
        )
        worker_context = dict(context)
        if execution_contract:
            worker_context["execution_contract"] = execution_contract
        run = self.repository.start_task(task, worker_key=worker_key)
        # Release the DB before slow worker execution; persist the final result afterwards.
        self.session.commit()
        try:
            result = await self._run_worker_persistently(
                worker_key=worker_key,
                task_type=task.task_type,
                prompt=task.redacted_prompt,
                context=worker_context,
                timeout_s=self._contract_timeout_s(execution_contract),
            )
            if result.status == "blocked":
                self._block_started_task(
                    task,
                    run,
                    reason=result.blocked_reason or result.summary,
                    result_json=result.result,
                    trace_id=trace.id if trace is not None else None,
                )
            elif result.status == "pending_external":
                self.repository.mark_pending_external(
                    task,
                    run,
                    result_summary=result.summary,
                    result_json=result.result,
                )
            elif result.status == "failed":
                self.repository.fail_task(
                    task,
                    run,
                    error_message=result.summary,
                    verification_status=_verification_status_from_result(result.result, fallback="failed"),
                    result_json=result.result,
                    trace_id=trace.id if trace is not None else None,
                )
            else:
                self.repository.complete_task(
                    task,
                    run,
                    result_summary=result.summary,
                    result_json=result.result,
                    verification_status=_verification_status_from_result(
                        result.result,
                        fallback=(
                            "awaiting_human_review"
                            if task.requires_review or task.approval_required
                            else "completed"
                        ),
                    ),
                    trace_id=trace.id if trace is not None else None,
                )
        except Exception as exc:
            self.repository.fail_task(
                task,
                run,
                error_message=f"{type(exc).__name__}: {exc}",
                trace_id=trace.id if trace is not None else None,
            )
        refreshed = self.repository.get_task(task.id) or task
        self._finalize_task_side_effects(task=refreshed, trace=trace, project=project)
        return self._serialize_task(refreshed)

    async def _run_worker_persistently(
        self,
        *,
        worker_key: str,
        task_type: str,
        prompt: str,
        context: dict[str, Any],
        timeout_s: int,
    ) -> WorkerExecutionResult:
        """Kończy rozpoczęty run nawet po rozłączeniu klienta HTTP.

        ``asyncio.to_thread`` używane przez Codexa nie zatrzymuje procesu po
        anulowaniu requestu. Bez osłony coroutine traci jednak wynik i zostawia
        rekord ``running`` na zawsze. Shield zachowuje wykonanie, a po anulowaniu
        requestu czekamy na już uruchomionego workera, aby repozytorium mogło
        zapisać wynik albo jawny błąd.
        """

        if worker_key == "ollama":
            operation = self.ollama.run(task_type=task_type, prompt=prompt, context=context)
        elif worker_key == CODEX_WORKER_KEY:
            operation = self.codex.run(task_type=task_type, prompt=prompt, context=context)
        else:
            operation = BrowserAiWorker(
                self.settings,
                worker_key=worker_key,
                runtime_controls=self.runtime_controls,
            ).run(task_type=task_type, prompt=prompt, context=context)

        worker_task = asyncio.create_task(operation)
        try:
            result = await asyncio.wait_for(asyncio.shield(worker_task), timeout=max(1, timeout_s))
            return self._validate_worker_result(
                result,
                worker_key=worker_key,
                prompt=prompt,
            )
        except TimeoutError:
            worker_task.cancel()
            try:
                await worker_task
            except (asyncio.CancelledError, Exception):
                pass
            return WorkerExecutionResult(
                worker_key=worker_key,
                status="failed",
                summary=f"Worker timed out after {max(1, timeout_s)} seconds.",
                result={
                    "lifecycle": [
                        {
                            "phase": "timeout",
                            "at": datetime.now(UTC).isoformat(),
                            "evidence": {"timeout_s": max(1, timeout_s)},
                        }
                    ],
                    "verification": {
                        "status": "failed",
                        "reason": "worker_timeout",
                    },
                },
            )
        except asyncio.CancelledError:
            if worker_task.cancelled():
                raise
            result = await worker_task
            return self._validate_worker_result(
                result,
                worker_key=worker_key,
                prompt=prompt,
            )

    async def run_safe_plan(self, task_id: int) -> dict[str, Any] | None:
        """Wykonuje tylko kolejne bezpieczne, lokalne kroki planu.

        Browser handoff i kazda akcja podwyzszonego ryzyka sa granica
        wykonania. Lokalny krok review jest wykonywany, a dopiero jego odebrany
        wynik trafia do review czlowieka.
        """
        root = self.repository.get_task(task_id)
        if root is None:
            return None
        if not root.children:
            raise ValueError("Task is not a delegation plan.")
        if root.status not in {"planned", "awaiting_review", "running", "incomplete"}:
            return self._serialize_task(root)

        risk_reason = self._stored_task_risk_reason(root)
        if risk_reason:
            return self._serialize_task(self.repository.block_task(root, reason=risk_reason))

        executed: list[int] = []
        stop_reason: str | None = None
        stop_status: str | None = None
        for child in sorted(root.children, key=lambda item: item.sequence_index):
            if child.status == "completed":
                executed.append(child.id)
                continue
            if child.worker_key == "capability_runtime" or child.task_type == "capability_workflow":
                self._attach_prior_results(root, child)
                result = await self.run_task(child.id)
                if result is None:
                    stop_reason = f"Krok #{child.id} zniknal z kolejki."
                    stop_status = "failed"
                    break
                if result["status"] not in {"completed", "awaiting_review"}:
                    # A usable partial brief can feed the next safe review, but
                    # must never become a completed child/plan implicitly.
                    child_result = result.get("result_json") if isinstance(result.get("result_json"), dict) else {}
                    if (result['status'] == 'incomplete' and child_result.get('workflow_status') == 'PARTIAL'
                            and child_result.get('production_status') == 'PRODUCTION_BRIEF_READY'):
                        executed.append(child.id)
                        continue
                    stop_reason = (
                        f"Krok #{child.id} zakonczyl sie statusem {result['status']}: "
                        f"{result.get('blocked_reason') or result.get('error_message') or 'brak szczegolow'}"
                    )
                    stop_status = {
                        "blocked": "blocked",
                        "pending_external": "pending_external",
                        "incomplete": "incomplete",
                    }.get(str(result["status"]), "failed")
                    break
                executed.append(child.id)
                continue
            if child.worker_key != "ollama" or child.privacy_mode != "strict_local":
                stop_reason = (
                    f"Krok #{child.id} wymaga workera '{child.worker_key or 'auto'}' "
                    "lub trybu spoza strict_local."
                )
                stop_status = "pending_external"
                break

            self._attach_prior_results(root, child)
            result = await self.run_task(child.id)
            if result is None:
                stop_reason = f"Krok #{child.id} zniknal z kolejki."
                stop_status = "failed"
                break
            if result["status"] != "completed":
                stop_reason = (
                    f"Krok #{child.id} zakonczyl sie statusem {result['status']}: "
                    f"{result.get('blocked_reason') or result.get('error_message') or 'brak szczegolow'}"
                )
                stop_status = {
                    "blocked": "blocked",
                    "pending_external": "pending_external",
                }.get(str(result["status"]), "failed")
                break
            executed.append(child.id)

        root = self.repository.get_task(task_id) or root
        remaining = [child for child in root.children if child.status != "completed"]
        partial_children = [child.id for child in root.children if child.status == 'incomplete'
            or (isinstance(child.result_json, dict) and child.result_json.get('workflow_status') == 'PARTIAL')]
        review_required = bool(root.approval_required) or any(
            child.requires_review or child.approval_required
            for child in root.children
        )
        if stop_reason:
            plan_status = stop_status or "failed"
            verification_status = {
                "blocked": "blocked",
                "pending_external": "pending_external",
                "incomplete": "incomplete",
            }.get(plan_status, "failed")
        elif review_required:
            plan_status = "awaiting_review"
            verification_status = "incomplete" if partial_children else "awaiting_human_review"
        elif partial_children:
            plan_status = 'incomplete'
            verification_status = 'incomplete'
        else:
            plan_status = "completed"
            verification_status = "completed"
        summary = (
            f"Automatycznie wykonano lokalne kroki: {', '.join(f'#{item}' for item in executed) or 'brak'}. "
            + (
                stop_reason
                or (
                    "Wszystkie lokalne kroki wraz z review zostaly wykonane; wynik czeka na review czlowieka."
                    if review_required
                    else "Wszystkie bezpieczne kroki planu zostaly wykonane."
                )
            )
        )
        if partial_children:
            summary = (f"Niepełne wyniki kroków: {', '.join(f'#{item}' for item in partial_children)}. "
                       "Plan nie jest w pełni ukończony. " + (stop_reason or
                       ('Wyniki czekają na review człowieka.' if review_required else 'Sprawdź brakujące dane i ograniczenia.')))
        self.repository.update_plan_progress(
            root,
            status=plan_status,
            result_summary=summary,
            result_json={
                "executed_child_ids": executed,
                "remaining_child_ids": [child.id for child in remaining],
                "partial_child_ids": partial_children,
                "stop_reason": stop_reason,
                "execution_policy": "safe_local_only",
            },
            verification_status=verification_status,
        )
        return self._serialize_task(self.repository.get_task(task_id) or root)

    @staticmethod
    def _should_auto_execute_plan(payload: DelegatePlanInput) -> bool:
        """Automatyzuje bezpieczny lokalny plan z Project Command lub capability workflow."""
        if payload.task_type in {"capability_workflow", "content_capability_review", "production_brief_review"}:
            return True
        context = payload.context if isinstance(payload.context, dict) else {}
        if context.get("run_capability_workflow") is True:
            return True
        from atnos.integrations.capability_workflow_service import is_capability_content_task

        if is_capability_content_task(payload.task_type, payload.prompt, context):
            return True
        return (
            payload.requested_by == "project_command_chat"
            and payload.privacy_mode == "strict_local"
            and set(payload.allowed_workers or ("ollama",)) <= {"ollama"}
        )

    async def _run_capability_workflow_task(
        self,
        *,
        payload: DelegateTaskInput,
        task: Any,
        trace: Any,
        project: Any,
        redacted_prompt: str,
        redacted_context: dict[str, Any],
    ) -> dict[str, Any]:
        run = self.repository.start_task(task, worker_key="capability_runtime")
        self.session.commit()
        try:
            result = self._execute_capability_workflow(
                prompt=redacted_prompt,
                task_type=payload.task_type,
                context=redacted_context,
                project=project,
            )
            summary = (
                f"Capability workflow {result.get('workflow_status')}: "
                f"{len(result.get('capabilities_used') or [])} capabilities"
            )
            trace_status = self._persist_capability_result(task=task, run=run, trace=trace,
                result=result, status_key='workflow_status', success_status='COMPLETED', summary=summary)
            self._finalize_delegation_trace(
                task=task,
                trace=trace,
                status=trace_status,
                output_summary=summary,
            )
        except Exception as exc:
            self.repository.fail_task(
                task,
                run,
                error_message=f"{type(exc).__name__}: {exc}",
                trace_id=trace.id if trace is not None else None,
            )
            self._finalize_delegation_trace(
                task=task,
                trace=trace,
                status=ExecutionTraceStatus.FAILED,
                output_summary=str(exc),
            )
        return self._serialize_task(task)

    async def _complete_capability_workflow_child(
        self,
        *,
        task: Any,
        trace: Any,
        project: Any,
    ) -> dict[str, Any]:
        payload = task.payload_json if isinstance(task.payload_json, dict) else {}
        context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
        run = self.repository.start_task(task, worker_key="capability_runtime")
        self.session.commit()
        try:
            result = self._execute_capability_workflow(
                prompt=task.redacted_prompt,
                task_type=task.task_type,
                context=context,
                project=project,
            )
            summary = (
                f"Capability workflow {result.get('workflow_status')}: "
                f"{len(result.get('capabilities_used') or [])} capabilities"
            )
            trace_status = self._persist_capability_result(task=task, run=run, trace=trace,
                result=result, status_key='workflow_status', success_status='COMPLETED', summary=summary)
            self._finalize_delegation_trace(
                task=task,
                trace=trace,
                status=trace_status,
                output_summary=summary,
            )
        except Exception as exc:
            self.repository.fail_task(
                task,
                run,
                error_message=f"{type(exc).__name__}: {exc}",
                trace_id=trace.id if trace is not None else None,
            )
        return self._serialize_task(task)

    async def _run_capability_request_task(self, *, task, trace, project):
        """Same provider route for first dispatch and explicit retry of its task."""
        from atnos.integrations.capability_router import route_data_need
        payload = task.payload_json if isinstance(task.payload_json, dict) else {}
        context = payload.get('context') if isinstance(payload.get('context'), dict) else {}
        run = self.repository.start_task(task, worker_key='capability_router')
        self.session.commit()
        try:
            routed = route_data_need(
                intent=str(context.get('intent') or task.redacted_prompt),
                capability=context.get('capability') if isinstance(context.get('capability'), str) else None,
                parameters=context.get('parameters') if isinstance(context.get('parameters'), dict) else {},
                project_id=str(project.id) if project is not None else None,
                fallback_allowed=context.get('fallback_allowed', True) is not False)
            trace_status = self._persist_capability_result(task=task, run=run, trace=trace,
                result=routed, status_key='status', success_status='SUCCESS',
                summary=str(routed.get('detail') or routed.get('status') or 'Unknown capability outcome'))
        except Exception as exc:
            self.repository.fail_task(task, run, error_message=f'{type(exc).__name__}: {redact_text(str(exc))}',
                trace_id=trace.id if trace is not None else None)
            trace_status = ExecutionTraceStatus.FAILED
        self._finalize_delegation_trace(task=task, trace=trace, status=trace_status,
            output_summary=task.result_summary or task.blocked_reason or task.error_message or '')
        return self._serialize_task(task)

    def _persist_capability_result(self, *, task, run, trace, result, status_key, success_status, summary):
        """Preserve the capability's actual outcome in both task and run."""
        status = result.get(status_key)
        trace_id = trace.id if trace is not None else None
        provenance = result.get('provenance') if isinstance(result.get('provenance'), dict) else {}
        public_data_received = (status_key == 'status' and status == 'HEALTHY'
            and isinstance(result.get('value'), (dict, list)) and bool(result['value'])
            and bool(provenance.get('source')) and bool(provenance.get('fetched_at'))
            and bool(result.get('provider')) and bool(result.get('capability')))
        if status == success_status or public_data_received:
            self.repository.complete_task(task, run, result_summary=summary, result_json=result,
                verification_status='awaiting_human_review' if task.requires_review or task.approval_required else 'completed',
                trace_id=trace_id)
            return ExecutionTraceStatus.COMPLETED
        if status == 'PARTIAL':
            self.repository.complete_task(task, run, result_summary=summary, result_json=result,
                                          verification_status='incomplete', trace_id=trace_id)
            # Same transaction: persist a terminal partial result, never emit
            # completed to the caller or a completed execution trace.
            task.status = 'incomplete'
            run.status = 'incomplete'
            self.session.flush()
            return ExecutionTraceStatus.SKIPPED
        if status in {'BLOCKED', 'REQUIRES_SETUP', 'REQUIRES_DATA', 'REQUIRES_APPROVAL',
                      'REQUIRES_BROWSER', 'NOT_INSTALLED', 'ACCESS_DENIED', 'NO_CAPABILITY',
                      'EMPTY_CONTENT', 'DEGRADED'}:
            reason = str(result.get('blocked_reason') or result.get('detail') or
                         f'Capability {status}: zadanie nie zostało w pełni wykonane.')
            self._block_started_task(task, run, reason=reason, result_json=result, trace_id=trace_id)
            task.result_json = result
            task.result_summary = summary
            task.completed_at = run.completed_at
            task.verification_status = 'blocked'
            return ExecutionTraceStatus.SKIPPED
        self.repository.fail_task(task, run,
            error_message=f'Capability outcome is not successful: {status or "MISSING_STATUS"}',
            result_json=result, trace_id=trace_id)
        return ExecutionTraceStatus.FAILED

    def _execute_capability_workflow(
        self,
        *,
        prompt: str,
        task_type: str,
        context: dict[str, Any],
        project: Any,
    ) -> dict[str, Any]:
        from atnos.integrations.capability_workflow_service import CapabilityWorkflowService

        project_key = None
        if isinstance(context.get("project_key"), str):
            project_key = context["project_key"]
        elif isinstance(context.get("channel"), str):
            project_key = context["channel"]
        elif project is not None:
            name = str(getattr(project, "name", "") or "").casefold()
            if "wealth" in name:
                project_key = "wealth_blueprint"
            elif "wonder" in name or "crew" in name:
                project_key = "wonder_crew"
            elif "dark" in name or "chronic" in name:
                project_key = "dark_chronicles"
        inject = context.get("inject_capability_status")
        if not isinstance(inject, dict):
            inject = None
        integrations_root = None
        if self.settings is not None and getattr(self.settings, "data_dir", None) is not None:
            integrations_root = Path(self.settings.data_dir) / "integrations"
        service = CapabilityWorkflowService(
            settings=self.settings,
            session=self.session,
            root=integrations_root,
        )
        return service.run(
            prompt=prompt,
            task_type=task_type,
            context=context,
            project_key=project_key or "wealth_blueprint",
            inject_status=inject,
        )

    def _attach_prior_results(self, root: Any, child: Any) -> None:
        """Przekazuje kolejnemu workerowi odebrane wyniki wcześniejszych kroków."""
        prior_results = [
            {
                "task_id": previous.id,
                "task_role": previous.task_role,
                "summary": previous.result_summary,
                "result": redact_payload(previous.result_json or {}),
            }
            for previous in sorted(root.children, key=lambda item: item.sequence_index)
            if previous.sequence_index < child.sequence_index and previous.status == "completed"
        ]
        if not prior_results:
            return
        payload = dict(child.payload_json or {})
        context = dict(payload.get("context") or {})
        context["prior_results"] = prior_results
        payload["context"] = context
        child.payload_json = payload
        self.session.flush()

    def _validate_worker_result(
        self,
        result: WorkerExecutionResult,
        *,
        worker_key: str,
        prompt: str,
    ) -> WorkerExecutionResult:
        """Waliduje transport i kontrakt wyniku bez udawania oceny merytorycznej."""
        if result.worker_key != worker_key:
            return self._invalid_worker_result(
                worker_key,
                result,
                reason=f"worker_identity_mismatch:{result.worker_key}",
            )
        if result.status not in {"completed", "failed", "blocked", "pending_external"}:
            return self._invalid_worker_result(
                worker_key,
                result,
                reason=f"unsupported_worker_status:{result.status}",
            )
        if result.status != "completed":
            return result
        if not result.summary.strip() or not isinstance(result.result, dict) or not result.result:
            return self._invalid_worker_result(worker_key, result, reason="empty_worker_result")

        candidate = str(
            result.result.get("content")
            or result.result.get("last_message")
            or result.summary
        ).strip()
        if not candidate:
            return self._invalid_worker_result(worker_key, result, reason="empty_worker_content")
        if " ".join(candidate.split()).casefold() == " ".join(prompt.split()).casefold():
            return self._invalid_worker_result(worker_key, result, reason="prompt_reused_as_result")

        payload = dict(result.result)
        payload["output_validation"] = {
            "status": "passed",
            "scope": "transport_and_result_contract",
            "checks": [
                "worker_identity_matches",
                "supported_terminal_status",
                "non_empty_result",
                "prompt_differs_from_result",
            ],
            "validated_at": datetime.now(UTC).isoformat(),
            "semantic_correctness_verified": False,
        }
        if not isinstance(payload.get("lifecycle"), list):
            payload["lifecycle"] = [
                {
                    "phase": "result_received",
                    "at": datetime.now(UTC).isoformat(),
                    "evidence": {"worker_key": worker_key},
                },
                {
                    "phase": "output_validated",
                    "at": datetime.now(UTC).isoformat(),
                    "evidence": {"scope": "transport_and_result_contract"},
                },
            ]
        return WorkerExecutionResult(
            worker_key=result.worker_key,
            status=result.status,
            summary=result.summary,
            result=payload,
            blocked_reason=result.blocked_reason,
        )

    @staticmethod
    def _invalid_worker_result(
        worker_key: str,
        result: WorkerExecutionResult,
        *,
        reason: str,
    ) -> WorkerExecutionResult:
        payload = dict(result.result or {})
        payload["output_validation"] = {
            "status": "failed",
            "scope": "transport_and_result_contract",
            "reason": reason,
            "validated_at": datetime.now(UTC).isoformat(),
        }
        payload["verification"] = {"status": "failed", "reason": reason}
        return WorkerExecutionResult(
            worker_key=worker_key,
            status="failed",
            summary=f"Worker result failed validation: {reason}.",
            result=payload,
        )

    def _block_started_task(
        self,
        task: Any,
        run: Any,
        *,
        reason: str,
        result_json: dict[str, Any],
        trace_id: int | None = None,
    ) -> None:
        now = datetime.now(UTC)
        self.repository.block_task(task, reason=reason, trace_id=trace_id)
        run.status = "blocked"
        run.completed_at = now
        run.result_summary = reason
        run.result_json = result_json
        run.error_message = reason
        if run.started_at is not None:
            run.duration_ms = max(0, int((now - run.started_at).total_seconds() * 1_000))
        self.session.flush()

    def _validated_project(self, project_id: int | None) -> Project | None:
        if project_id is None:
            return None
        if project_id <= 0:
            raise ValueError("project_id must be a positive integer.")
        project = self.session.get(Project, project_id)
        if project is None:
            raise ValueError(f"Project {project_id} does not exist.")
        return project

    def _create_delegation_trace(
        self,
        *,
        task: Any,
        project: Project | None,
        worker_key: str | None,
    ) -> Any | None:
        if project is None:
            return None
        trace = self.execution_traces.create(
            project_id=project.id,
            trace_kind=ExecutionTraceKind.PROVIDER,
            status=ExecutionTraceStatus.RUNNING,
            step_name="ai_worker_delegation",
            provider_key=worker_key or "delegation_router",
            input_summary=task.input_summary,
            metadata_json={
                "task_id": task.id,
                "task_type": task.task_type,
                "privacy_mode": task.privacy_mode,
            },
        )
        task.trace_id = trace.id
        self.session.flush()
        return trace

    def _finalize_task_side_effects(
        self,
        *,
        task: Any,
        trace: Any | None,
        project: Project | None,
    ) -> None:
        trace_status = {
            "completed": ExecutionTraceStatus.COMPLETED,
            "failed": ExecutionTraceStatus.FAILED,
            "blocked": ExecutionTraceStatus.SKIPPED,
            "pending_external": ExecutionTraceStatus.PENDING,
        }.get(str(task.status), ExecutionTraceStatus.PENDING)
        summary = task.result_summary or task.error_message or task.blocked_reason or str(task.status)
        self._finalize_delegation_trace(
            task=task,
            trace=trace,
            status=trace_status,
            output_summary=summary,
        )
        if project is None or task.status != "completed":
            return
        result_summary = redact_text(str(task.result_summary or "")).strip()
        if not result_summary:
            return
        self.session.add(
            ProjectMemory(
                project_id=project.id,
                memory_kind=ProjectMemoryKind.TASK,
                priority=ProjectMemoryPriority.MEDIUM,
                content=(
                    f"AI delegation #{task.id} completed by {task.worker_key}: "
                    f"{result_summary[:2_000]}"
                ),
                confidence_score=0.7,
                is_pinned=False,
                is_archived=False,
            )
        )
        self.session.flush()

    def _finalize_delegation_trace(
        self,
        *,
        task: Any,
        trace: Any | None,
        status: ExecutionTraceStatus,
        output_summary: str,
    ) -> None:
        if trace is None:
            return
        trace.status = status
        trace.output_summary = redact_text(str(output_summary or ""))[:2_000]
        trace.error_message = (
            redact_text(str(task.error_message or task.blocked_reason or ""))[:2_000]
            if status in {ExecutionTraceStatus.FAILED, ExecutionTraceStatus.SKIPPED}
            else None
        )
        trace.metadata_json = {
            "task_id": task.id,
            "task_status": task.status,
            "verification_status": task.verification_status,
            "worker_key": task.worker_key,
        }
        self.session.flush()

    def _contract_timeout_s(self, contract: dict[str, Any]) -> int:
        value = contract.get("max_duration_s", self.settings.ollama_timeout_s)
        try:
            return max(1, min(600, int(value)))
        except (TypeError, ValueError):
            return max(1, min(600, int(self.settings.ollama_timeout_s)))

    async def _select_worker(self, payload: DelegateTaskInput) -> str:
        allowed = set(payload.allowed_workers or ("ollama", CODEX_WORKER_KEY, *BROWSER_WORKERS.keys()))
        if payload.preferred_worker:
            if payload.preferred_worker not in allowed:
                raise ValueError(f"Preferred worker '{payload.preferred_worker}' is not allowed.")
            if payload.preferred_worker == CODEX_WORKER_KEY and payload.privacy_mode != "allow_cloud_api":
                raise ValueError("Codex worker requires privacy_mode='allow_cloud_api'.")
            return payload.preferred_worker
        if payload.privacy_mode == "strict_local" or "ollama" in allowed:
            return "ollama"
        for worker_key in BROWSER_WORKERS:
            if worker_key in allowed:
                return worker_key
        return "ollama"

    async def _worker_status(self, worker_key: str) -> WorkerStatusSnapshot:
        if worker_key == "ollama":
            return await self.ollama.status()
        if worker_key == CODEX_WORKER_KEY:
            return await self.codex.status()
        if worker_key in BROWSER_WORKERS:
            return BrowserAiWorker(
                self.settings,
                worker_key=worker_key,
                runtime_controls=self.runtime_controls,
            ).status()
        return WorkerStatusSnapshot(
            worker_key=worker_key,
            label=worker_key,
            status="not_configured",
            ready=False,
            reason=f"Unknown AI worker: {worker_key}",
        )

    @staticmethod
    def _validate_input(payload: DelegateTaskInput) -> None:
        if payload.task_type == "coding" and (payload.privacy_mode == "strict_local" or payload.preferred_worker in {None, "ollama"}):
            if payload.privacy_mode != "strict_local" or payload.preferred_worker not in {None, "ollama"} or payload.allowed_workers != ("ollama",):
                raise ValueError("Local coding proposals require strict_local and allowed_workers=['ollama'].")
            if not payload.requires_approval_for_result:
                raise ValueError("Local coding proposals require independent result review.")
            if len(payload.prompt) > 4000:
                raise ValueError("Coding task exceeds 4000 characters; split it into a smaller task.")
        if payload.privacy_mode not in VALID_PRIVACY_MODES:
            raise ValueError(f"Invalid privacy_mode: {payload.privacy_mode}")
        if payload.priority not in VALID_PRIORITIES:
            raise ValueError(f"Invalid priority: {payload.priority}")
        if not payload.task_type.strip():
            raise ValueError("task_type cannot be empty.")
        if not payload.prompt.strip():
            raise ValueError("prompt cannot be empty.")
        if len(payload.acceptance_criteria) > 12:
            raise ValueError("acceptance_criteria cannot contain more than 12 items.")
        if any(not item.strip() for item in payload.acceptance_criteria):
            raise ValueError("acceptance_criteria cannot contain empty items.")

    @staticmethod
    def _high_risk_reason(payload: DelegateTaskInput) -> str | None:
        # This worker only returns text and cannot execute tools. In a closed,
        # human-reviewed local review, supplied material is data, not an action
        # request (e.g. a drawn 'card' in a storyboard). The actual prompt is
        # still screened; all other tasks retain the full context risk check.
        material = '' if AiDelegationRouter._is_text_only_review(payload.task_type, payload.preferred_worker,
            payload.allowed_workers, payload.privacy_mode, payload.requires_approval_for_result) else payload.context
        text = f"{payload.task_type}\n{payload.prompt}\n{material}"
        return AiDelegationRouter._high_risk_reason_for_text(text)

    @staticmethod
    def _is_text_only_review(task_type, worker_key, allowed_workers, privacy_mode, requires_review) -> bool:
        return (task_type == 'review' and worker_key == 'ollama' and privacy_mode == 'strict_local'
                and requires_review is True and tuple(allowed_workers or ()) == ('ollama',))

    @staticmethod
    def _high_risk_reason_for_text(text: str) -> str | None:
        normalized = text.casefold()
        if any(token in normalized for token in HIGH_RISK_TOKENS):
            return (
                "AI delegation blocked a high-risk action. Use action_broker/trusted_auto "
                "approval flow for publishing, payments, trading, OAuth, 2FA, credentials or account changes."
            )
        return None

    @staticmethod
    def _stored_task_risk_reason(task: Any) -> str | None:
        """Sprawdza dane zlecenia, nie opis naszych własnych guardraili."""
        payload = task.payload_json if isinstance(task.payload_json, dict) else {}
        context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
        if AiDelegationRouter._is_text_only_review(task.task_type, task.worker_key, payload.get('allowed_workers'),
                task.privacy_mode, payload.get('requires_approval_for_result')):
            context = {}
        return AiDelegationRouter._high_risk_reason_for_text(
            f"{task.task_type}\n{task.redacted_prompt}\n{context}"
        )

    def _build_execution_contract(
        self,
        payload: DelegateTaskInput,
        *,
        objective: str,
        context: dict[str, Any],
        max_iterations: int,
    ) -> dict[str, Any]:
        """Buduje audytowalny kontrakt bez deklarowania wyniku pracy workera."""
        allowed_workers = list(payload.allowed_workers or ("ollama", CODEX_WORKER_KEY, *BROWSER_WORKERS.keys()))
        scope = {
            key: context[key]
            for key in ("project_id", "project_name", "project_type", "repository_root", "task_scope", "source_paths")
            if key in context
        }
        acceptance = [item.strip() for item in payload.acceptance_criteria if item.strip()]
        if not acceptance:
            acceptance = self._default_acceptance_criteria(payload.task_type)
        return {
            "objective": _summary(objective, limit=1_000),
            "task_type": payload.task_type,
            "scope": scope,
            "acceptance_criteria": acceptance,
            "allowed_workers": allowed_workers,
            "allowed_tools": self._allowed_tools(allowed_workers),
            "forbidden_actions": [
                "public publishing or uploads",
                "payments, purchases or card use",
                "OAuth consent, 2FA or credential handling",
                "live trading or order placement",
                "destructive account or filesystem operations",
            ],
            "required_evidence": [
                "redacted task input and context",
                "worker run record with timestamps and worker identity",
                "result payload or explicit blocked/error status",
                "source citations for claims based on external or local research",
            ],
            "max_duration_s": max(15, min(600, int(self.settings.ollama_timeout_s))),
            "max_iterations": max(1, min(max_iterations, 12)),
            "required_result_format": "exact replacement edits with source/candidate hashes; independent review and tests pending" if payload.task_type == "coding" and payload.privacy_mode == "strict_local" else "concise summary, evidence/citations, limitations, recommended next step",
            "rollback": "No mutation is authorized by this delegation. Code changes require a separate approved action with a rollback snapshot.",
            "verification": "Worker completion is not proof of correctness; validate required evidence before marking the result verified.",
        }

    @staticmethod
    def _build_child_contract(root_contract: dict[str, Any], subtask: Any) -> dict[str, Any]:
        contract = dict(root_contract)
        contract["objective"] = _summary(str(subtask.title), limit=1_000)
        contract["task_type"] = str(subtask.task_type)
        contract["task_role"] = str(subtask.task_role)
        contract["allowed_workers"] = [str(subtask.worker_key)]
        contract["allowed_tools"] = AiDelegationRouter._allowed_tools([str(subtask.worker_key)])
        contract["max_iterations"] = 1
        contract["requires_human_review"] = bool(subtask.requires_review)
        return contract

    @staticmethod
    def _allowed_tools(worker_keys: list[str]) -> list[str]:
        tools: list[str] = []
        if "ollama" in worker_keys:
            tools.append("ollama_local_api")
        if CODEX_WORKER_KEY in worker_keys:
            tools.append("codex_sanitized_cli_or_isolated_patch_proposal")
        if any(worker in BROWSER_WORKERS for worker in worker_keys):
            tools.append("supervised_browser_handoff")
        if "capability_runtime" in worker_keys:
            tools.append("capability_runtime")
        return tools or ["no_execution_tool_selected"]

    @staticmethod
    def _default_acceptance_criteria(task_type: str) -> list[str]:
        criteria = [
            "The result directly addresses the declared objective.",
            "The result lists material limitations and does not claim unverified completion.",
            "The result proposes one concrete next step.",
        ]
        normalized = task_type.casefold()
        if any(token in normalized for token in ("research", "review", "analysis", "market", "content")):
            criteria.insert(1, "Research claims include source citations or explicitly state that no source was available.")
        if any(token in normalized for token in ("code", "engineering", "runtime", "test")):
            criteria.insert(1, "The result identifies affected files or explicitly states that no code inspection occurred.")
        return criteria

    @staticmethod
    def _serialize_task(task) -> dict[str, Any]:
        result_json = task.result_json if isinstance(task.result_json, dict) else {}
        capabilities_used = result_json.get("capabilities_used") if isinstance(result_json, dict) else None
        latest_run = max(getattr(task, 'runs', []), key=lambda run: run.id or 0, default=None)
        liveness = execution_liveness(task, latest_run)
        uncertain_execution = task.status == 'running' and liveness['state'] in {'unknown', 'owner_lost'}
        legacy_completed = task.status == 'completed' and latest_run is not None and latest_run.status == 'completed'
        historical_diagnostics = ({'blocked_reason': task.blocked_reason, 'error_message': task.error_message}
            if legacy_completed and (task.blocked_reason or task.error_message) else None)
        return {
            "id": task.id,
            "task_type": task.task_type,
            "status": 'blocked' if uncertain_execution else task.status,
            "recorded_status": task.status,
            "execution_liveness": liveness,
            "priority": task.priority,
            "requested_by": task.requested_by,
            "worker_key": task.worker_key,
            "privacy_mode": task.privacy_mode,
            "parent_task_id": task.parent_task_id,
            "task_group": task.task_group,
            "task_role": task.task_role,
            "sequence_index": task.sequence_index,
            "requires_review": task.requires_review,
            "verification_status": 'requires_reconciliation' if uncertain_execution else task.verification_status,
            "input_summary": task.input_summary,
            "redacted_prompt": task.redacted_prompt,
            "payload_json": task.payload_json,
            "execution_contract": (
                task.payload_json.get("execution_contract")
                if isinstance(task.payload_json, dict)
                else None
            ),
            "result_summary": task.result_summary,
            "result_json": task.result_json,
            "capabilities_used": capabilities_used,
            "error_message": None if legacy_completed else task.error_message,
            "blocked_reason": liveness['reason'] if uncertain_execution else None if legacy_completed else task.blocked_reason,
            "historical_diagnostics": historical_diagnostics,
            "approval_required": task.approval_required,
            "trace_id": task.trace_id,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "updated_at": task.updated_at.isoformat() if task.updated_at else None,
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            "runs": [
                {
                    "id": run.id,
                    "worker_key": run.worker_key,
                    "status": run.status,
                    "duration_ms": run.duration_ms,
                    "result_summary": run.result_summary,
                    "metadata_json": run.metadata_json,
                    "error_message": run.error_message,
                    "started_at": run.started_at.isoformat() if run.started_at else None,
                    "completed_at": run.completed_at.isoformat() if run.completed_at else None,
                }
                for run in getattr(task, "runs", [])
            ],
            "children": [
                {
                    "id": child.id,
                    "task_type": child.task_type,
                    "status": child.status,
                    "worker_key": child.worker_key,
                    "task_role": child.task_role,
                    "sequence_index": child.sequence_index,
                    "requires_review": child.requires_review,
                    "verification_status": child.verification_status,
                    "input_summary": child.input_summary,
                    "blocked_reason": child.blocked_reason,
                    "result_summary": child.result_summary,
                    "capabilities_used": (
                        child.result_json.get("capabilities_used")
                        if isinstance(child.result_json, dict)
                        else None
                    ),
                    "execution_contract": (
                        child.payload_json.get("execution_contract")
                        if isinstance(child.payload_json, dict)
                        else None
                    ),
                }
                for child in sorted(getattr(task, "children", []), key=lambda item: item.sequence_index)
            ],
        }


def _verification_status_from_result(result: dict[str, Any], *, fallback: str) -> str:
    """Mapuje wyłącznie niezależny wynik weryfikacji na status zadania."""
    verification = result.get("verification")
    if not isinstance(verification, dict):
        return fallback
    if verification.get("status") == "passed":
        # The proposed patch was verified in an isolated worktree. It still is
        # not an approval to apply the diff to the active source checkout.
        return "verified_proposal_not_applied"
    if verification.get("status") == "failed":
        return "verification_failed"
    return fallback


def _lifecycle_phases(result: dict[str, Any]) -> list[str]:
    lifecycle = result.get("lifecycle")
    if not isinstance(lifecycle, list):
        return []
    return [
        str(item.get("phase"))
        for item in lifecycle
        if isinstance(item, dict) and item.get("phase")
    ]


def _summary(value: str, *, limit: int = 240) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."
