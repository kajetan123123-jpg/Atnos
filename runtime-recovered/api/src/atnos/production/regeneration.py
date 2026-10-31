from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from atnos.core.config import Settings
from atnos.intelligence.storage import connect, utc_now_iso
from atnos.production.models import ProductionContext
from atnos.production.orchestrator import ProductionOrchestrator

EventSink = Callable[[dict[str, Any]], None | Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RegenerationPlan:
    """Opis nowej, bezpiecznie zakolejkowanej proby produkcji."""

    source_job_id: str
    context: ProductionContext
    orchestrator: ProductionOrchestrator

    def to_payload(self, *, reason: str) -> dict[str, Any]:
        return {
            "status": "queued",
            "source_job_id": self.source_job_id,
            "job_id": self.context.job_id,
            "account_id": self.context.account_id,
            "topic_id": self.context.topic.id,
            "reason": reason,
            "message": "Regeneration job queued. No upload or public publication was executed.",
        }


async def prepare_regeneration_job(
    settings: Settings,
    *,
    source_job_id: str,
    reason: str = "manual_recovery",
    event_sink: EventSink | None = None,
) -> RegenerationPlan:
    """Utworz nowy job dla tego samego tematu bez uploadu i bez publikacji."""
    account_id, topic_id = _load_regeneration_source(settings, source_job_id)
    orchestrator = ProductionOrchestrator(settings, event_sink=event_sink)
    context = await orchestrator.prepare(account_id=account_id, topic_id=topic_id)
    _mark_regeneration_source(settings, source_job_id, context.job_id, reason)
    return RegenerationPlan(source_job_id=source_job_id, context=context, orchestrator=orchestrator)


def _load_regeneration_source(settings: Settings, source_job_id: str) -> tuple[str, str]:
    with connect(settings) as db:
        source = db.execute(
            """
            SELECT id, account_id, topic_id, status
            FROM pipeline_jobs
            WHERE id = ?
            """,
            (source_job_id,),
        ).fetchone()
        if source is None:
            raise ValueError(f"Job {source_job_id} was not found.")
        active = db.execute(
            """
            SELECT id
            FROM pipeline_jobs
            WHERE account_id = ?
              AND topic_id = ?
              AND status IN ('queued', 'running')
              AND id <> ?
            LIMIT 1
            """,
            (source["account_id"], source["topic_id"], source_job_id),
        ).fetchone()
        if active is not None:
            raise RuntimeError(f"A production job for this topic is already active: {active['id']}")
        db.execute(
            "UPDATE topic_queue SET status = ?, updated_at = ? WHERE id = ?",
            ("approved", utc_now_iso(), source["topic_id"]),
        )
        db.commit()
        return str(source["account_id"]), str(source["topic_id"])


def _mark_regeneration_source(settings: Settings, source_job_id: str, new_job_id: str, reason: str) -> None:
    with connect(settings) as db:
        db.execute(
            """
            UPDATE pipeline_jobs
            SET review_status = ?, updated_at = ?
            WHERE id = ?
            """,
            (f"regeneration_queued:{new_job_id}:{reason[:80]}", utc_now_iso(), source_job_id),
        )
        db.commit()
