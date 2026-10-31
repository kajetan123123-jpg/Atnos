"""Mark production orders whose execution worker disappeared as interrupted.

The order, its stage checkpoints, and its budget counters remain authoritative.
Recovery only releases a dead process lease; dispatch remains an explicit action.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
import re

from atnos.intelligence.storage import connect, json_dumps, utc_now_iso
from atnos.production.order_store import _event, _same_process


def _interrupted_error(step: str, artifact_dir: str) -> dict:
    # A dispatched one-shot generator is not retryable just because its parent
    # died. Preserve its outcome as unknown until normal evidence reconciliation.
    match = re.fullmatch(r"assets/image_local/flux2-trial/[^/]+/([0-9a-f]{16})", step)
    if match and (Path(artifact_dir) / "assets/image_local/flux_trial" / match[1] / "dispatch.json").is_file():
        return {
            "category": "execution_outcome_unknown",
            "message": "Worker został przerwany po wysłaniu generacji. Wynik wymaga sprawdzenia; automatyczne ponowienie jest zablokowane.",
            "dispatch_preserved": True,
        }
    return {
        "category": "worker_interrupted",
        "message": "Proces produkcji został przerwany. Przed wznowieniem sprawdź wynik ostatniej operacji; zachowane etapy pozostają dostępne.",
    }


def recover_interrupted_orders(settings, *, job_ids: Sequence[str] | None = None) -> dict[str, object]:
    """Reconcile all orders at startup or at most 100 requested order IDs on read."""
    selected_ids = tuple(dict.fromkeys(job_ids)) if job_ids is not None else None
    if selected_ids is not None and len(selected_ids) > 100:
        raise ValueError("On-demand order recovery is limited to 100 IDs")
    recovered: list[str] = []
    preserved_live = 0
    if selected_ids == ():
        return {"recovered_count": 0, "recovered_job_ids": [], "preserved_live_count": 0}
    order_filter = " AND j.id IN (" + ",".join("?" for _ in selected_ids) + ")" if selected_ids is not None else ""
    params = selected_ids or ()
    recoverable_status = "(j.status = 'running' OR (j.status = 'queued' AND j.current_step = 'worker_starting'))"
    query = (
        "SELECT j.id, j.current_step, j.artifact_dir, o.lease_pid, o.lease_process_started "
        "FROM pipeline_jobs AS j JOIN pipeline_job_orders AS o ON o.job_id = j.id "
        # A graceful lease cleanup can run even when its in-flight stage never
        # reached a terminal state. Running jobs without a PID are interrupted
        # too; ordinary queued jobs awaiting dispatch must remain untouched.
        "WHERE " + recoverable_status + " AND (o.lease_pid IS NOT NULL OR j.status='running')" + order_filter
    )
    with connect(settings) as db:
        # Most polls see no dead worker and need no SQLite write lock.
        candidates = db.execute(query, params).fetchall()
        dead_ids = [row["id"] for row in candidates if not _same_process(row["lease_pid"], row["lease_process_started"])]
        preserved_live = len(candidates) - len(dead_ids)
        if not dead_ids:
            return {"recovered_count": 0, "recovered_job_ids": [],
                    "preserved_live_count": preserved_live}

        # Recheck under the same write lock used by lease acquisition. A worker
        # could have completed or claimed the order after the first observation.
        db.execute("BEGIN IMMEDIATE")
        pending_query = (
            "SELECT j.id, j.current_step, j.artifact_dir, o.lease_pid, o.lease_process_started "
            "FROM pipeline_jobs AS j JOIN pipeline_job_orders AS o ON o.job_id = j.id "
            "WHERE " + recoverable_status + " AND (o.lease_pid IS NOT NULL OR j.status='running') AND j.id IN ("
            + ",".join("?" for _ in dead_ids) + ")"
        )
        for row in db.execute(pending_query, tuple(dead_ids)).fetchall():
            if _same_process(row["lease_pid"], row["lease_process_started"]):
                preserved_live += 1
                continue
            job_id = str(row["id"])
            step = str(row["current_step"] or "unknown")
            detail = _interrupted_error(step, row["artifact_dir"])
            error = json_dumps(detail)
            db.execute(
                "UPDATE pipeline_jobs SET status='blocked', error=?, updated_at=? WHERE id=?",
                (error, utc_now_iso(), job_id),
            )
            db.execute(
                """UPDATE pipeline_job_orders
                   SET lease_token=NULL, lease_pid=NULL, lease_process_started=NULL
                   WHERE job_id=?""",
                (job_id,),
            )
            _event(db, job_id, step, "worker_interrupted", {"previous_step": step, "outcome_category": detail["category"]})
            recovered.append(job_id)
        db.commit()
    return {"recovered_count": len(recovered), "recovered_job_ids": recovered,
            "preserved_live_count": preserved_live}
