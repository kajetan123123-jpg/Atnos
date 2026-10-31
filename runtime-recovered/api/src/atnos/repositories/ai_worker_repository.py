from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import case, func, select, update
from sqlalchemy.orm import Session, selectinload

from atnos.db.models.ai_worker_task import AiWorkerRun, AiWorkerTask
from atnos.ai_workers.execution_liveness import current_executor_owner


class AiWorkerRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_task(
        self,
        *,
        task_type: str,
        redacted_prompt: str,
        worker_key: str | None,
        privacy_mode: str,
        priority: str,
        requested_by: str,
        input_summary: str | None,
        payload_json: dict[str, object],
        approval_required: bool = False,
        parent_task_id: int | None = None,
        task_group: str | None = None,
        task_role: str = "single",
        sequence_index: int = 0,
        requires_review: bool = False,
        verification_status: str = "unverified",
    ) -> AiWorkerTask:
        task = AiWorkerTask(
            task_type=task_type,
            redacted_prompt=redacted_prompt,
            worker_key=worker_key,
            privacy_mode=privacy_mode,
            priority=priority,
            requested_by=requested_by,
            parent_task_id=parent_task_id,
            task_group=task_group,
            task_role=task_role,
            sequence_index=sequence_index,
            requires_review=requires_review,
            verification_status=verification_status,
            input_summary=input_summary,
            payload_json=payload_json,
            approval_required=approval_required,
        )
        self.session.add(task)
        self.session.flush()
        return task

    def get_task(self, task_id: int) -> AiWorkerTask | None:
        statement = (
            select(AiWorkerTask)
            .options(selectinload(AiWorkerTask.runs), selectinload(AiWorkerTask.children))
            .where(AiWorkerTask.id == task_id)
        )
        return self.session.scalar(statement)

    def list_tasks(
        self,
        *,
        status: str | None = None,
        parent_task_id: int | None = None,
        roots_only: bool = False,
        query: str | None = None,
        conversation_id: int | None = None,
        limit: int = 50,
    ) -> list[AiWorkerTask]:
        statement = select(AiWorkerTask).options(selectinload(AiWorkerTask.runs), selectinload(AiWorkerTask.children))
        if status:
            statement = statement.where(AiWorkerTask.status == status)
        if query:
            from sqlalchemy import or_
            statement = statement.where(or_(AiWorkerTask.input_summary.contains(query, autoescape=True),
                AiWorkerTask.redacted_prompt.contains(query, autoescape=True),
                AiWorkerTask.payload_json['context']['job_id'].as_string().contains(query, autoescape=True),
                AiWorkerTask.payload_json['context']['job_token'].as_string().contains(query, autoescape=True)))
        if conversation_id is not None:
            statement = statement.where(func.coalesce(
                AiWorkerTask.payload_json['conversation_id'].as_integer(),
                AiWorkerTask.payload_json['context']['conversation_id'].as_integer()) == conversation_id)
        if parent_task_id is not None:
            statement = statement.where(AiWorkerTask.parent_task_id == parent_task_id)
        elif roots_only:
            statement = statement.where(AiWorkerTask.parent_task_id.is_(None))
        partition = (AiWorkerTask.task_group,
                     case((AiWorkerTask.task_group.is_(None), AiWorkerTask.id), else_=None))
        # Recent groups first, but preserve the declared order inside a group.
        # Filters above are applied before the window/order/limit.
        statement = statement.order_by(
            func.max(AiWorkerTask.created_at).over(partition_by=partition).desc(),
            func.max(AiWorkerTask.id).over(partition_by=partition).desc(),
            AiWorkerTask.sequence_index.asc(), AiWorkerTask.id.asc()).limit(limit)
        return list(self.session.scalars(statement))

    def mark_planned(
        self,
        task: AiWorkerTask,
        *,
        result_summary: str,
        result_json: dict[str, object],
        task_group: str | None = None,
        verification_status: str = "planned",
    ) -> AiWorkerTask:
        task.status = "planned"
        task.worker_key = "planner"
        task.task_role = "planner"
        task.task_group = task_group
        task.result_summary = result_summary
        task.result_json = result_json
        task.verification_status = verification_status
        self.session.flush()
        return task

    def update_plan_progress(
        self,
        task: AiWorkerTask,
        *,
        status: str,
        result_summary: str,
        result_json: dict[str, object],
        verification_status: str,
    ) -> AiWorkerTask:
        """Zapisuje stan orkiestratora bez udawania, ze plan jest w calosci wykonany."""
        task.status = status
        task.result_summary = result_summary
        task.result_json = result_json
        task.verification_status = verification_status
        if status in {"completed", "failed", "blocked", "cancelled"}:
            task.completed_at = datetime.now(UTC)
        self.session.flush()
        return task

    def start_task(self, task: AiWorkerTask, *, worker_key: str) -> AiWorkerRun:
        now = datetime.now(UTC)
        if task.status not in {'queued', 'planned', 'failed', 'blocked', 'incomplete'}:
            raise ValueError(f'Task {task.id} cannot be claimed from {task.status}.')
        claimed = self.session.execute(update(AiWorkerTask).where(
            AiWorkerTask.id == task.id, AiWorkerTask.status == task.status,
            AiWorkerTask.started_at == task.started_at,
        ).values(status='running', worker_key=worker_key, started_at=now, completed_at=None),
            execution_options={'synchronize_session': False})
        if claimed.rowcount != 1:
            raise ValueError(f'Task {task.id} claim rejected: state changed or another worker claimed it.')
        self.session.refresh(task, attribute_names=['status', 'worker_key', 'started_at', 'completed_at'])
        run = AiWorkerRun(
            task_id=task.id,
            worker_key=worker_key,
            status="running",
            started_at=now,
            metadata_json={'executor_owner': current_executor_owner()},
        )
        self.session.add(run)
        task.runs.append(run)
        self.session.flush()
        return run

    def complete_task(
        self,
        task: AiWorkerTask,
        run: AiWorkerRun | None,
        *,
        result_summary: str,
        result_json: dict[str, object],
        trace_id: int | None = None,
        verification_status: str | None = None,
    ) -> AiWorkerTask:
        now = datetime.now(UTC)
        task.status = "completed"
        task.completed_at = now
        task.blocked_reason = None
        task.error_message = None
        task.result_summary = result_summary
        task.result_json = result_json
        task.trace_id = trace_id
        if verification_status is not None:
            task.verification_status = verification_status
        if run is not None:
            run.status = "completed"
            run.completed_at = now
            run.result_summary = result_summary
            run.result_json = result_json
            run.metadata_json = _metadata_with_lifecycle(run.metadata_json, result_json)
            run.duration_ms = _duration_ms(run.started_at, now)
        self.session.flush()
        return task

    def mark_pending_external(
        self,
        task: AiWorkerTask,
        run: AiWorkerRun | None,
        *,
        result_summary: str,
        result_json: dict[str, object],
    ) -> AiWorkerTask:
        """Zapisuje handoff bez fałszywego oznaczania zewnętrznego wyniku jako ukończony."""
        now = datetime.now(UTC)
        task.status = "pending_external"
        task.completed_at = None
        task.result_summary = result_summary
        task.result_json = result_json
        task.verification_status = "pending_external"
        if run is not None:
            run.status = "pending_external"
            run.completed_at = now
            run.result_summary = result_summary
            run.result_json = result_json
            run.duration_ms = _duration_ms(run.started_at, now)
        self.session.flush()
        return task

    def fail_task(
        self,
        task: AiWorkerTask,
        run: AiWorkerRun | None,
        *,
        error_message: str,
        trace_id: int | None = None,
        verification_status: str = "failed",
        result_json: dict[str, object] | None = None,
    ) -> AiWorkerTask:
        now = datetime.now(UTC)
        task.status = "failed"
        task.completed_at = now
        task.error_message = error_message
        task.trace_id = trace_id
        task.verification_status = verification_status
        if result_json is not None:
            task.result_json = result_json
        if run is not None:
            run.status = "failed"
            run.completed_at = now
            run.error_message = error_message
            if result_json is not None:
                run.result_json = result_json
                run.metadata_json = _metadata_with_lifecycle(run.metadata_json, result_json)
            run.duration_ms = _duration_ms(run.started_at, now)
        self.session.flush()
        return task

    def review_codex_result(
        self,
        task: AiWorkerTask,
        *,
        decision: str,
        reviewer: str,
        note: str,
    ) -> AiWorkerTask:
        """Zapisuje decyzję człowieka bez stosowania propozycji do repozytorium."""
        now = datetime.now(UTC)
        result = dict(task.result_json or {})
        history = list(result.get("review_history") or [])
        review = {
            "decision": decision,
            "reviewer": reviewer,
            "note": note,
            "reviewed_at": now.isoformat(),
            "apply_authorized": False,
        }
        history.append(review)
        result["review"] = review
        result["review_history"] = history
        result["proposal_state"] = "approved_not_applied" if decision == "approve" else "rejected"
        task.result_json = result
        task.status = "approved" if decision == "approve" else "rejected"
        task.verification_status = "approved_not_applied" if decision == "approve" else "rejected"
        task.completed_at = now
        self.session.flush()
        return task

    def rollback_codex_proposal(
        self,
        task: AiWorkerTask,
        *,
        reviewer: str,
        reason: str,
    ) -> AiWorkerTask:
        """Unieważnia izolowaną propozycję, zachowując diff jako dowód audytowy."""
        now = datetime.now(UTC)
        result = dict(task.result_json or {})
        rollback = dict(result.get("rollback") or {})
        rollback.update(
            {
                "status": "rolled_back",
                "rolled_back_at": now.isoformat(),
                "reviewer": reviewer,
                "reason": reason,
                "main_worktree_modified": False,
                "proposal_artifact_retained_for_audit": True,
                "apply_authorized": False,
            }
        )
        result["rollback"] = rollback
        result["proposal_state"] = "rolled_back"
        task.result_json = result
        task.status = "rolled_back"
        task.verification_status = "rolled_back"
        task.completed_at = now
        self.session.flush()
        return task

    def block_task(self, task: AiWorkerTask, *, reason: str, trace_id: int | None = None) -> AiWorkerTask:
        task.status = "blocked"
        task.blocked_reason = reason
        task.completed_at = datetime.now(UTC)
        task.trace_id = trace_id
        task.verification_status = "blocked"
        self.session.flush()
        return task

    def cancel_task(self, task: AiWorkerTask) -> AiWorkerTask:
        task.status = "cancelled"
        task.completed_at = datetime.now(UTC)
        task.verification_status = "cancelled"
        self.session.flush()
        return task


def _duration_ms(started_at: datetime | None, finished_at: datetime) -> int | None:
    if started_at is None:
        return None
    return max(0, int((finished_at - started_at).total_seconds() * 1000))


def _metadata_with_lifecycle(
    current: dict[str, object] | None,
    result_json: dict[str, object],
) -> dict[str, object]:
    metadata = dict(current or {})
    lifecycle = result_json.get("lifecycle")
    if isinstance(lifecycle, list):
        metadata["lifecycle"] = lifecycle
        metadata["lifecycle_phase"] = lifecycle[-1].get("phase") if lifecycle and isinstance(lifecycle[-1], dict) else None
    metadata["prompt_sha256"] = result_json.get("prompt_sha256")
    metadata["result_sha256"] = result_json.get("result_sha256")
    metadata["prompt_equals_result"] = result_json.get("prompt_equals_result")
    return metadata
