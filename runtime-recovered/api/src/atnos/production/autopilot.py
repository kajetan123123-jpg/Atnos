from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from typing import Any

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.intelligence.storage import connect, rows_to_dicts, utc_now_iso
from atnos.production.cost_guard import CostGuard
from atnos.production.job_recovery import ProductionJobRecoveryService
from atnos.production.orchestrator import ProductionOrchestrator
from atnos.production.review_dispatcher import ProductionReviewDispatcher
from atnos.production.voice_provider_status import build_voice_provider_status

EventSink = Callable[[dict[str, Any]], None | Awaitable[None]]
SessionScope = Callable[[], AbstractContextManager[Session]]

ACTIVE_JOB_STATUSES = ("queued", "running")


class ProductionAutopilotService:
    """Uruchamia produkcję dla zatwierdzonych tematów bez ręcznego startu joba."""

    def __init__(
        self,
        settings: Settings,
        *,
        event_sink: EventSink | None = None,
        session_scope: SessionScope | None = None,
        max_retries: int = 3,
    ) -> None:
        self.settings = settings
        self.event_sink = event_sink
        self.session_scope = session_scope
        self.max_retries = max(1, int(max_retries))

    async def run_once(self, *, max_jobs: int | None = None, account_id: str | None = None) -> dict[str, Any]:
        if not self.settings.shorts_autopilot_enabled:
            return {
                "status": "disabled",
                "started_count": 0,
                "jobs": [],
                "errors": [],
                "message": "SHORTS_AUTOPILOT_ENABLED is disabled.",
            }

        safe_limit = max(1, min(int(max_jobs or self.settings.shorts_autopilot_max_jobs_per_tick), 3))
        cost_guard = CostGuard(self.settings)
        cost_estimate = cost_guard.estimate_production_job()
        cost_decision = cost_guard.check_can_spend(
            cost_estimate.estimated_cost_pln * safe_limit,
            account_id=account_id,
            operation="production_autopilot_batch",
        )
        if not cost_decision["allowed"]:
            await self._emit(
                {
                    "type": "production_autopilot",
                    "status": "blocked",
                    "blocker": "cost_guard_budget_blocked",
                    "cost_guard": cost_decision,
                    "timestamp": utc_now_iso(),
                }
            )
            return {
                "status": "blocked",
                "started_count": 0,
                "jobs": [],
                "errors": [
                    {
                        "scope": "global",
                        "blocker": "cost_guard_budget_blocked",
                        "cost_guard": cost_decision,
                    }
                ],
                "cost_guard": cost_decision,
                "message": "Autopilot production is blocked by the configured API spend budget.",
            }
        recovery = await asyncio.to_thread(
            ProductionJobRecoveryService(self.settings).recover_stale_jobs,
            account_id=account_id,
        )
        active_jobs = await asyncio.to_thread(self._global_active_jobs)
        if active_jobs:
            return {
                "status": "busy",
                "started_count": 0,
                "jobs": [],
                "errors": [],
                "recovery": recovery,
                "cost_guard": cost_decision,
                "active_jobs": active_jobs,
                "message": "Production is already running; autopilot keeps jobs sequential to protect GPU and TTS resources.",
            }
        candidates = await asyncio.to_thread(self._select_candidates, safe_limit, account_id)
        if not candidates:
            return {
                "status": "idle",
                "started_count": 0,
                "jobs": [],
                "errors": [],
                "recovery": recovery,
                "cost_guard": cost_decision,
                "message": "No approved topics are ready for production.",
            }
        voice_provider = build_voice_provider_status(self.settings)
        if bool(getattr(self.settings, "shorts_require_premium_voice_before_render", False)) and not bool(
            voice_provider.get("public_voice_ready")
        ):
            await self._emit(
                {
                    "type": "production_autopilot",
                    "status": "blocked",
                    "blocker": "premium_voice_provider_not_ready",
                    "next_action": str(voice_provider.get("next_action") or "run_voice_provider_doctor"),
                    "timestamp": utc_now_iso(),
                }
            )
            return {
                "status": "blocked",
                "started_count": 0,
                "jobs": [],
                "errors": [
                    {
                        "scope": "global",
                        "blocker": "premium_voice_provider_not_ready",
                        "next_action": str(voice_provider.get("next_action") or "run_voice_provider_doctor"),
                    }
                ],
                "recovery": recovery,
                "voice_provider": voice_provider,
                "cost_guard": cost_decision,
                "message": "Autopilot production is blocked until a premium voice provider passes live readiness for render.",
            }

        jobs: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        orchestrator = ProductionOrchestrator(
            self.settings,
            event_sink=self._emit,
            max_retries=self.max_retries,
        )
        review_dispatcher = ProductionReviewDispatcher(
            self.settings,
            session_scope=self.session_scope,
            event_sink=self._emit,
        )

        for candidate in candidates:
            await self._emit(
                {
                    "type": "production_autopilot",
                    "status": "starting",
                    "account_id": candidate["account_id"],
                    "topic_id": candidate["id"],
                    "topic_title": candidate["title"],
                    "timestamp": utc_now_iso(),
                }
            )
            try:
                result = await orchestrator.start(account_id=str(candidate["account_id"]), topic_id=str(candidate["id"]))
            except Exception as exc:
                error = {
                    "account_id": candidate["account_id"],
                    "topic_id": candidate["id"],
                    "error": f"{type(exc).__name__}: {exc}",
                }
                errors.append(error)
                await self._emit(
                    {
                        "type": "production_autopilot",
                        "status": "error",
                        **error,
                        "timestamp": utc_now_iso(),
                    }
                )
                continue
            review_result = (
                await review_dispatcher.handle_job(str(result.get("id")))
                if str(result.get("status") or "") == "qa_passed" and result.get("id")
                else {
                    "status": "not_ready",
                    "message": f"Job finished with status {result.get('status')}.",
                }
            )
            jobs.append(
                {
                    "job_id": result.get("id"),
                    "account_id": candidate["account_id"],
                    "topic_id": candidate["id"],
                    "topic_title": candidate["title"],
                    "job_status": result.get("status"),
                    "review": review_result,
                    "private_upload": review_result.get("private_upload")
                    or (
                        "human_review_required"
                        if self.settings.shorts_require_human_approval_before_upload
                        else "manual_endpoint_available"
                    ),
                }
            )

        return {
            "status": "completed" if jobs else "failed",
            "started_count": len(jobs),
            "jobs": jobs,
            "errors": errors,
            "recovery": recovery,
            "cost_guard": cost_decision,
            "message": f"Started {len(jobs)} production job(s).",
        }

    def _global_active_jobs(self) -> list[dict[str, Any]]:
        params: list[Any] = [*ACTIVE_JOB_STATUSES]
        active_statuses_sql = ",".join("?" for _ in ACTIVE_JOB_STATUSES)
        with connect(self.settings) as db:
            rows = db.execute(
                f"""
                SELECT id, account_id, topic_id, status, current_step, updated_at
                FROM pipeline_jobs
                WHERE status IN ({active_statuses_sql})
                ORDER BY COALESCE(updated_at, created_at, '') ASC
                """,
                tuple(params),
            ).fetchall()
        return rows_to_dicts(rows)

    def _select_candidates(self, limit: int, account_id: str | None) -> list[dict[str, Any]]:
        params: list[Any] = []
        account_filter = ""
        if account_id:
            account_filter = "AND t.account_id = ?"
            params.append(str(account_id))
        active_statuses_sql = ",".join("?" for _ in ACTIVE_JOB_STATUSES)
        params.extend(ACTIVE_JOB_STATUSES)
        params.extend(ACTIVE_JOB_STATUSES)
        params.append(limit)
        with connect(self.settings) as db:
            rows = db.execute(
                f"""
                SELECT
                    t.id,
                    t.account_id,
                    t.title,
                    t.description,
                    t.total_score,
                    a.content_profile,
                    t.created_at
                FROM topic_queue t
                INNER JOIN intelligence_accounts a
                    ON a.account_id = t.account_id AND a.enabled = 1
                WHERE t.status = 'approved'
                    AND TRIM(COALESCE(a.content_profile, '')) <> ''
                    AND COALESCE(a.automation_mode, 'manual') IN ('auto_private', 'auto_public')
                    {account_filter}
                    AND NOT EXISTS (
                        SELECT 1
                        FROM pipeline_jobs active_account_job
                        WHERE active_account_job.account_id = t.account_id
                          AND active_account_job.status IN ({active_statuses_sql})
                    )
                    AND NOT EXISTS (
                        SELECT 1
                        FROM pipeline_jobs active_topic_job
                        WHERE active_topic_job.topic_id = t.id
                          AND active_topic_job.status IN ({active_statuses_sql})
                    )
                ORDER BY COALESCE(t.total_score, 0) DESC, COALESCE(t.created_at, '') ASC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return rows_to_dicts(rows)

    async def _emit(self, event: dict[str, Any]) -> None:
        if self.event_sink is None:
            return
        result = self.event_sink(event)
        if asyncio.iscoroutine(result):
            await result
