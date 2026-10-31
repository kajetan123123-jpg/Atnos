from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from typing import Any

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.intelligence.storage import (
    connect,
    read_intelligence_account_configs,
    utc_now_iso,
)
from atnos.production.cost_guard import CostGuard
from atnos.production.autopilot import ProductionAutopilotService
from atnos.scoring.topic_scorer import TopicScorer

EventSink = Callable[[dict[str, Any]], None | Awaitable[None]]
SessionScope = Callable[[], AbstractContextManager[Session]]


class AutonomousContentLoopService:
    """Domyka pętlę: sygnały -> tematy -> auto-approve -> produkcja QA."""

    def __init__(
        self,
        settings: Settings,
        *,
        event_sink: EventSink | None = None,
        session_scope: SessionScope | None = None,
    ) -> None:
        self.settings = settings
        self.event_sink = event_sink
        self.session_scope = session_scope

    async def run_once(self, *, account_id: str | None = None, max_jobs: int | None = None) -> dict[str, Any]:
        if not self.settings.shorts_autopilot_enabled:
            return {
                "status": "disabled",
                "started_count": 0,
                "generated_count": 0,
                "approved_count": 0,
                "rejected_count": 0,
                "jobs": [],
                "errors": [],
                "accounts": [],
            }

        accounts = self._enabled_accounts(account_id)
        if not accounts:
            return {
                "status": "idle",
                "started_count": 0,
                "generated_count": 0,
                "approved_count": 0,
                "rejected_count": 0,
                "jobs": [],
                "errors": [],
                "accounts": [],
                "message": "No channels are set to auto_private or auto_public. Manual channels require an explicit production start.",
            }
        cost_guard = CostGuard(self.settings)
        production_estimate = cost_guard.estimate_production_job()
        safe_jobs = max(1, min(int(max_jobs or self.settings.shorts_autopilot_max_jobs_per_tick), 3))
        topic_generation_cost = float(getattr(self.settings, "atnos_estimated_topic_generation_batch_cost_pln", 0.0) or 0.0)
        cost_decision = cost_guard.check_can_spend(
            production_estimate.estimated_cost_pln * safe_jobs + topic_generation_cost,
            account_id=account_id,
            operation="autonomous_content_loop",
        )
        if not cost_decision["allowed"]:
            await self._emit(
                {
                    "type": "content_loop",
                    "status": "blocked",
                    "blocker": "cost_guard_budget_blocked",
                    "cost_guard": cost_decision,
                    "timestamp": utc_now_iso(),
                }
            )
            return {
                "status": "blocked",
                "started_count": 0,
                "generated_count": 0,
                "approved_count": 0,
                "rejected_count": 0,
                "jobs": [],
                "errors": [
                    {
                        "scope": "global",
                        "blocker": "cost_guard_budget_blocked",
                        "cost_guard": cost_decision,
                    }
                ],
                "accounts": [],
                "cost_guard": cost_decision,
            }
        account_results: list[dict[str, Any]] = []
        generated_total = 0
        approved_total = 0
        rejected_total = 0

        for account in accounts:
            result = await self._prepare_account_queue(account)
            account_results.append(result)
            generated_total += int(result.get("generated_count") or 0)
            approved_total += int(result.get("approved_count") or 0)
            rejected_total += int(result.get("rejected_count") or 0)

        production = await ProductionAutopilotService(
            self.settings,
            event_sink=self._emit,
            session_scope=self.session_scope,
        ).run_once(max_jobs=max_jobs, account_id=account_id)
        return {
            "status": "completed"
            if generated_total or approved_total or int(production.get("started_count") or 0)
            else production.get("status", "idle"),
            "generated_count": generated_total,
            "approved_count": approved_total,
            "rejected_count": rejected_total,
            "started_count": int(production.get("started_count") or 0),
            "jobs": production.get("jobs", []),
            "errors": production.get("errors", []),
            "accounts": account_results,
            "production": production,
            "cost_guard": cost_decision,
        }

    def _enabled_accounts(self, account_id: str | None) -> list[dict[str, Any]]:
        automated_modes = {"auto_private", "auto_public"}
        accounts = [
            item
            for item in read_intelligence_account_configs(self.settings)
            if item.get("enabled") and str(item.get("automation_mode") or "manual") in automated_modes
        ]
        if account_id:
            return [item for item in accounts if str(item.get("account_id")) == str(account_id)]
        return accounts

    async def _prepare_account_queue(self, account: dict[str, Any]) -> dict[str, Any]:
        account_id = str(account["account_id"])
        region = str(account.get("region") or "PL")
        before = self._queue_counts(account_id)
        generated_count = 0
        generated_rejected_count = 0
        if before["buffer_count"] < self.settings.shorts_autopilot_min_topic_buffer:
            generated = await TopicScorer(self.settings).generate_topics(
                account_id=account_id,
                count=self.settings.shorts_autopilot_generate_count,
                geo=region,
            )
            generated_result = self._store_generated_topics(account_id=account_id, topics=generated)
            generated_count = generated_result["stored_count"]
            generated_rejected_count = generated_result["rejected_count"]
            if generated_count:
                await self._emit(
                    {
                        "type": "content_loop",
                        "status": "topics_generated",
                        "account_id": account_id,
                        "count": generated_count,
                        "timestamp": utc_now_iso(),
                    }
                )

        moderation = self._moderate_pending_topics(account_id)
        best_effort = self._approve_best_viable_pending_topic(account_id)
        after = self._queue_counts(account_id)
        return {
            "account_id": account_id,
            "region": region,
            "before": before,
            "after": after,
            "generated_count": generated_count,
            "generated_rejected_count": generated_rejected_count,
            "approved_count": int(moderation["approved_count"]) + int(best_effort["approved_count"]),
            "strict_approved_count": int(moderation["approved_count"]),
            "best_effort_approved_count": int(best_effort["approved_count"]),
            "rejected_count": int(moderation["rejected_count"]) + generated_rejected_count,
            "best_effort_topic": best_effort.get("topic"),
        }

    def _queue_counts(self, account_id: str) -> dict[str, int]:
        with connect(self.settings) as db:
            rows = db.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM topic_queue
                WHERE account_id = ?
                GROUP BY status
                """,
                (account_id,),
            ).fetchall()
        counts = {str(row["status"]): int(row["count"]) for row in rows}
        return {
            "pending": counts.get("pending", 0),
            "approved": counts.get("approved", 0),
            "rejected": counts.get("rejected", 0),
            "in_production": counts.get("in_production", 0),
            "done": counts.get("done", 0),
            "buffer_count": counts.get("pending", 0) + counts.get("approved", 0),
        }

    def _store_generated_topics(self, *, account_id: str, topics: list[dict[str, Any]]) -> dict[str, int]:
        if not topics:
            return {"stored_count": 0, "rejected_count": 0}
        now = utc_now_iso()
        stored = 0
        rejected = 0
        with connect(self.settings) as db:
            for topic in topics:
                title = str(topic.get("title") or topic.get("topic") or "").strip()
                if not title:
                    continue
                dimensions = topic.get("dimensions") if isinstance(topic.get("dimensions"), dict) else {}
                flags = topic.get("flags") if isinstance(topic.get("flags"), dict) else {}
                topic_id = str(topic.get("id") or f"topic-{account_id}-{stored}-{now}")
                total_score = _float(topic.get("total_score"))
                status = "rejected" if total_score < self.settings.shorts_autopilot_auto_reject_below_score else "pending"
                cursor = db.execute(
                    """
                    INSERT INTO topic_queue (
                        id, account_id, title, description, inspired_by, status, total_score,
                        viral_potential, search_demand, competition_gap, trend_velocity,
                        niche_fit, freshness, clickbait_risk, evergreen, shorts_potential,
                        llm_unavailable, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO NOTHING
                    """,
                    (
                        topic_id,
                        account_id,
                        title,
                        str(topic.get("description") or "").strip(),
                        "autonomous_content_loop",
                        status,
                        total_score,
                        _float(dimensions.get("viral_potential")),
                        _float(dimensions.get("search_demand")),
                        _float(dimensions.get("competition_gap")),
                        _float(dimensions.get("trend_velocity")),
                        _float(dimensions.get("niche_fit")),
                        _float(dimensions.get("freshness")),
                        int(bool(flags.get("clickbait_risk"))),
                        int(bool(flags.get("evergreen"))),
                        int(bool(flags.get("shorts_potential"))),
                        int(bool(flags.get("llm_unavailable"))),
                        now,
                        now,
                    ),
                )
                inserted = int(cursor.rowcount == 1)
                stored += inserted
                if inserted and status == "rejected":
                    rejected += 1
            db.commit()
        return {"stored_count": stored, "rejected_count": rejected}

    def _moderate_pending_topics(self, account_id: str) -> dict[str, int]:
        now = utc_now_iso()
        approve_min = float(self.settings.shorts_autopilot_auto_approve_min_score)
        llm_unavailable_min = min(100.0, approve_min + 10.0)
        reject_below = float(self.settings.shorts_autopilot_auto_reject_below_score)
        approve_clauses = [
            "status = 'pending'",
            "COALESCE(total_score, 0) >= ?",
            "(COALESCE(llm_unavailable, 0) = 0 OR COALESCE(total_score, 0) >= ?)",
        ]
        approve_params: list[Any] = [approve_min, llm_unavailable_min]
        if self.settings.shorts_autopilot_require_shorts_potential:
            approve_clauses.append("COALESCE(shorts_potential, 0) = 1")
        if self.settings.shorts_autopilot_block_clickbait_risk:
            approve_clauses.append("COALESCE(clickbait_risk, 0) = 0")
        with connect(self.settings) as db:
            rejected = db.execute(
                """
                UPDATE topic_queue
                SET status = 'rejected', updated_at = ?
                WHERE account_id = ? AND status = 'pending' AND COALESCE(total_score, 0) < ?
                """,
                (now, account_id, reject_below),
            ).rowcount
            approved = db.execute(
                f"""
                UPDATE topic_queue
                SET status = 'approved', updated_at = ?, inspired_by = COALESCE(inspired_by, 'auto_approved')
                WHERE account_id = ? AND {" AND ".join(approve_clauses)}
                """,
                (now, account_id, *approve_params),
            ).rowcount
            db.commit()
        return {
            "approved_count": int(approved or 0),
            "rejected_count": int(rejected or 0),
        }

    def _approve_best_viable_pending_topic(self, account_id: str) -> dict[str, Any]:
        if not self.settings.shorts_autopilot_best_effort_approval_enabled:
            return {"approved_count": 0, "topic": None}
        with connect(self.settings) as db:
            approved = db.execute(
                "SELECT COUNT(*) AS count FROM topic_queue WHERE account_id = ? AND status = 'approved'",
                (account_id,),
            ).fetchone()
            if int(approved["count"] if approved else 0) > 0:
                return {"approved_count": 0, "topic": None}

            clauses = [
                "account_id = ?",
                "status = 'pending'",
                "COALESCE(total_score, 0) >= ?",
            ]
            params: list[Any] = [account_id, float(self.settings.shorts_autopilot_best_effort_min_score)]
            if self.settings.shorts_autopilot_require_shorts_potential:
                clauses.append("COALESCE(shorts_potential, 0) = 1")
            if self.settings.shorts_autopilot_block_clickbait_risk:
                clauses.append("COALESCE(clickbait_risk, 0) = 0")
            row = db.execute(
                f"""
                SELECT id, title, total_score, shorts_potential, clickbait_risk, llm_unavailable
                FROM topic_queue
                WHERE {" AND ".join(clauses)}
                ORDER BY COALESCE(total_score, 0) DESC, COALESCE(created_at, '') ASC
                LIMIT 1
                """,
                tuple(params),
            ).fetchone()
            if row is None:
                return {"approved_count": 0, "topic": None}
            now = utc_now_iso()
            db.execute(
                """
                UPDATE topic_queue
                SET status = 'approved',
                    updated_at = ?,
                    inspired_by = CASE
                        WHEN inspired_by IS NULL OR TRIM(inspired_by) = '' THEN 'best_viable_auto_approved'
                        WHEN instr(inspired_by, 'best_viable_auto_approved') > 0 THEN inspired_by
                        ELSE inspired_by || '|best_viable_auto_approved'
                    END
                WHERE id = ? AND status = 'pending'
                """,
                (now, row["id"]),
            )
            db.commit()
        return {
            "approved_count": 1,
            "topic": {
                "id": row["id"],
                "title": row["title"],
                "total_score": row["total_score"],
                "approval_mode": "best_viable_auto_approved",
                "llm_unavailable": bool(row["llm_unavailable"]),
            },
        }

    async def _emit(self, event: dict[str, Any]) -> None:
        if self.event_sink is None:
            return
        result = self.event_sink(event)
        if asyncio.iscoroutine(result):
            await result


def _float(value: object) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0
