from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.intelligence.storage import (
    connect,
    json_dumps,
    json_loads,
    utc_midnight_tomorrow_iso,
    utc_now,
    utc_now_iso,
)
from atnos.memory.obsidian_writer import write_analytics_learning_note
from atnos.production.owner_review_notifications import dispatch_private_upload_link_email
from atnos.production.oauth_resolver import resolve_youtube_oauth_account_id
from atnos.production.quality_scheduler import ProductionQualityScheduler, QUALITY_REVIEW_BLOCKED
from atnos.production.qa_gate import REQUIRED_QA_CHECK_NAMES
from atnos.services.slack_webhook_notifier import SlackWebhookNotifier
from atnos.services.youtube_connector_service import YouTubeConnectorError, YouTubeConnectorService, YouTubeQuotaExceededError
from youtube.upload_guard import assert_upload_video_file


FOLLOWUP_WINDOWS_HOURS = (24, 48, 72)
FOLLOWUP_RETRY_DELAY_HOURS = 1


class ProductionUploadError(RuntimeError):
    pass


def upload_private_from_package(
    *,
    settings: Settings,
    session: Session,
    job_id: str,
) -> dict[str, Any]:
    """Upload a QA-approved production job as a private YouTube video only."""
    job = _load_job(settings, job_id)
    if job["status"] not in {"qa_passed", "upload_connection_required", "private_upload_failed"}:
        raise ProductionUploadError(f"Job {job_id} is not ready for private upload.")
    if str(job.get("quality_review_status") or "") == QUALITY_REVIEW_BLOCKED:
        raise ProductionUploadError("Production quality review blocks private upload. Regenerate visuals/audio first.")
    quality = ProductionQualityScheduler(settings).audit_job(job_id, job=job)
    if quality.get("status") == "blocked":
        blockers = ", ".join(str(item) for item in (quality.get("blockers") or [])[:5])
        raise ProductionUploadError(f"Production quality review blocks private upload: {blockers}")
    package_path = Path(str(job.get("upload_package_path") or ""))
    if not package_path.exists():
        raise ProductionUploadError(f"Upload package for job {job_id} was not found.")

    package = json_loads(package_path.read_text(encoding="utf-8"), {})
    metadata = package.get("upload_metadata") if isinstance(package, dict) else None
    if not isinstance(metadata, dict):
        raise ProductionUploadError("Upload package has no upload_metadata.")
    visibility = str(metadata.get("visibility") or "private").strip().lower()
    if visibility != "private":
        raise ProductionUploadError("Production upload endpoint only allows private visibility.")
    if not bool(package.get("auto_uploadable")):
        raise ProductionUploadError("QA did not mark this package as auto_uploadable.")
    qa_report = _load_authoritative_qa_report(job=job, package=package)
    _validate_qa_report_for_private_upload(qa_report)

    video_path = Path(str(package.get("video_file_path") or ""))
    try:
        video_path = assert_upload_video_file(video_path, settings=settings, require_run_artifacts=False)
    except ValueError as exc:
        raise ProductionUploadError(str(exc)) from exc
    thumbnail_path = Path(str(package.get("thumbnail_file_path") or ""))
    if not thumbnail_path.exists():
        thumbnail_path = None
    subtitle_path = _existing_file_path(job.get("subtitle_path")) or _existing_file_path(package.get("subtitle_file_path"))
    captions_path = subtitle_path if subtitle_path is not None and subtitle_path.suffix.lower() == ".srt" else None

    youtube = _build_youtube_connector(
        session,
        settings,
        account_id=_youtube_oauth_account_id(settings, str(job["account_id"]), session=session),
    )
    connection_result = _youtube_connection_required_result(youtube, job_id=job_id)
    if connection_result is not None:
        _persist_upload_result(settings, job, connection_result, status="upload_connection_required")
        _notify_private_upload(settings, connection_result, session=session, job=job)
        return connection_result
    try:
        language = str(metadata.get("language") or metadata.get("default_language") or "").strip() or None
        audio_language = (
            str(metadata.get("audio_language") or metadata.get("default_audio_language") or language or "").strip() or None
        )
        upload_result = youtube.upload_short(
            video_path=video_path,
            title=str(metadata.get("title") or job.get("topic_id") or "Atnos Short")[:100],
            description=str(metadata.get("description") or "")[:5000],
            tags=[str(tag) for tag in (metadata.get("tags") or [])][:15],
            thumbnail_path=thumbnail_path,
            captions_path=captions_path,
            privacy_status="private",
            made_for_kids=bool(metadata.get("made_for_kids", False)),
            default_language=language,
            default_audio_language=audio_language,
        )
    except YouTubeQuotaExceededError as exc:
        retry_after = utc_midnight_tomorrow_iso()
        result = {
            "status": "quota_exceeded",
            "job_id": job_id,
            "message": str(exc),
            "visibility": "private",
            "retry_after": retry_after,
        }
        _persist_upload_result(settings, job, result, status="private_upload_quota_exceeded")
        _notify_private_upload(settings, result, session=session, job=job)
        return result
    except YouTubeConnectorError as exc:
        result = {
            "status": "connection_required",
            "job_id": job_id,
            "message": str(exc),
            "visibility": "private",
        }
        _persist_upload_result(settings, job, result, status="upload_connection_required")
        _notify_private_upload(settings, result, session=session, job=job)
        return result
    except Exception as exc:
        result = {
            "status": "error",
            "job_id": job_id,
            "message": str(exc),
            "visibility": "private",
        }
        _persist_upload_result(settings, job, result, status="private_upload_failed")
        _notify_private_upload(settings, result, session=session, job=job)
        return result

    video_id = str(upload_result.get("video_id") or "").strip()
    if not video_id:
        raise ProductionUploadError("YouTube upload did not return video_id.")
    youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    result = {
        "status": "uploaded_private",
        "job_id": job_id,
        "youtube_video_id": video_id,
        "youtube_url": youtube_url,
        "visibility": "private",
        "upload": upload_result,
    }
    _persist_upload_result(settings, job, result, status="uploaded_private")
    followups = schedule_analytics_followups(settings=settings, job=job, youtube_video_id=video_id)
    result["analytics_followups"] = followups
    _persist_followup_plan(settings, job_id, followups)
    _notify_private_upload(settings, result, session=session, job=job)
    return result


