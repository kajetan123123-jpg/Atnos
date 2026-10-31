from __future__ import annotations

from sqlalchemy.orm import Session

from atnos.repositories.ai_worker_repository import AiWorkerRepository


class AiWorkerTaskQueue:
    """Cienka fasada nad repozytorium, zostawiona jako stabilny punkt integracji."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = AiWorkerRepository(session)

    def enqueue_manual(self, *, tasks: list[str], conversation_id: int) -> list[dict]:
        """Persist ordered work without granting execution or starting a worker."""
        import hashlib
        import json
        from atnos.ai_workers.redaction import redact_text
        if not 1 <= len(tasks) <= 10 or any(not task.strip() or len(task) > 4000 for task in tasks):
            raise ValueError("Queue requires 1–10 nonempty tasks, at most 4000 characters each.")
        tasks = [redact_text(task.strip()) for task in tasks]
        key = hashlib.sha256(json.dumps([conversation_id, tasks], ensure_ascii=False).encode()).hexdigest()
        from sqlalchemy import select
        from atnos.db.models.ai_worker_task import AiWorkerTask
        # No worker consumes these held plans. Repeated same-chat submissions reuse them.
        rows = list(self.session.scalars(select(AiWorkerTask).where(
            AiWorkerTask.task_group == key, AiWorkerTask.status == "planned"
        ).order_by(AiWorkerTask.sequence_index)))
        if not rows:
            for index, task in enumerate(tasks):
                row = self.repository.create_task(
                    task_type="manual_queue", redacted_prompt=task, worker_key=None,
                    privacy_mode="strict_local", priority="normal", requested_by="chat_queue",
                    input_summary=task[:240], payload_json={"conversation_id": conversation_id,
                        "execution_scope": "hold_only", "auto_execute": False},
                    approval_required=True, task_group=key, sequence_index=index,
                    verification_status="not_executed",
                )
                row.status = "planned"
                rows.append(row)
            self.session.commit()
        return [{"id": row.id, "position": row.sequence_index + 1,
                 "status": row.status, "task": row.redacted_prompt, "executed": False} for row in rows]

    def list_recent(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, object]]:
        return [
            {
                "id": item.id,
                "task_type": item.task_type,
                "status": item.status,
                "worker_key": item.worker_key,
                "priority": item.priority,
                "created_at": item.created_at.isoformat() if item.created_at else None,
                "result_summary": item.result_summary,
                "blocked_reason": item.blocked_reason,
                "error_message": item.error_message,
            }
            for item in self.repository.list_tasks(status=status, limit=limit)
        ]
