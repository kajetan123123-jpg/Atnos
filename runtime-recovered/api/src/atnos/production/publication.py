from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.intelligence.storage import connect, json_dumps, utc_midnight_tomorrow_iso, utc_now_iso
from atnos.production.private_upload import (
    _build_youtube_connector,
    _missing_youtube_scopes,
    _youtube_oauth_account_id,
)
from atnos.production.publication_quality import public_quality_blockers
from atnos.services.slack_webhook_notifier import SlackWebhookNotifier
from atnos.services.youtube_connector_service import YouTubeConnectorError, YouTubeConnectorService, YouTubeQuotaExceededError


class ProductionPublicationError(RuntimeError):
    pass


PUBLIC_PROMOTION_READY_STATUSES = {
    "uploaded_private",
    "public_publish_failed",
    "public_publish_connection_required",
    "public_publish_quota_exceeded",
}


def promote_private_job_to_public(
    *,
    settings: Settings,
    session: Session,
    job_id: str,
    approved: bool,
    publish_at: str | None = None,
) -> dict[str, Any]:
    """Promote a reviewed private YouTube upload to scheduled-public or public."""
    if not approved:
        raise ProductionPublicationError("Explicit approval is required before public publication.")
    if not settings.allow_public_upload:
        raise ProductionPublicationError("ALLOW_PUBLIC_UPLOAD is disabled.")
    if settings.atnos_require_approval_for_publication and not approved:
        raise ProductionPublicationError("Publication approval is required by policy.")

    job = _load_job(settings, job_id)
    status = str(job.get("status") or "")
    if status not in PUBLIC_PROMOTION_READY_STATUSES:
        raise ProductionPublicationError(f"Job {job_id} is not ready for public promotion.")
    youtube_video_id = str(job.get("youtube_video_id") or "").strip()
    if not youtube_video_id:
        raise ProductionPublicationError(f"Job {job_id} has no youtube_video_id.")
    quality_blockers = public_quality_blockers(settings, job)
    if quality_blockers:
        raise ProductionPublicationError(
            f"Job {job_id} is blocked by public quality gate: {', '.join(quality_blockers)}."
        )

    youtube = _build_youtube_connector(
        session,
        settings,
        account_id=_youtube_oauth_account_id(settings, str(job["account_id"]), session=session),
    )
    connection_result = _youtube_connection_required_result(youtube, job_id=job_id, youtube_video_id=youtube_video_id)
    if connection_result is not None:
        _persist_publication_result(settings, job, connection_result, status="public_publish_connection_required")
        _notify_publication(settings, connection_result)
        return connection_result

    try:
        publish_result = youtube.promote_private_video(
            video_id=youtube_video_id,
            approved=approved,
            publish_at=publish_at,
        )
    except YouTubeQuotaExceededError as exc:
        result = {
            "status": "quota_exceeded",
            "job_id": job_id,
            "youtube_video_id": youtube_video_id,
            "youtube_url": f"https://www.youtube.com/watch?v={youtube_video_id}",
            "retry_after": utc_midnight_tomorrow_iso(),
            "message": str(exc),
        }
        _persist_publication_result(settings, job, result, status="public_publish_quota_exceeded")
        _notify_publication(settings, result)
        return result
    except YouTubeConnectorError as exc:
        result = {
            "status": "error",
            "job_id": job_id,
            "youtube_video_id": youtube_video_id,
            "youtube_url": f"https://www.youtube.com/watch?v={youtube_video_id}",
            "message": str(exc),
        }
        _persist_publication_result(settings, job, result, status="public_publish_failed")
        _notify_publication(settings, result)
        return result

    result = {
        "job_id": job_id,
        "approved": True,
        **publish_result,
    }
    target_status = "scheduled_public" if str(result.get("status")) == "scheduled_public" else "published_public"
    _persist_publication_result(settings, job, result, status=target_status)
    _notify_publication(settings, result)
    return result