def _youtube_connection_required_result(youtube: YouTubeConnectorService, *, job_id: str) -> dict[str, Any] | None:
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
        "message": "YouTube Studio OAuth is not ready for private upload.",
        "visibility": "private",
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


def _missing_youtube_scopes(status_payload: dict[str, Any]) -> list[str]:
    required = {
        "openid",
        "email",
        "profile",
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.readonly",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
    }
    granted = {_normalize_oauth_scope(str(scope)) for scope in (status_payload.get("granted_scopes") or [])}
    if not bool(status_payload.get("connected")):
        return sorted(required)
    return sorted(scope for scope in required if _normalize_oauth_scope(scope) not in granted)


def _normalize_oauth_scope(scope: str) -> str:
    normalized = str(scope or "").strip()
    aliases = {
        "https://www.googleapis.com/auth/userinfo.email": "email",
        "https://www.googleapis.com/auth/userinfo.profile": "profile",
    }
    return aliases.get(normalized, normalized)


def _youtube_oauth_account_id(settings: Settings, account_id: str, *, session: Session | None = None) -> int | None:
    return resolve_youtube_oauth_account_id(settings, account_id, session=session)


def _build_youtube_connector(session: Session, settings: Settings, *, account_id: int | None) -> Any:
    try:
        return YouTubeConnectorService(session, settings, account_id=account_id)
    except TypeError:
        # Keeps existing tests/legacy connector shims compatible while real connector gets account_id.
        return YouTubeConnectorService(session, settings)


