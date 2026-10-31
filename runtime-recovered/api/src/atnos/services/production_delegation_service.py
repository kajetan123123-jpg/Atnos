from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from atnos.ai_workers.delegation_router import AiDelegationRouter, DelegatePlanInput
from atnos.core.config import Settings
from atnos.intelligence.storage import connect
from atnos.repositories.ai_worker_repository import AiWorkerRepository
from atnos.services.production_preflight_service import ProductionPreflightService


class ProductionDelegationService:
    """Tworzy audytowalny plan review dla produkcji Shorta.

    Warstwa nie renderuje, nie publikuje i nie kupuje usług. Jej celem jest
    wymusić konkretne review: research, skrypt, wizuale, miniatura, QA.
    Wszystkie komunikaty opiera na danych z preflightu i zapisanych taskach.
    """

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.repository = AiWorkerRepository(session)

    async def create_review_plan(
        self,
        *,
        account_id: str,
        topic_id: str,
        allow_browser_ai: bool = False,
        max_steps: int = 6,
    ) -> dict[str, Any]:
        preflight = await ProductionPreflightService(self.session, self.settings).check(
            account_id=account_id,
            topic_id=topic_id,
        )
        preflight_payload = preflight.to_dict()
        topic = preflight_payload["topic"]
        public_quality = preflight_payload["public_quality_gate"]
        signal_counts = preflight_payload["signal_counts"]
        prompt = self._build_review_prompt(preflight_payload)
        privacy_mode = "allow_browser_ai" if allow_browser_ai else "strict_local"
        allowed_workers = (
            ("ollama", "browser_claude", "browser_chatgpt", "browser_kimi")
            if allow_browser_ai
            else ("ollama",)
        )

        root = AiDelegationRouter(self.session, self.settings).delegate_plan(
            DelegatePlanInput(
                task_type="content_short_quality_review",
                prompt=prompt,
                context={
                    "account_id": account_id,
                    "topic_id": topic_id,
                    "topic": topic,
                    "signal_counts": signal_counts,
                    "public_quality_gate": public_quality,
                    "runtime": preflight_payload["runtime"],
                    "style_guide": preflight_payload["style_guide"],
                    "visual_backend": preflight_payload["visual_backend"],
                    "voice_provider": preflight_payload["voice_provider"],
                    "source": "production_preflight",
                },
                preferred_worker="ollama",
                allowed_workers=allowed_workers,
                privacy_mode=privacy_mode,
                priority="high",
                requested_by="production_delegation",
                requires_approval_for_result=True,
                max_steps=max_steps,
            )
        )
        return {
            "status": "planned" if root.get("status") == "planned" else root.get("status"),
            "account_id": account_id,
            "topic_id": topic_id,
            "ready_for_review": root.get("status") == "planned",
            "source": "production_preflight",
            "evidence": self._evidence(preflight_payload),
            "root_task": root,
            "latest": self.status(account_id=account_id, topic_id=topic_id),
        }

    def status(self, *, account_id: str, topic_id: str) -> dict[str, Any]:
        matching = self._matching_review_roots(account_id=account_id, topic_id=topic_id)
        latest = matching[0] if matching else None
        topic = self._topic_snapshot(account_id=account_id, topic_id=topic_id)
        counts = self._task_counts(latest)
        has_executable_task = counts["latest_queued_child_tasks"] > 0 or counts["latest_failed_child_tasks"] > 0
        production_gate = self._production_gate(latest, counts)
        review_summary = self._review_checklist_summary(
            latest=latest,
            counts=counts,
            production_gate=production_gate,
        )
        return {
            "account_id": account_id,
            "topic_id": topic_id,
            "topic": topic,
            "has_review_plan": latest is not None,
            "ready_for_review": latest is not None and latest.status == "planned",
            "ready_for_execution": has_executable_task,
            "ready_for_human_review": latest is not None and counts["latest_completed_child_tasks"] > 0,
            "ready_for_production_start": production_gate["ready"],
            "production_gate_blockers": production_gate["blockers"],
            "status": latest.status if latest is not None else "missing",
            "reason": (
                "Production review gate passed: all required review tasks are completed."
                if production_gate["ready"]
                else "; ".join(production_gate["blockers"])
            ),
            "root_task": AiDelegationRouter._serialize_task(latest) if latest is not None else None,
            "counts": {"matching_root_tasks": len(matching), **counts},
            "review_summary": review_summary,
            "next_action": (
                "Run POST /api/production/delegation-review/plan before local production."
                if latest is None
                else (
                    "Run POST /api/production/delegation-review/run-next to execute the next child task."
                    if has_executable_task
                    else (
                        "Review completed child task outputs; production can start."
                        if production_gate["ready"]
                        else "Resolve blocked/running review tasks before local production."
                    )
                )
            ),
        }

    async def run_next_review_task(self, *, account_id: str, topic_id: str) -> dict[str, Any]:
        matching = self._matching_review_roots(account_id=account_id, topic_id=topic_id)
        latest = matching[0] if matching else None
        if latest is None:
            missing = {
                "status": "missing",
                "account_id": account_id,
                "topic_id": topic_id,
                "ran_task": None,
                "reason": "No delegation review plan exists for this account/topic.",
                "latest": self.status(account_id=account_id, topic_id=topic_id),
            }
            missing["review_summary"] = missing["latest"].get("review_summary")
            return missing

        next_task = self._next_executable_child(latest)
        if next_task is None:
            no_task = {
                "status": "no_executable_task",
                "account_id": account_id,
                "topic_id": topic_id,
                "ran_task": None,
                "reason": "All child tasks are completed, blocked, cancelled or already running.",
                "latest": self.status(account_id=account_id, topic_id=topic_id),
            }
            no_task["review_summary"] = no_task["latest"].get("review_summary")
            return no_task

        ran_task = await AiDelegationRouter(self.session, self.settings).run_task(int(next_task.id))
        latest_status = self.status(account_id=account_id, topic_id=topic_id)
        return {
            "status": ran_task.get("status") if ran_task is not None else "missing",
            "account_id": account_id,
            "topic_id": topic_id,
            "ran_task": ran_task,
            "reason": (
                ran_task.get("result_summary")
                or ran_task.get("blocked_reason")
                or ran_task.get("error_message")
                or f"Task {next_task.id} updated."
                if ran_task is not None
                else f"Task {next_task.id} was not found."
            ),
            "latest": latest_status,
            "review_summary": latest_status.get("review_summary"),
        }

    async def run_all_review_tasks(
        self,
        *,
        account_id: str,
        topic_id: str,
        limit: int = 12,
    ) -> dict[str, Any]:
        """Run pending review children sequentially and stop on the first hard blocker."""
        safe_limit = max(1, min(int(limit), 12))
        results: list[dict[str, Any]] = []
        stopped_reason: str | None = None

        for _ in range(safe_limit):
            latest_status = self.status(account_id=account_id, topic_id=topic_id)
            if not latest_status.get("has_review_plan"):
                stopped_reason = "No delegation review plan exists for this account/topic."
                break
            if bool(latest_status.get("ready_for_production_start")):
                stopped_reason = "Production review gate already passed."
                break
            if not bool(latest_status.get("ready_for_execution")):
                stopped_reason = latest_status.get("reason") or "No executable review task remains."
                break

            result = await self.run_next_review_task(account_id=account_id, topic_id=topic_id)
            results.append(result)
            if result.get("status") in {"blocked", "failed", "missing", "no_executable_task"}:
                stopped_reason = result.get("reason") or f"Stopped on status {result.get('status')}."
                break

        latest = self.status(account_id=account_id, topic_id=topic_id)
        if stopped_reason is None:
            stopped_reason = (
                "Execution limit reached."
                if len(results) >= safe_limit and not latest.get("ready_for_production_start")
                else "No more review work was required."
            )
        return {
            "status": "completed" if latest.get("ready_for_production_start") else "partial",
            "account_id": account_id,
            "topic_id": topic_id,
            "ran_count": len(results),
            "limit": safe_limit,
            "results": results,
            "reason": stopped_reason,
            "latest": latest,
            "review_summary": latest.get("review_summary"),
        }

    def _build_review_prompt(self, preflight: dict[str, Any]) -> str:
        topic = preflight["topic"]
        signal_counts = preflight["signal_counts"]
        public_quality = preflight["public_quality_gate"]
        blockers = public_quality.get("blockers") or []
        warnings = preflight.get("warnings") or []
        blocking = preflight.get("blocking") or []
        return "\n".join(
            [
                "Create an English Shorts production review plan for Atnos.",
                "Use only the evidence below. Do not invent readiness.",
                "",
                f"Topic: {topic.get('title') or topic.get('topic_id')}",
                f"Topic status: {topic.get('status')} / score: {topic.get('total_score')}",
                f"Topic flags: {topic.get('flags')}",
                f"Research signals: {signal_counts}",
                f"Preflight blocking: {blocking}",
                f"Preflight warnings: {warnings}",
                f"Public quality ready: {public_quality.get('ready')}",
                f"Public quality blockers: {blockers}",
                "",
                "Required review areas:",
                "- Hook strength in first 3 seconds.",
                "- Script must be human, specific, English-first, not generic.",
                "- Visual plan must map every beat to concrete topic-relevant scenes.",
                "- Thumbnail must look mature and channel-specific, not childish.",
                "- Voice direction must be engaging; fallback voice is not public-quality.",
                "- Claims must be source-backed; finance content must avoid investment advice.",
            ]
        )

    def _evidence(self, preflight: dict[str, Any]) -> dict[str, Any]:
        return {
            "topic": preflight.get("topic"),
            "signal_counts": preflight.get("signal_counts"),
            "ready_to_start_local_job": preflight.get("ready_to_start_local_job"),
            "ready_for_public_quality": preflight.get("ready_for_public_quality"),
            "public_quality_blockers": (preflight.get("public_quality_gate") or {}).get("blockers") or [],
            "runtime": preflight.get("runtime"),
            "voice_provider": preflight.get("voice_provider"),
            "visual_backend": preflight.get("visual_backend"),
            "style_guide": preflight.get("style_guide"),
        }

    def _review_checklist_summary(
        self,
        *,
        latest: Any | None,
        counts: dict[str, int],
        production_gate: dict[str, Any],
    ) -> dict[str, Any]:
        """Buduje krótki werdykt review wyłącznie z zapisanych tasków i gate blockerów."""
        blockers = list(production_gate.get("blockers") or [])
        ready = bool(production_gate.get("ready"))
        children = sorted(list(latest.children), key=lambda item: item.sequence_index) if latest is not None else []
        result_summaries = [
            str(child.result_summary)
            for child in children
            if child.result_summary
        ][:3]
        first_action = self._review_first_action(blockers=blockers, ready=ready)
        return {
            "verdict": "production_ready" if ready else "blocked",
            "can_start_production": ready,
            "headline": (
                "Production review passed: all required review tasks are completed."
                if ready
                else "Production review is blocking render."
            ),
            "first_action": first_action,
            "completed_tasks": counts["latest_completed_child_tasks"],
            "total_tasks": counts["latest_child_tasks"],
            "completed_review_tasks": counts["latest_completed_review_tasks"],
            "required_review_tasks": counts["latest_review_tasks"],
            "queued_tasks": counts["latest_queued_child_tasks"],
            "running_tasks": counts["latest_running_child_tasks"],
            "blocked_tasks": counts["latest_blocked_child_tasks"],
            "failed_tasks": counts["latest_failed_child_tasks"],
            "blockers": blockers,
            "evidence": {
                "source": "ai_worker_tasks",
                "root_task_id": int(latest.id) if latest is not None else None,
                "child_task_ids": [int(child.id) for child in children],
                "latest_result_summaries": result_summaries,
            },
        }

    def _review_first_action(self, *, blockers: list[str], ready: bool) -> str:
        if ready:
            return "Start production or review generated task outputs before rendering."
        if "production_review_plan_missing" in blockers:
            return "Create review plan."
        if any(
            blocker.startswith("production_review_tasks_queued:")
            or blocker.startswith("production_review_tasks_incomplete:")
            or blocker.startswith("production_required_review_incomplete:")
            for blocker in blockers
        ):
            return "Run all review tasks."
        if any(blocker.startswith("production_review_tasks_running:") for blocker in blockers):
            return "Wait for running review task."
        if any(
            blocker.startswith("production_review_tasks_blocked:")
            or blocker.startswith("production_review_tasks_failed:")
            for blocker in blockers
        ):
            return "Open blocked/failed review task and fix its reason."
        return "Inspect production review blockers."

    def _task_matches(self, task: Any, *, account_id: str, topic_id: str) -> bool:
        if task.task_type != "content_short_quality_review":
            return False
        payload = task.payload_json if isinstance(task.payload_json, dict) else {}
        context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
        return context.get("account_id") == account_id and context.get("topic_id") == topic_id

    def _matching_review_roots(self, *, account_id: str, topic_id: str) -> list[Any]:
        roots = self.repository.list_tasks(roots_only=True, limit=120)
        matching = [
            task
            for task in roots
            if self._task_matches(task, account_id=account_id, topic_id=topic_id)
        ]
        matching.sort(key=lambda item: item.created_at, reverse=True)
        return matching

    def _task_counts(self, latest: Any | None) -> dict[str, int]:
        children = list(latest.children) if latest is not None else []
        return {
            "latest_child_tasks": len(children),
            "latest_review_tasks": sum(1 for child in children if child.requires_review),
            "latest_completed_review_tasks": sum(
                1 for child in children if child.requires_review and child.status == "completed"
            ),
            "latest_queued_child_tasks": sum(1 for child in children if child.status in {"queued", "planned"}),
            "latest_running_child_tasks": sum(1 for child in children if child.status == "running"),
            "latest_completed_child_tasks": sum(1 for child in children if child.status == "completed"),
            "latest_blocked_child_tasks": sum(1 for child in children if child.status == "blocked"),
            "latest_failed_child_tasks": sum(1 for child in children if child.status == "failed"),
        }

    def _production_gate(self, latest: Any | None, counts: dict[str, int]) -> dict[str, Any]:
        blockers: list[str] = []
        if latest is None:
            blockers.append("production_review_plan_missing")
        if latest is not None and latest.status != "planned":
            blockers.append(f"production_review_plan_status_{latest.status}")
        if counts["latest_child_tasks"] <= 0:
            blockers.append("production_review_child_tasks_missing")
        if counts["latest_review_tasks"] <= 0:
            blockers.append("production_required_review_task_missing")
        if counts["latest_queued_child_tasks"] > 0:
            blockers.append(f"production_review_tasks_queued:{counts['latest_queued_child_tasks']}")
        if counts["latest_running_child_tasks"] > 0:
            blockers.append(f"production_review_tasks_running:{counts['latest_running_child_tasks']}")
        if counts["latest_blocked_child_tasks"] > 0:
            blockers.append(f"production_review_tasks_blocked:{counts['latest_blocked_child_tasks']}")
        if counts["latest_failed_child_tasks"] > 0:
            blockers.append(f"production_review_tasks_failed:{counts['latest_failed_child_tasks']}")
        if counts["latest_completed_child_tasks"] < counts["latest_child_tasks"]:
            blockers.append(
                f"production_review_tasks_incomplete:"
                f"{counts['latest_completed_child_tasks']}/{counts['latest_child_tasks']}"
            )
        if counts["latest_completed_review_tasks"] < counts["latest_review_tasks"]:
            blockers.append(
                f"production_required_review_incomplete:"
                f"{counts['latest_completed_review_tasks']}/{counts['latest_review_tasks']}"
            )
        return {"ready": not blockers, "blockers": blockers}

    def _next_executable_child(self, latest: Any) -> Any | None:
        children = sorted(latest.children, key=lambda item: item.sequence_index)
        return next((child for child in children if child.status in {"queued", "planned", "failed"}), None)

    def _topic_snapshot(self, *, account_id: str, topic_id: str) -> dict[str, Any]:
        with connect(self.settings) as db:
            row = db.execute(
                """
                SELECT id, account_id, title, description, status, total_score,
                       clickbait_risk, evergreen, shorts_potential, created_at, updated_at
                FROM topic_queue
                WHERE id = ? AND account_id = ?
                """,
                (topic_id, account_id),
            ).fetchone()
        if row is None:
            return {"exists": False, "account_id": account_id, "topic_id": topic_id}
        return {
            "exists": True,
            "id": str(row["id"]),
            "account_id": str(row["account_id"]),
            "title": str(row["title"] or ""),
            "description": str(row["description"] or ""),
            "status": str(row["status"] or ""),
            "total_score": float(row["total_score"]) if row["total_score"] is not None else None,
            "flags": {
                "clickbait_risk": bool(row["clickbait_risk"]),
                "evergreen": bool(row["evergreen"]),
                "shorts_potential": bool(row["shorts_potential"]),
            },
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
