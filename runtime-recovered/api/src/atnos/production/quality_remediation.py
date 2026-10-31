from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from atnos.core.config import Settings
from atnos.intelligence.storage import connect, json_loads, rows_to_dicts, utc_now_iso
from atnos.production.quality_scheduler import QUALITY_REVIEW_BLOCKED
from atnos.production.regeneration import prepare_regeneration_job

EventSink = Callable[[dict[str, Any]], None | Awaitable[None]]


class ProductionQualityRemediationService:
    """Automatycznie kolejkuje bezpieczne regeneracje dla jobów zablokowanych przez jakość."""

    def __init__(self, settings: Settings, *, event_sink: EventSink | None = None) -> None:
        self.settings = settings
        self.event_sink = event_sink

    def status(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.settings.shorts_quality_auto_remediation_enabled),
            "run_jobs": bool(self.settings.shorts_quality_auto_remediation_run_jobs),
            "interval_minutes": int(self.settings.shorts_quality_auto_remediation_interval_minutes),
            "max_jobs_per_tick": int(self.settings.shorts_quality_auto_remediation_max_jobs_per_tick),
            "candidate_count": self._candidate_count(),
        }

    async def run_due(self, *, limit: int | None = None) -> dict[str, Any]:
        if not self.settings.shorts_quality_auto_remediation_enabled:
            return {"status": "disabled", "planned_count": 0, "started_count": 0, "results": []}
        safe_limit = max(1, min(int(limit or self.settings.shorts_quality_auto_remediation_max_jobs_per_tick), 3))
        candidates = self._select_candidates(limit=safe_limit)
        results: list[dict[str, Any]] = []
        for candidate in candidates:
            results.append(await self._remediate(candidate))
        return {
            "status": "completed" if results else "idle",
            "planned_count": len(candidates),
            "started_count": sum(1 for item in results if item.get("status") in {"queued", "started", "completed"}),
            "results": results,
        }

    async def _remediate(self, job: dict[str, Any]) -> dict[str, Any]:
        job_id = str(job.get("id") or "")
        result_payload = _json_field(job.get("quality_review_result"), {})
        reason = remediation_reason(result_payload)
        try:
            plan = await prepare_regeneration_job(
                self.settings,
                source_job_id=job_id,
                reason=reason,
                event_sink=self._emit,
            )
        except Exception as exc:
            return {
                "status": "error",
                "source_job_id": job_id,
                "reason": reason,
                "message": f"{type(exc).__name__}: {exc}",
            }

        event = {
            "type": "pipeline_regeneration",
            "source_job_id": plan.source_job_id,
            "job_id": plan.context.job_id,
            "account_id": plan.context.account_id,
            "topic_id": plan.context.topic.id,
            "status": "queued",
            "reason": reason,
            "message": "quality auto-remediation regeneration queued",
            "timestamp": utc_now_iso(),
        }
        await self._emit(event)
        payload = plan.to_payload(reason=reason)
        payload["remediation"] = {
            "reason": reason,
            "source_blockers": list(result_payload.get("blockers") or []),
            "next_action": result_payload.get("next_action"),
            "run_jobs": bool(self.settings.shorts_quality_auto_remediation_run_jobs),
        }
        if self.settings.shorts_quality_auto_remediation_run_jobs:
            try:
                run_result = await plan.orchestrator.run_job(plan.context)
            except Exception as exc:
                payload["status"] = "error"
                payload["run_error"] = f"{type(exc).__name__}: {exc}"
                return payload
            payload["status"] = "completed"
            payload["run_result"] = run_result
        return payload

    def _select_candidates(self, *, limit: int) -> list[dict[str, Any]]:
        with connect(self.settings) as db:
            rows = db.execute(
                """
                SELECT j.*
                FROM pipeline_jobs j
                WHERE j.quality_review_status = ?
                  AND COALESCE(j.review_status, '') = ?
                  AND COALESCE(j.quality_review_result, '') <> ''
                  AND NOT EXISTS (
                    SELECT 1
                    FROM pipeline_jobs active
                    WHERE active.account_id = j.account_id
                      AND active.topic_id = j.topic_id
                      AND active.id <> j.id
                      AND active.status IN ('queued', 'running')
                  )
                ORDER BY COALESCE(j.quality_review_checked_at, j.updated_at, j.created_at, '') ASC
                LIMIT ?
                """,
                (QUALITY_REVIEW_BLOCKED, QUALITY_REVIEW_BLOCKED, limit),
            ).fetchall()
        return rows_to_dicts(rows)

    def _candidate_count(self) -> int:
        with connect(self.settings) as db:
            row = db.execute(
                """
                SELECT COUNT(*) AS count
                FROM pipeline_jobs
                WHERE quality_review_status = ?
                  AND COALESCE(review_status, '') = ?
                  AND COALESCE(quality_review_result, '') <> ''
                """,
                (QUALITY_REVIEW_BLOCKED, QUALITY_REVIEW_BLOCKED),
            ).fetchone()
        return int(row["count"] if row is not None else 0)

    async def _emit(self, event: dict[str, Any]) -> None:
        if self.event_sink is None:
            return
        result = self.event_sink(event)
        if asyncio.iscoroutine(result):
            await result


def remediation_reason(quality_review_result: dict[str, Any]) -> str:
    """Sklasyfikuj blokery jakości do stabilnego powodu regeneracji."""
    blockers = [str(item) for item in quality_review_result.get("blockers") or []]
    blocker_text = " ".join(blockers).casefold()
    if "premium_voice" in blocker_text:
        return "quality_auto_remediation:premium_voice_required"
    if "semantic_first_frame" in blocker_text:
        return "quality_auto_remediation:first_frame_semantic_failed"
    if "semantic_visual" in blocker_text:
        return "quality_auto_remediation:thumbnail_semantic_failed"
    if "thumbnail_quality" in blocker_text:
        return "quality_auto_remediation:thumbnail_quality_failed"
    if "scene_concreteness" in blocker_text or "topic_anchor" in blocker_text or "visual_relevance" in blocker_text:
        return "quality_auto_remediation:visual_plan_failed"
    if "generated_image" in blocker_text:
        return "quality_auto_remediation:generated_image_audit_failed"
    if "style_guide" in blocker_text:
        return "quality_auto_remediation:style_guide_missing"
    if "premium_visual_backend" in blocker_text:
        return "quality_auto_remediation:premium_visual_backend_required"
    return "quality_auto_remediation:public_quality_failed"


def _json_field(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    return json_loads(str(value or ""), default)