def schedule_analytics_followups(
    *,
    settings: Settings,
    job: dict[str, Any],
    youtube_video_id: str,
) -> list[dict[str, Any]]:
    now = utc_now()
    created_at = utc_now_iso()
    rows: list[dict[str, Any]] = []
    with connect(settings) as db:
        for window_hours in FOLLOWUP_WINDOWS_HOURS:
            due_at = (now + timedelta(hours=window_hours)).isoformat()
            db.execute(
                """
                INSERT INTO production_analytics_followups (
                    job_id, account_id, topic_id, youtube_video_id, due_at, window_hours, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'scheduled', ?)
                ON CONFLICT(job_id, window_hours) DO UPDATE SET
                    youtube_video_id=excluded.youtube_video_id,
                    due_at=excluded.due_at,
                    status='scheduled',
                    error=NULL
                """,
                (
                    job["id"],
                    job["account_id"],
                    job["topic_id"],
                    youtube_video_id,
                    due_at,
                    window_hours,
                    created_at,
                ),
            )
            rows.append(
                {
                    "job_id": job["id"],
                    "window_hours": window_hours,
                    "due_at": due_at,
                    "status": "scheduled",
                }
            )
        db.commit()
    return rows


def run_analytics_followup(
    *,
    settings: Settings,
    session: Session,
    followup_id: int,
) -> dict[str, Any]:
    with connect(settings) as db:
        row = db.execute("SELECT * FROM production_analytics_followups WHERE id = ?", (followup_id,)).fetchone()
        if row is None:
            raise ProductionUploadError(f"Analytics follow-up {followup_id} was not found.")
        followup = dict(row)
        job_row = db.execute("SELECT * FROM pipeline_jobs WHERE id = ?", (followup["job_id"],)).fetchone()
        job = dict(job_row) if job_row is not None else {}

    youtube = _build_youtube_connector(
        session,
        settings,
        account_id=_youtube_oauth_account_id(settings, str(followup["account_id"]), session=session),
    )
    try:
        analytics = youtube.fetch_analytics(str(followup["youtube_video_id"]))
        score_update = _score_from_analytics(analytics, window_hours=int(followup["window_hours"]))
        status = "completed" if analytics.get("status") in {"ready", "ready_degraded"} else "scheduled"
        error = None
    except Exception as exc:
        analytics = {"status": "unavailable", "message": str(exc)}
        score_update = _score_from_analytics(analytics, window_hours=int(followup["window_hours"]))
        status = "scheduled"
        error = str(exc)

    qa_summary = _qa_summary_for_analytics(job)
    if qa_summary:
        score_update = {**score_update, "qa_summary": qa_summary}

    attempted_at = utc_now_iso()
    completed_at = attempted_at if status == "completed" else None
    next_due_at = None if status == "completed" else (utc_now() + timedelta(hours=FOLLOWUP_RETRY_DELAY_HOURS)).isoformat()
    if status == "completed":
        obsidian_path = write_analytics_learning_note(
            settings,
            account_id=str(followup["account_id"]),
            topic_id=str(followup["topic_id"]),
            youtube_video_id=str(followup["youtube_video_id"]),
            window_hours=int(followup["window_hours"]),
            analytics=analytics,
            score_update=score_update,
            completed_at=attempted_at,
        )
        if obsidian_path is not None:
            score_update = {**score_update, "obsidian_learning_note_path": str(obsidian_path)}
    with connect(settings) as db:
        topic_row = db.execute(
            "SELECT total_score, analytics_history_json FROM topic_queue WHERE id = ?",
            (followup["topic_id"],),
        ).fetchone()
        analytics_history = _append_analytics_history(
            json_loads(topic_row["analytics_history_json"], []) if topic_row else [],
            followup=followup,
            analytics=analytics,
            score_update=score_update,
            qa_summary=qa_summary,
            completed_at=attempted_at,
        )
        feedback_adjusted_score = _feedback_adjusted_score(
            base_score=_float(topic_row["total_score"]) if topic_row else None,
            performance_score=_float(score_update.get("performance_score")),
        )
        db.execute(
            """
            UPDATE production_analytics_followups
            SET status = ?, analytics_json = ?, score_update_json = ?, error = ?, completed_at = ?, due_at = COALESCE(?, due_at)
            WHERE id = ?
            """,
            (status, json_dumps(analytics), json_dumps(score_update), error, completed_at, next_due_at, followup_id),
        )
        db.execute(
            """
            UPDATE topic_queue
            SET last_youtube_video_id = ?,
                last_analytics_json = ?,
                analytics_history_json = ?,
                performance_score = ?,
                feedback_adjusted_score = ?,
                last_performance_recommendation = ?,
                analytics_updated_at = ?,
                status = CASE WHEN ? = 'completed' THEN 'done' ELSE status END,
                updated_at = ?
            WHERE id = ?
            """,
            (
                followup["youtube_video_id"],
                json_dumps(analytics),
                json_dumps(analytics_history),
                score_update.get("performance_score"),
                feedback_adjusted_score,
                score_update.get("recommendation"),
                attempted_at,
                status,
                attempted_at,
                followup["topic_id"],
            ),
        )
        db.commit()

    result = {
        "followup_id": followup_id,
        "job_id": followup["job_id"],
        "status": status,
        "analytics": analytics,
        "score_update": score_update,
    }
    _notify_analytics_followup(settings, result)
    return result


