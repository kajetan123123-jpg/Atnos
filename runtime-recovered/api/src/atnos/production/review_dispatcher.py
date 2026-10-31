from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.intelligence.storage import connect, json_dumps, json_loads, utc_now_iso
from atnos.production.owner_review_notifications import dispatch_review_packet_email
from atnos.production.private_upload import ProductionUploadError, upload_private_from_package
from atnos.production.quality_scheduler import ProductionQualityScheduler
from atnos.services.slack_webhook_notifier import SlackWebhookNotifier

EventSink = Callable[[dict[str, Any]], None | Awaitable[None]]
SessionScope = Callable[[], AbstractContextManager[Session]]


class ProductionReviewDispatcher:
    """Obsługuje krok po QA: review właściciela albo bezpieczny prywatny upload."""

    def __init__(
        self,
        settings: Settings,
        *,
        session_scope: SessionScope | None = None,
        event_sink: EventSink | None = None,
    ) -> None:
        self.settings = settings
        self.session_scope = session_scope
        self.event_sink = event_sink

    async def handle_job(self, job_id: str) -> dict[str, Any]:
        job = await asyncio.to_thread(self._load_job, job_id)
        status = str(job.get("status") or "")
        if status != "qa_passed":
            return {
                "status": "skipped",
                "job_id": job_id,
                "message": f"Job is not QA-approved: {status or 'unknown'}.",
            }

        quality = await asyncio.to_thread(ProductionQualityScheduler(self.settings).audit_job, job_id, job=job)
        if quality.get("status") == "blocked":
            self._notify(
                {
                    "type": "production_review",
                    "job_id": job_id,
                    "account_id": job.get("account_id"),
                    "topic_id": job.get("topic_id"),
                    "status": "quality_review_blocked",
                    "blockers": quality.get("blockers") or [],
                    "timestamp": utc_now_iso(),
                }
            )
            return {
                "status": "quality_review_blocked",
                "job_id": job_id,
                "quality_review": quality,
                "message": "Review mail/private upload blocked by production quality gate.",
            }

        if self.settings.shorts_require_human_approval_before_upload:
            return await asyncio.to_thread(self._create_review_notification, job)
        return await asyncio.to_thread(self._attempt_private_upload, job)

    def _attempt_private_upload(self, job: dict[str, Any]) -> dict[str, Any]:
        job_id = str(job["id"])
        if self.session_scope is None:
            result = self._create_review_notification(
                job,
                review_status="awaiting_human_review",
                message="QA passed, but automatic private upload needs database session access.",
            )
            result["private_upload"] = "session_scope_unavailable"
            return result

        self._persist_review_state(
            job_id,
            review_status="private_upload_attempting",
            notification_path=str(job.get("review_notification_path") or ""),
        )
        try:
            with self.session_scope() as session:
                upload_result = upload_private_from_package(
                    settings=self.settings,
                    session=session,
                    job_id=job_id,
                )
        except ProductionUploadError as exc:
            result = self._create_review_notification(
                job,
                review_status="private_upload_blocked",
                message=f"QA passed, but private upload is blocked: {exc}",
            )
            result["private_upload"] = "blocked"
            return result

        upload_status = str(upload_result.get("status") or "updated")
        review_status = "uploaded_private" if upload_status == "uploaded_private" else f"private_upload_{upload_status}"
        if upload_status != "uploaded_private":
            result = self._create_review_notification(
                job,
                review_status=review_status,
                message=str(upload_result.get("message") or f"Private upload returned {upload_status}."),
            )
            result["private_upload"] = upload_result
            return result
        self._persist_review_state(
            job_id,
            review_status=review_status,
            notification_path=str(job.get("review_notification_path") or ""),
        )
        event = {
            "type": "production_review",
            "job_id": job_id,
            "status": review_status,
            "private_upload_status": upload_status,
            "youtube_url": upload_result.get("youtube_url"),
            "timestamp": utc_now_iso(),
        }
        self._notify(event)
        return {
            "status": review_status,
            "job_id": job_id,
            "private_upload": upload_result,
            "message": upload_result.get("message") or upload_result.get("youtube_url") or "Private upload updated.",
        }

    def _create_review_notification(
        self,
        job: dict[str, Any],
        *,
        review_status: str = "awaiting_human_review",
        message: str = "QA passed; review package is ready.",
    ) -> dict[str, Any]:
        job_id = str(job["id"])
        existing_path = Path(str(job.get("review_notification_path") or ""))
        if str(job.get("review_status") or "") == review_status and existing_path.exists():
            payload = self._load_json_file(existing_path)
            owner_notification = self._dispatch_owner_review_email(payload)
            if payload and owner_notification.get("status") not in {"skipped", "unchanged"}:
                payload["owner_notification"] = owner_notification
                existing_path.write_text(json_dumps(payload), encoding="utf-8")
            return {
                "status": "already_notified",
                "job_id": job_id,
                "review_status": review_status,
                "review_notification_path": str(existing_path),
                "owner_notification": payload.get("owner_notification") if payload else owner_notification,
                "message": "Review notification already exists.",
            }

        raw_artifact_dir = str(job.get("artifact_dir") or "").strip()
        artifact_dir = Path(raw_artifact_dir) if raw_artifact_dir else Path()
        if not raw_artifact_dir:
            artifact_dir = self.settings.data_dir / "production" / str(job.get("account_id") or "unknown") / job_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        review_path = artifact_dir / "review_notification.json"
        review_md_path = artifact_dir / "review_notification.md"
        qa_report = self._load_json_file(job.get("qa_report_path"))
        upload_package = self._load_json_file(job.get("upload_package_path"))
        payload = self._build_review_payload(
            job=job,
            qa_report=qa_report,
            upload_package=upload_package,
            review_status=review_status,
            message=message,
            review_markdown_path=review_md_path,
        )
        review_path.write_text(json_dumps(payload), encoding="utf-8")
        review_md_path.write_text(self._build_review_markdown(payload), encoding="utf-8")
        owner_notification = self._dispatch_owner_review_email(payload)
        payload["owner_notification"] = owner_notification
        review_path.write_text(json_dumps(payload), encoding="utf-8")
        review_md_path.write_text(self._build_review_markdown(payload), encoding="utf-8")
        self._persist_review_state(job_id, review_status=review_status, notification_path=str(review_path))
        event = {
            "type": "production_review",
            "job_id": job_id,
            "account_id": job.get("account_id"),
            "topic_id": job.get("topic_id"),
            "status": review_status,
            "review_notification_path": str(review_path),
            "video_file_path": payload.get("video_file_path"),
            "timestamp": payload["created_at"],
        }
        self._notify(event)
        return {
            "status": review_status,
            "job_id": job_id,
            "review_status": review_status,
            "review_notification_path": str(review_path),
            "review_markdown_path": str(review_md_path),
            "video_file_path": payload.get("video_file_path"),
            "thumbnail_file_path": payload.get("thumbnail_file_path"),
            "owner_notification": owner_notification,
            "upload_endpoint": payload["upload_endpoint"],
            "message": message,
        }

    def _build_review_payload(
        self,
        *,
        job: dict[str, Any],
        qa_report: dict[str, Any],
        upload_package: dict[str, Any],
        review_status: str,
        message: str,
        review_markdown_path: Path,
    ) -> dict[str, Any]:
        metadata = upload_package.get("upload_metadata") if isinstance(upload_package.get("upload_metadata"), dict) else {}
        upload_endpoint = f"POST /api/production/jobs/{job.get('id')}/upload-private"
        return {
            "type": "production_review_ready",
            "created_at": utc_now_iso(),
            "status": review_status,
            "message": message,
            "job_id": str(job.get("id") or ""),
            "account_id": str(job.get("account_id") or ""),
            "topic_id": str(job.get("topic_id") or ""),
            "topic_title": str(job.get("topic_title") or metadata.get("title") or ""),
            "video_file_path": str(upload_package.get("video_file_path") or ""),
            "thumbnail_file_path": str(upload_package.get("thumbnail_file_path") or job.get("thumbnail_path") or ""),
            "subtitle_file_path": str(upload_package.get("subtitle_file_path") or job.get("subtitle_path") or ""),
            "qa_report_path": str(job.get("qa_report_path") or upload_package.get("qa_report_path") or ""),
            "upload_package_path": str(job.get("upload_package_path") or ""),
            "review_markdown_path": str(review_markdown_path),
            "upload_endpoint": upload_endpoint,
            "public_upload_allowed": False,
            "private_upload_allowed": bool(
                qa_report.get("auto_uploadable")
                and not qa_report.get("blocking_failures")
                and str((qa_report.get("upload_decision") or {}).get("status") or "").lower()
                != "blocked"
            ),
            "qa": {
                "overall": qa_report.get("overall"),
                "auto_uploadable": bool(qa_report.get("auto_uploadable")),
                "blocking_failures": list(qa_report.get("blocking_failures") or []),
                "advisory_failures": list(qa_report.get("advisory_failures") or []),
                "required_check_count": qa_report.get("required_check_count"),
            },
            "metadata": {
                "title": str(metadata.get("title") or ""),
                "description": str(metadata.get("description") or ""),
                "tags": list(metadata.get("tags") or []),
                "visibility": str(metadata.get("visibility") or "private"),
            },
            "next_steps": [
                "Obejrzyj final_short.mp4 na telefonie.",
                "Sprawdź pierwsze 3 sekundy, czytelność napisów i głośność narracji.",
                f"Jeśli akceptujesz, uruchom {upload_endpoint}",
                "Publiczna publikacja pozostaje zablokowana poza tym krokiem.",
            ],
        }

    @staticmethod
    def _build_review_markdown(payload: dict[str, Any]) -> str:
        qa = payload.get("qa") if isinstance(payload.get("qa"), dict) else {}
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        failures = qa.get("blocking_failures") or []
        advisory = qa.get("advisory_failures") or []
        return "\n".join(
            [
                "# Atnos - short gotowy do review",
                "",
                f"- Job: `{payload.get('job_id')}`",
                f"- Konto: `{payload.get('account_id')}`",
                f"- Temat: {payload.get('topic_title') or payload.get('topic_id')}",
                f"- Status QA: `{qa.get('overall')}`",
                f"- Auto-upload prywatny dozwolony: `{payload.get('private_upload_allowed')}`",
                f"- Publiczny upload: `{payload.get('public_upload_allowed')}`",
                "",
                "## Pliki",
                f"- Wideo: `{payload.get('video_file_path')}`",
                f"- Thumbnail: `{payload.get('thumbnail_file_path')}`",
                f"- Napisy: `{payload.get('subtitle_file_path')}`",
                f"- QA: `{payload.get('qa_report_path')}`",
                "",
                "## Metadata",
                f"- Tytuł: {metadata.get('title')}",
                f"- Widoczność: `{metadata.get('visibility')}`",
                f"- Tagi: {', '.join(str(tag) for tag in metadata.get('tags') or [])}",
                "",
                "## QA",
                f"- Blokujące błędy: {', '.join(str(item) for item in failures) if failures else 'brak'}",
                f"- Ostrzeżenia: {', '.join(str(item) for item in advisory) if advisory else 'brak'}",
                "",
                "## Następny krok",
                f"- {payload.get('upload_endpoint')}",
                "",
                "## Notification",
                f"- Gmail: `{(payload.get('owner_notification') or {}).get('status', 'not_attempted')}`",
                "",
            ]
        )

    def _dispatch_owner_review_email(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not payload:
            return {"status": "skipped", "reason": "empty_review_payload"}
        existing = payload.get("owner_notification") if isinstance(payload.get("owner_notification"), dict) else {}
        if existing.get("status") in {"sent", "drafted"}:
            return {"status": "unchanged", "previous": existing}
        if self.session_scope is None:
            return {"status": "skipped", "reason": "session_scope_unavailable"}
        try:
            with self.session_scope() as session:
                return dispatch_review_packet_email(self.settings, session, payload)
        except Exception as exc:
            return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}

    def _persist_review_state(self, job_id: str, *, review_status: str, notification_path: str) -> None:
        with connect(self.settings) as db:
            db.execute(
                """
                UPDATE pipeline_jobs
                SET review_status = ?,
                    review_notified_at = COALESCE(review_notified_at, ?),
                    review_notification_path = COALESCE(NULLIF(?, ''), review_notification_path),
                    updated_at = ?
                WHERE id = ?
                """,
                (review_status, utc_now_iso(), notification_path, utc_now_iso(), job_id),
            )
            db.commit()

    def _load_job(self, job_id: str) -> dict[str, Any]:
        with connect(self.settings) as db:
            row = db.execute(
                """
                SELECT
                    j.*,
                    t.title AS topic_title,
                    t.description AS topic_description,
                    t.total_score AS topic_total_score
                FROM pipeline_jobs j
                LEFT JOIN topic_queue t ON t.id = j.topic_id
                WHERE j.id = ?
                """,
                (job_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"Production job {job_id} was not found.")
        return dict(row)

    @staticmethod
    def _load_json_file(value: object) -> dict[str, Any]:
        path = Path(str(value or ""))
        if not path.exists() or not path.is_file():
            return {}
        payload = json_loads(path.read_text(encoding="utf-8"), {})
        return payload if isinstance(payload, dict) else {}

    def _notify(self, event: dict[str, Any]) -> None:
        if self.event_sink is not None:
            result = self.event_sink(event)
            if asyncio.iscoroutine(result):
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    asyncio.run(result)
                else:
                    loop.create_task(result)
        SlackWebhookNotifier(self.settings).notify_pipeline_event(
            {
                "type": "pipeline_step",
                "job_id": str(event.get("job_id") or ""),
                "step": "review",
                "status": str(event.get("status") or "updated"),
                "progress": 1.0,
                "message": str(event.get("message") or event.get("review_notification_path") or "review updated"),
                "timestamp": str(event.get("timestamp") or utc_now_iso()),
            }
        )