def public_promotion_blockers(settings: Settings, payload: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if not settings.allow_public_upload:
        blockers.append("public_upload_disabled")
    if str(payload.get("status") or "") not in PUBLIC_PROMOTION_READY_STATUSES:
        blockers.append("private_upload_not_ready")
    if not str(payload.get("youtube_video_id") or "").strip():
        blockers.append("youtube_video_id_missing")
    upload_result = payload.get("upload_result")
    if isinstance(upload_result, dict) and str(upload_result.get("visibility") or "") != "private":
        blockers.append("source_upload_not_private")
    blockers.extend(public_quality_blockers(settings, payload))
    return list(dict.fromkeys(blockers))


def _load_job(settings: Settings, job_id: str) -> dict[str, Any]:
    with connect(settings) as db:
        row = db.execute("SELECT * FROM pipeline_jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise ProductionPublicationError(f"Job {job_id} was not found.")
    return dict(row)


def _youtube_connection_required_result(
    youtube: YouTubeConnectorService,
    *,
    job_id: str,
    youtube_video_id: str,
) -> dict[str, Any] | None:
    status = youtube.get_status()
    if status.ready:
        return None
    evidence = status.to_dict()
    missing_scopes = _missing_youtube_scopes(evidence)
    next_steps = ["Open GET /oauth/youtube-studio/status for full OAuth readiness diagnostics."]
    if not evidence.get("configured"):
        next_steps.append("Import a real Google client_secret.json via POST /oauth/youtube-studio/connect.")
    if not evidence.get("connected"):
        next_steps.append("Run POST /oauth/youtube-studio/start and complete Google consent in the browser.")
    if missing_scopes:
        next_steps.append(f"Reconnect YouTube Studio with missing scopes: {', '.join(missing_scopes)}.")
    return {
        "status": "connection_required",
        "job_id": job_id,
        "youtube_video_id": youtube_video_id,
        "youtube_url": f"https://www.youtube.com/watch?v={youtube_video_id}",
        "message": "YouTube Studio OAuth is not ready for public promotion.",
        "oauth": {
            "ready": False,
            "connector_key": evidence.get("connector_key"),
            "configured": evidence.get("configured"),
            "connected": evidence.get("connected"),
            "warnings": evidence.get("warnings") or [],
            "granted_scopes": evidence.get("granted_scopes") or [],
            "missing_scopes": missing_scopes,
            "readiness_endpoint": "GET /oauth/youtube-studio/status",
            "connect_endpoint": "POST /oauth/youtube-studio/connect",
            "start_endpoint": "POST /oauth/youtube-studio/start",
            "next_steps": next_steps,
        },
    }


def _persist_publication_result(settings: Settings, job: dict[str, Any], result: dict[str, Any], *, status: str) -> None:
    now = utc_now_iso()
    scheduled_at = result.get("publish_at") if status == "scheduled_public" else None
    published_at = now if status == "published_public" else None
    with connect(settings) as db:
        db.execute(
            """
            UPDATE pipeline_jobs
            SET status = ?,
                current_step = 'public_publish',
                public_publish_result = ?,
                public_scheduled_at = COALESCE(?, public_scheduled_at),
                public_published_at = COALESCE(?, public_published_at),
                updated_at = ?
            WHERE id = ?
            """,
            (status, json_dumps(result), scheduled_at, published_at, now, job["id"]),
        )
        if status in {"scheduled_public", "published_public"}:
            db.execute(
                """
                UPDATE topic_queue
                SET status = 'done',
                    updated_at = ?
                WHERE id = ?
                """,
                (now, job.get("topic_id")),
            )
        db.commit()


def _notify_publication(settings: Settings, result: dict[str, Any]) -> None:
    status = str(result.get("status") or "updated")
    message = str(result.get("message") or result.get("publish_at") or result.get("youtube_url") or "public promotion updated")
    event = {
        "type": "pipeline_step",
        "job_id": str(result.get("job_id") or ""),
        "step": "public_publish",
        "status": status,
        "progress": 1.0 if status in {"scheduled_public", "published_public"} else 0.92,
        "message": message,
        "timestamp": utc_now_iso(),
    }
    SlackWebhookNotifier(settings).notify_pipeline_event(event)