def run_due_analytics_followups(
    *,
    settings: Settings,
    session: Session,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Run scheduled production analytics follow-ups that are due now."""
    now = utc_now_iso()
    with connect(settings) as db:
        rows = db.execute(
            """
            SELECT id
            FROM production_analytics_followups
            WHERE status = 'scheduled' AND due_at <= ?
            ORDER BY due_at ASC
            LIMIT ?
            """,
            (now, max(1, int(limit))),
        ).fetchall()
        claimed_ids: list[int] = []
        for row in rows:
            followup_id = int(row["id"])
            result = db.execute(
                """
                UPDATE production_analytics_followups
                SET status = 'running'
                WHERE id = ? AND status = 'scheduled'
                """,
                (followup_id,),
            )
            if result.rowcount:
                claimed_ids.append(followup_id)
        db.commit()
    results: list[dict[str, Any]] = []
    for followup_id in claimed_ids:
        try:
            results.append(
                run_analytics_followup(
                    settings=settings,
                    session=session,
                    followup_id=followup_id,
                )
            )
        except Exception as exc:
            results.append(_reschedule_failed_due_followup(settings, followup_id=followup_id, error=str(exc)))
    return results


def _reschedule_failed_due_followup(settings: Settings, *, followup_id: int, error: str) -> dict[str, Any]:
    """Keep scheduler recovery idempotent when a claimed follow-up fails unexpectedly."""
    next_due_at = (utc_now() + timedelta(hours=FOLLOWUP_RETRY_DELAY_HOURS)).isoformat()
    attempted_at = utc_now_iso()
    with connect(settings) as db:
        row = db.execute("SELECT * FROM production_analytics_followups WHERE id = ?", (followup_id,)).fetchone()
        if row is None:
            return {
                "followup_id": followup_id,
                "status": "missing",
                "analytics": {"status": "unavailable", "message": error},
                "score_update": {"status": "waiting_for_data", "performance_score": None},
            }
        followup = dict(row)
        analytics = {"status": "unavailable", "message": error}
        score_update = _score_from_analytics(analytics, window_hours=int(followup["window_hours"]))
        db.execute(
            """
            UPDATE production_analytics_followups
            SET status = 'scheduled',
                analytics_json = ?,
                score_update_json = ?,
                error = ?,
                due_at = ?,
                completed_at = NULL
            WHERE id = ?
            """,
            (json_dumps(analytics), json_dumps(score_update), error, next_due_at, followup_id),
        )
        db.commit()
    result = {
        "followup_id": followup_id,
        "job_id": followup["job_id"],
        "status": "scheduled",
        "rescheduled_after_error": True,
        "next_due_at": next_due_at,
        "attempted_at": attempted_at,
        "analytics": analytics,
        "score_update": score_update,
    }
    _notify_analytics_followup(settings, result)
    return result


def _score_from_analytics(analytics: dict[str, Any], *, window_hours: int | None = None) -> dict[str, Any]:
    analytics_status = str(analytics.get("status") or "").strip()
    if analytics_status not in {"ready", "ready_degraded"}:
        return {
            "status": "waiting_for_data",
            "window_hours": window_hours,
            "performance_score": None,
            "recommendation": "Poczekaj na stabilne dane YouTube Analytics.",
        }
    degraded = analytics_status == "ready_degraded"
    views = _float(analytics.get("views")) or 0.0
    avg_pct = _float(analytics.get("average_view_percentage")) or 0.0
    avg_duration = _float(analytics.get("average_view_duration")) or 0.0
    likes = _float(analytics.get("likes")) or 0.0
    comments = _float(analytics.get("comments")) or 0.0
    shares = _float(analytics.get("shares")) or 0.0
    subscribers = _float(analytics.get("subscribers_gained")) or 0.0
    engagement_rate = ((likes + comments + shares) / views * 100.0) if views > 0 else 0.0
    views_score = min(30.0, views / 1000.0 * 30.0)
    retention_score = min(35.0, avg_pct / 100.0 * 35.0)
    duration_score = min(15.0, avg_duration / 45.0 * 15.0)
    engagement_score = min(12.0, engagement_rate / 8.0 * 12.0)
    subscriber_score = min(8.0, subscribers / 10.0 * 8.0)
    score = round(views_score + retention_score + duration_score + engagement_score + subscriber_score, 2)
    if score >= 75:
        recommendation = "Temat i format sa mocne. Wygeneruj podobne warianty."
    elif score >= 50:
        recommendation = "Wynik sredni. Zachowaj temat, ale popraw hook i tempo."
    else:
        recommendation = "Wynik slaby. Nie kopiuj formatu bez zmian."
    if degraded:
        recommendation = (
            "Ograniczony feedback z YouTube Data API: oceniaj ostroznie, bo brakuje retencji i watch time. "
            f"{recommendation}"
        )
    return {
        "status": "scored_degraded" if degraded else "scored",
        "window_hours": window_hours,
        "performance_score": score,
        "degraded": degraded,
        "analytics_source": analytics.get("fallback_source") or "youtube_analytics_api",
        "views_score": round(views_score, 2),
        "retention_score": round(retention_score, 2),
        "duration_score": round(duration_score, 2),
        "engagement_score": round(engagement_score, 2),
        "subscriber_score": round(subscriber_score, 2),
        "engagement_rate": round(engagement_rate, 3),
        "views": int(views),
        "average_view_percentage": round(avg_pct, 2),
        "average_view_duration": round(avg_duration, 2),
        "subscribers_gained": int(subscribers),
        "recommendation": recommendation,
    }


def _feedback_adjusted_score(
    *,
    base_score: float | None,
    performance_score: float | None,
) -> float | None:
    """Blend pre-production signal score with real post-upload performance."""
    if performance_score is None:
        return base_score
    if base_score is None:
        return round(max(0.0, min(100.0, performance_score)), 2)
    return round(max(0.0, min(100.0, (base_score * 0.45) + (performance_score * 0.55))), 2)


def _append_analytics_history(
    existing: Any,
    *,
    followup: dict[str, Any],
    analytics: dict[str, Any],
    score_update: dict[str, Any],
    qa_summary: dict[str, Any] | None = None,
    completed_at: str,
) -> list[dict[str, Any]]:
    history = existing if isinstance(existing, list) else []
    window_hours = int(followup["window_hours"])
    entry = {
        "window_hours": window_hours,
        "completed_at": completed_at,
        "youtube_video_id": str(followup["youtube_video_id"]),
        "analytics_status": analytics.get("status"),
        "views": analytics.get("views"),
        "average_view_percentage": analytics.get("average_view_percentage"),
        "average_view_duration": analytics.get("average_view_duration"),
        "likes": analytics.get("likes"),
        "comments": analytics.get("comments"),
        "shares": analytics.get("shares"),
        "subscribers_gained": analytics.get("subscribers_gained"),
        "performance_score": score_update.get("performance_score"),
        "recommendation": score_update.get("recommendation"),
        "qa_summary": qa_summary or score_update.get("qa_summary"),
    }
    deduped = [
        item
        for item in history
        if not (
            isinstance(item, dict)
            and int(_float(item.get("window_hours")) or -1) == window_hours
            and str(item.get("youtube_video_id") or "") == str(followup["youtube_video_id"])
        )
    ]
    deduped.append(entry)
    deduped.sort(key=lambda item: (int(_float(item.get("window_hours")) or 0), str(item.get("completed_at") or "")))
    return deduped[-20:]


def _qa_summary_for_analytics(job: dict[str, Any]) -> dict[str, Any]:
    qa_path = _existing_file_path(job.get("qa_report_path")) if job else None
    if qa_path is None:
        return {}
    qa_report = json_loads(qa_path.read_text(encoding="utf-8"), {})
    if not isinstance(qa_report, dict):
        return {}
    upload_decision = qa_report.get("upload_decision") if isinstance(qa_report.get("upload_decision"), dict) else {}
    return {
        "overall": qa_report.get("overall"),
        "auto_uploadable": bool(qa_report.get("auto_uploadable")),
        "blocking_failures": list(qa_report.get("blocking_failures") or []),
        "advisory_failures": list(qa_report.get("advisory_failures") or []),
        "upload_decision_status": upload_decision.get("status"),
        "private_upload_allowed": upload_decision.get("private_upload_allowed"),
    }


def _load_job(settings: Settings, job_id: str) -> dict[str, Any]:
    with connect(settings) as db:
        row = db.execute("SELECT * FROM pipeline_jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise ProductionUploadError(f"Job {job_id} was not found.")
    return dict(row)


def _load_authoritative_qa_report(*, job: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
    """Load QA from the job row first; package metadata is not trusted as the source of truth."""
    qa_path = _existing_file_path(job.get("qa_report_path"))
    if qa_path is None:
        qa_path = _existing_file_path(package.get("qa_report_path"))
    if qa_path is None:
        raise ProductionUploadError("QA report is required before private upload.")
    payload = json_loads(qa_path.read_text(encoding="utf-8"), {})
    if not isinstance(payload, dict):
        raise ProductionUploadError("QA report is not a valid object.")
    return payload


def _validate_qa_report_for_private_upload(qa_report: dict[str, Any]) -> None:
    if not bool(qa_report.get("auto_uploadable")):
        raise ProductionUploadError("QA report does not allow automatic private upload.")
    upload_decision = qa_report.get("upload_decision")
    if isinstance(upload_decision, dict):
        decision_status = str(upload_decision.get("status") or "").strip().lower()
        private_upload_allowed = upload_decision.get("private_upload_allowed")
        if decision_status in {"blocked", "failed", "fail", "error"} or private_upload_allowed is False:
            reason = str(upload_decision.get("reason") or "QA upload_decision blocks private upload.")
            failures = upload_decision.get("required_failures")
            failure_text = ""
            if isinstance(failures, list) and failures:
                failure_text = f" Required failures: {', '.join(str(item) for item in failures[:5])}."
            raise ProductionUploadError(f"{reason}{failure_text}")
    required_checks = qa_report.get("required_checks")
    if not isinstance(required_checks, list) or len(required_checks) != len(REQUIRED_QA_CHECK_NAMES):
        raise ProductionUploadError(
            f"QA report is incomplete: expected {len(REQUIRED_QA_CHECK_NAMES)} required quality checks."
        )
    required_names = tuple(str(item.get("name") or "") for item in required_checks if isinstance(item, dict))
    if required_names != REQUIRED_QA_CHECK_NAMES:
        raise ProductionUploadError("QA report required checks do not match the production QA contract.")
    failed_required = [
        str(item.get("name") or "unknown")
        for item in required_checks
        if not isinstance(item, dict) or str(item.get("status") or "").strip().lower() != "pass"
    ]
    if failed_required:
        raise ProductionUploadError(f"QA report has failed required checks: {', '.join(failed_required[:5])}")
    visual_variety = next(
        (
            item
            for item in required_checks
            if isinstance(item, dict) and str(item.get("name") or "") == "Visual variety"
        ),
        {},
    )
    actual_variety = (
        visual_variety.get("value", {}).get("actual_visual_variety")
        if isinstance(visual_variety.get("value"), dict)
        else None
    )
    actual_status = str(actual_variety.get("status") or "").strip().lower() if isinstance(actual_variety, dict) else ""
    if actual_status != "pass":
        raise ProductionUploadError("QA report is missing a passing actual rendered clip variety measurement.")
    blocking = qa_report.get("blocking_failures")
    if isinstance(blocking, list) and blocking:
        raise ProductionUploadError(f"QA report has blocking failures: {', '.join(str(item) for item in blocking[:5])}")
    overall = str(qa_report.get("overall") or "").strip().lower()
    if overall in {"failed", "fail", "error"}:
        raise ProductionUploadError(f"QA report overall status blocks upload: {overall}.")


def _persist_upload_result(settings: Settings, job: dict[str, Any], result: dict[str, Any], *, status: str) -> None:
    now = utc_now_iso()
    with connect(settings) as db:
        db.execute(
            """
            UPDATE pipeline_jobs
            SET status = ?,
                current_step = 'private_upload',
                youtube_video_id = ?,
                youtube_url = ?,
                upload_result = ?,
                uploaded_at = CASE WHEN ? = 'uploaded_private' THEN ? ELSE uploaded_at END,
                updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                result.get("youtube_video_id"),
                result.get("youtube_url"),
                json_dumps(result),
                status,
                now,
                now,
                job["id"],
            ),
        )
        if status == "uploaded_private":
            db.execute(
                """
                UPDATE topic_queue
                SET status = 'done',
                    last_youtube_video_id = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    result.get("youtube_video_id"),
                    now,
                    job.get("topic_id"),
                ),
            )
        db.commit()


def _persist_followup_plan(settings: Settings, job_id: str, followups: list[dict[str, Any]]) -> None:
    with connect(settings) as db:
        db.execute(
            "UPDATE pipeline_jobs SET analytics_followup_plan = ?, updated_at = ? WHERE id = ?",
            (json_dumps(followups), utc_now_iso(), job_id),
        )
        db.commit()


def _persist_private_upload_notification(settings: Settings, job_id: str, notification: dict[str, Any]) -> None:
    if not job_id:
        return
    with connect(settings) as db:
        row = db.execute("SELECT upload_result FROM pipeline_jobs WHERE id = ?", (job_id,)).fetchone()
        payload = json_loads(str(row["upload_result"] or ""), {}) if row is not None else {}
        if not isinstance(payload, dict):
            payload = {}
        payload["owner_notification"] = notification
        db.execute(
            "UPDATE pipeline_jobs SET upload_result = ?, updated_at = ? WHERE id = ?",
            (json_dumps(payload), utc_now_iso(), job_id),
        )
        db.commit()


def _notify_private_upload(
    settings: Settings,
    result: dict[str, Any],
    *,
    session: Session | None = None,
    job: dict[str, Any] | None = None,
) -> None:
    status = str(result.get("status") or "updated")
    message = str(result.get("message") or result.get("youtube_url") or "private upload updated")
    event = {
        "type": "pipeline_step",
        "job_id": str(result.get("job_id") or ""),
        "step": "private_upload",
        "status": status,
        "progress": 1.0 if status == "uploaded_private" else 0.92,
        "message": message,
        "timestamp": utc_now_iso(),
    }
    SlackWebhookNotifier(settings).notify_pipeline_event(event)
    if status != "uploaded_private" or session is None:
        return
    notification = dispatch_private_upload_link_email(settings, session, result, job=job)
    _persist_private_upload_notification(settings, str(result.get("job_id") or ""), notification)


def _notify_analytics_followup(settings: Settings, result: dict[str, Any]) -> None:
    status = str(result.get("status") or "updated")
    analytics = result.get("analytics") if isinstance(result.get("analytics"), dict) else {}
    score_update = result.get("score_update") if isinstance(result.get("score_update"), dict) else {}
    if status == "completed":
        message = (
            "analytics follow-up complete"
            f" | views={analytics.get('views', '-')}"
            f" | performance_score={score_update.get('performance_score', '-')}"
        )
    else:
        message = str(analytics.get("message") or score_update.get("recommendation") or "analytics follow-up rescheduled")
    event = {
        "type": "pipeline_step",
        "job_id": str(result.get("job_id") or ""),
        "step": "analytics",
        "status": status,
        "progress": 1.0 if status == "completed" else 0.75,
        "message": message,
        "timestamp": utc_now_iso(),
    }
    SlackWebhookNotifier(settings).notify_pipeline_event(event)


def _existing_file_path(value: object) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.exists() and path.is_file() else None


def _float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
