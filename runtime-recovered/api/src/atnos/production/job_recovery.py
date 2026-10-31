from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from atnos.core.config import Settings
from atnos.intelligence.storage import connect, utc_now, utc_now_iso
from atnos.production.scene_checkpoint import SceneCheckpointStore

ACTIVE_JOB_STATUSES = ("queued", "running")


class ProductionJobRecoveryService:
    """Odzyskuje tematy z jobów produkcyjnych przerwanych przez crash lub timeout procesu."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def recover_stale_jobs(
        self,
        *,
        stale_after_minutes: int | None = None,
        account_id: str | None = None,
        resume_video_jobs: bool = False,
        recover_all_active: bool = False,
    ) -> dict[str, Any]:
        """Zamień stare aktywne joby na error i przywróć ich tematy do kolejki approved."""
        minutes = max(1, int(stale_after_minutes or self.settings.shorts_stale_job_after_minutes))
        cutoff = (utc_now() - timedelta(minutes=minutes)).isoformat()
        now = utc_now_iso()
        account_filter = ""
        time_filter = "" if recover_all_active else "AND COALESCE(updated_at, created_at, '') <= ?"
        params: list[Any] = [] if recover_all_active else [cutoff]
        if account_id:
            account_filter = "AND account_id = ?"
            params.append(str(account_id))

        with connect(self.settings) as db:
            rows = db.execute(
                f"""
                SELECT
                    id, account_id, topic_id, status, current_step, artifact_dir,
                    audio_timestamps, asset_plan, updated_at, created_at
                FROM pipeline_jobs
                WHERE status IN ('queued', 'running')
                  AND NOT EXISTS (SELECT 1 FROM pipeline_job_orders o WHERE o.job_id = pipeline_jobs.id)
                  {time_filter}
                  {account_filter}
                ORDER BY COALESCE(updated_at, created_at, '') ASC
                """,
                tuple(params),
            ).fetchall()

            recovered_jobs: list[dict[str, Any]] = []
            resume_job_ids: list[str] = []
            restored_topic_count = 0
            for row in rows:
                job_id = str(row["id"])
                topic_id = str(row["topic_id"] or "")
                current_step = str(row["current_step"] or "unknown")
                previous_status = str(row["status"] or "")
                resume_eligibility = _video_resume_eligibility(row)
                if resume_video_jobs and resume_eligibility["eligible"]:
                    message = (
                        f"Queued resumable production job after {minutes} minutes without heartbeat. "
                        f"Previous status={previous_status}; step={current_step}."
                    )
                    db.execute(
                        """
                        UPDATE pipeline_jobs
                        SET status = 'queued',
                            current_step = 'video',
                            error = NULL,
                            review_status = 'resume_queued',
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (now, job_id),
                    )
                    db.execute(
                        """
                        UPDATE pipeline_job_steps
                        SET status = 'queued',
                            message = ?,
                            completed_at = NULL
                        WHERE job_id = ? AND step = 'video'
                        """,
                        (message, job_id),
                    )
                    resume_job_ids.append(job_id)
                    recovered_jobs.append(
                        {
                            "job_id": job_id,
                            "account_id": str(row["account_id"] or ""),
                            "topic_id": topic_id,
                            "previous_status": previous_status,
                            "previous_step": current_step,
                            "previous_updated_at": row["updated_at"],
                            "topic_restored_to_approved": False,
                            "resume_queued": True,
                            "resume_reason": resume_eligibility["reason"],
                            "message": message,
                        }
                    )
                    continue
                message = (
                    f"Recovered stale production job after {minutes} minutes without heartbeat. "
                    f"Previous status={previous_status}; step={current_step}."
                )
                db.execute(
                    """
                    UPDATE pipeline_jobs
                    SET status = 'error',
                        error = ?,
                        review_status = 'stale_recovered',
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (message, now, job_id),
                )
                db.execute(
                    """
                    UPDATE pipeline_job_steps
                    SET status = 'error',
                        message = ?,
                        completed_at = ?
                    WHERE job_id = ? AND status IN ('queued', 'running')
                    """,
                    (message, now, job_id),
                )
                if current_step not in {"", "unknown", "queued"}:
                    db.execute(
                        """
                        INSERT INTO pipeline_job_steps (
                            job_id, step, status, progress, attempt, message, started_at, completed_at
                        )
                        VALUES (?, ?, 'error', 0, 0, ?, ?, ?)
                        ON CONFLICT(job_id, step) DO UPDATE SET
                            status='error',
                            message=excluded.message,
                            completed_at=excluded.completed_at
                        """,
                        (job_id, current_step, message, now, now),
                    )
                topic_restored = False
                if topic_id:
                    cursor = db.execute(
                        """
                        UPDATE topic_queue
                        SET status = 'approved', updated_at = ?
                        WHERE id = ?
                          AND status = 'in_production'
                          AND NOT EXISTS (
                              SELECT 1
                              FROM pipeline_jobs active
                              WHERE active.topic_id = topic_queue.id
                                AND active.status IN ('queued', 'running')
                          )
                        """,
                        (now, topic_id),
                    )
                    topic_restored = cursor.rowcount > 0
                    restored_topic_count += int(topic_restored)

                recovered_jobs.append(
                    {
                        "job_id": job_id,
                        "account_id": str(row["account_id"] or ""),
                        "topic_id": topic_id,
                        "previous_status": previous_status,
                        "previous_step": current_step,
                        "previous_updated_at": row["updated_at"],
                        "topic_restored_to_approved": topic_restored,
                        "resume_queued": False,
                        "resume_reason": resume_eligibility["reason"],
                        "message": message,
                    }
                )

            db.commit()

        return {
            "status": "completed",
            "stale_after_minutes": minutes,
            "cutoff": None if recover_all_active else cutoff,
            "recover_all_active": recover_all_active,
            "recovered_count": len(recovered_jobs),
            "resume_queued_count": len(resume_job_ids),
            "resume_job_ids": resume_job_ids,
            "restored_topic_count": restored_topic_count,
            "jobs": recovered_jobs,
        }


def _video_resume_eligibility(row: Any) -> dict[str, Any]:
    if str(row["current_step"] or "") != "video":
        return {"eligible": False, "reason": "current_step_is_not_video"}
    artifact_dir = Path(str(row["artifact_dir"] or ""))
    if not artifact_dir.is_dir():
        return {"eligible": False, "reason": "artifact_directory_missing"}
    voice = _json_object(row["audio_timestamps"])
    narration_path = Path(str(voice.get("output_file") or ""))
    if not narration_path.is_file() or narration_path.stat().st_size <= 0:
        return {"eligible": False, "reason": "reusable_narration_missing"}
    visuals = _json_object(row["asset_plan"])
    scenes = visuals.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        return {"eligible": False, "reason": "asset_plan_scenes_missing"}
    checkpoint_path = artifact_dir / "video" / "scene_checkpoint.json"
    if not checkpoint_path.is_file():
        return {"eligible": False, "reason": "scene_checkpoint_missing"}
    try:
        store = SceneCheckpointStore(
            checkpoint_path,
            job_id=str(row["id"]),
            scene_ids=[f"scene_{index:02d}" for index in range(1, len(scenes) + 1)],
        )
    except (OSError, ValueError):
        return {"eligible": False, "reason": "scene_checkpoint_invalid"}
    statuses = [scene.status for scene in store.manifest.scenes]
    if not any(status in {"RUNNING", "DONE", "CACHED", "FAILED"} for status in statuses):
        return {"eligible": False, "reason": "scene_checkpoint_has_no_progress"}
    return {"eligible": True, "reason": "durable_video_checkpoint_and_inputs_verified"}


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
