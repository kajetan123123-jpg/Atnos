"""Sub-agent w tle: tworzy zadanie AI worker i odpala je poza turą czatu."""

from __future__ import annotations

import asyncio
import threading
from typing import Any
from uuid import uuid4

from atnos.ai_workers.delegation_router import AiDelegationRouter, DelegateTaskInput
from atnos.core.config import Settings
from atnos.db.session import DatabaseManager


class BackgroundSubagentService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def start(
        self,
        *,
        prompt: str,
        task_type: str = "general",
        preferred_worker: str | None = "ollama",
        privacy_mode: str = "strict_local",
        priority: str = "normal",
        source_paths: list[str] | None = None,
        conversation_id: int | None = None,
    ) -> dict[str, Any]:
        if conversation_id is not None and (isinstance(conversation_id, bool)
                or not isinstance(conversation_id, int) or conversation_id <= 0):
            raise ValueError('conversation_id must be a positive integer')
        clean_prompt = str(prompt or "").strip()
        if not clean_prompt:
            raise ValueError("prompt is required")
        clean_type = " ".join(str(task_type or "general").strip().split()) or "general"
        worker = (preferred_worker or "ollama").strip() or "ollama"
        mode = (privacy_mode or "strict_local").strip() or "strict_local"
        if mode not in {"strict_local", "allow_browser_ai", "allow_cloud_api"}:
            mode = "strict_local"
        prio = (priority or "normal").strip() or "normal"
        if prio not in {"low", "normal", "high"}:
            prio = "normal"
        coding = clean_type == "coding"
        if coding:
            # Preserve source indentation and pin coding delegation to the local runtime.
            clean_prompt = str(prompt or "").strip()
            if len(clean_prompt) > 4000:
                raise ValueError("Coding task exceeds 4000 characters; split it into a smaller task.")
            worker, mode = "ollama", "strict_local"
            from atnos.ai_workers.coding_proposal import source_snapshot

            source_snapshot(self.settings.app_root, source_paths)

        job_token = uuid4().hex[:10]
        holder: dict[str, Any] = {"job_token": job_token}

        def _run() -> None:
            db = DatabaseManager(self.settings)
            try:
                with db.session_scope() as session:
                    router = AiDelegationRouter(session, self.settings)
                    result = asyncio.run(
                        router.delegate(
                            DelegateTaskInput(
                                task_type=clean_type,
                                prompt=clean_prompt,
                                context={"source": "chat_ai_delegate_background", "job_id": job_token,
                                    **({'conversation_id': conversation_id} if conversation_id is not None else {}),
                                    **({"source_paths": source_paths} if coding else {})},
                                preferred_worker=worker,
                                allowed_workers=("ollama",) if coding else (worker, "ollama", "codex"),
                                privacy_mode=mode,
                                priority=prio,
                                requested_by="chat_agent",
                                requires_approval_for_result=coding,
                            )
                        )
                    )
                    holder["result"] = result
            except Exception as exc:  # pragma: no cover - background isolation
                holder["error"] = f"{exc.__class__.__name__}: {exc}"
            finally:
                db.dispose()

        thread = threading.Thread(
            target=_run,
            name=f"atnos-subagent-{job_token}",
            daemon=True,
        )
        thread.start()
        return {
            "status": "started_background",
            "job_token": job_token,
            "thread_name": thread.name,
            "task_type": clean_type,
            "preferred_worker": worker,
            "privacy_mode": mode,
            "conversation_id": conversation_id,
            "execution_verified": False,
            "hint": (
                "Zlecono uruchomienie w tle; nie potwierdzono jeszcze wykonania. "
                "Sprawdź rejestrację i stan przez GET /api/ai-workers/tasks lub ai_delegate_status."
            ),
        }

    def list_recent(self, *, limit: int = 12, query: str | None = None, conversation_id: int | None = None) -> dict[str, Any]:
        db = DatabaseManager(self.settings)
        try:
            with db.session_scope() as session:
                tasks = AiDelegationRouter(session, self.settings).list_tasks(
                    roots_only=True,
                    query=query,
                    conversation_id=conversation_id,
                    limit=max(1, min(int(limit), 50)),
                )
            # Status questions must not send whole prompts, code diffs and nested
            # execution histories into the small chat model's context window.
            summaries = []
            for task in tasks:
                context = (task.get("payload_json") or {}).get("context") or {}
                summaries.append({
                    **{key: task.get(key) for key in ("id", "task_type", "status", "worker_key", "verification_status", "created_at", "updated_at", "sequence_index", "task_group")},
                    "job_token": context.get('job_id') or (
                        context.get('job_token') if context.get('job_token') != '[REDACTED]' else None),
                    "summary": str(task.get("input_summary") or task.get("result_summary") or "")[:240],
                    "error": str(task.get("error_message") or task.get("blocked_reason") or "")[:240],
                })
            groups = {group: index for index, group in enumerate(dict.fromkeys(item.get('task_group') or str(item['id']) for item in summaries))}
            summaries.sort(key=lambda item: (groups[item.get('task_group') or str(item['id'])], item.get('sequence_index') or 0))
            return {"status": "ok", "count": len(summaries), "scope": "filtered_root_tasks" if query or conversation_id is not None else "recent_root_tasks", "query": query, "conversation_id": conversation_id, "tasks": summaries}
        finally:
            db.dispose()
