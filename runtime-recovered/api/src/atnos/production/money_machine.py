from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.intelligence.storage import connect, json_loads, read_intelligence_account_configs, rows_to_dicts, utc_now, utc_now_iso
from atnos.production.cost_guard import CostGuard
from atnos.production.oauth_resolver import resolve_youtube_oauth_account
from atnos.production.private_upload import ProductionUploadError, upload_private_from_package
from atnos.production.publication import ProductionPublicationError, promote_private_job_to_public
from atnos.production.publication_quality import public_quality_blockers, public_quality_evidence
from atnos.production.quality_backfill import backfill_visual_quality_plan_for_job
from atnos.production.voice_provider_status import build_voice_provider_status
from atnos.services.oauth_provider_registry import YOUTUBE_OPTIONAL_CAPTION_SCOPE
from atnos.services.youtube_connector_service import YouTubeConnectorService


READY_PRIVATE_UPLOAD_STATUSES = {"qa_passed"}
ACTIVE_STATUSES = {"queued", "running"}
PUBLIC_STATUSES = {"scheduled_public", "published_public"}
SUMMARY_PREVIEW_LIMIT = 5


class MoneyMachineStatusService:
    """Buduje operacyjny status maszynki Shorts: bufor, QA, upload, analityka i blokery."""

    def __init__(self, settings: Settings, *, session: Session | None = None) -> None:
        self.settings = settings
        self.session = session

    def build(self, *, account_id: str | None = None, include_oauth: bool = True) -> dict[str, Any]:
        accounts = [
            account
            for account in read_intelligence_account_configs(self.settings)
            if bool(account.get("enabled")) and (account_id is None or str(account.get("account_id")) == str(account_id))
        ]
        account_payloads = [self._account_status(account, include_oauth=include_oauth) for account in accounts]
        active_jobs = [
            job
            for account in account_payloads
            for job in (account.get("active_job_details") or [])
            if isinstance(job, dict)
        ]
        totals = self._totals(account_payloads)
        voice_provider = self._voice_provider_status()
        guard = CostGuard(self.settings)
        cost_estimate = guard.estimate_production_job()
        cost_guard = guard.check_can_spend(
            cost_estimate.estimated_cost_pln,
            operation="money_machine_next_production_job",
        )
        cost_guard["next_job_estimate_notes"] = list(cost_estimate.notes)
        return {
            "status": self._overall_status(account_payloads, totals, cost_guard),
            "generated_at": utc_now_iso(),
            "autopilot_enabled": bool(self.settings.shorts_autopilot_enabled),
            "human_approval_before_upload": bool(self.settings.shorts_require_human_approval_before_upload),
            "public_upload_enabled": bool(self.settings.allow_public_upload),
            "voice_provider": voice_provider,
            "cost_guard": cost_guard,
            "target_topic_buffer": int(self.settings.shorts_autopilot_min_topic_buffer),
            "totals": totals,
            "active_jobs": active_jobs,
            "accounts": account_payloads,
            "global_blockers": self._global_blockers(account_payloads, voice_provider, cost_guard),
            "next_actions": self._next_actions(account_payloads, voice_provider, cost_guard),
        }

    def _account_status(self, account: dict[str, Any], *, include_oauth: bool) -> dict[str, Any]:
        account_id = str(account.get("account_id") or "")
        topic_counts = self._topic_counts(account_id)
        job_counts = self._job_counts(account_id)
        ready_jobs = self._ready_private_upload_jobs(account_id)
        public_jobs = self._public_promotion_jobs(account_id)
        active_jobs = self._active_job_details(account_id)
        recent_jobs = self._recent_jobs(account_id)
        signal_counts = self._signal_counts(account_id)
        analytics = self._analytics_status(account_id)
        oauth = self._youtube_oauth_status(account, include_oauth=include_oauth)
        upload_capabilities = self._upload_capabilities(account_id, oauth)
        blockers = self._account_blockers(
            account=account,
            topic_counts=topic_counts,
            job_counts=job_counts,
            ready_jobs=ready_jobs,
            public_jobs=public_jobs,
            signal_counts=signal_counts,
            analytics=analytics,
            oauth=oauth,
        )
        return {
            "account_id": account_id,
            "channel_title": account.get("channel_title"),
            "content_profile": account.get("content_profile"),
            "niches": account.get("niches") or [],
            "region": account.get("region") or "PL",
            "topic_counts": topic_counts,
            "job_counts": job_counts,
            "ready_private_upload_count": len(ready_jobs),
            "ready_private_upload_jobs": ready_jobs,
            "public_promotion_count": len(public_jobs),
            "public_promotion_jobs": public_jobs,
            "active_job_details": active_jobs,
            "recent_jobs": recent_jobs,
            "signals": signal_counts,
            "analytics": analytics,
            "youtube_oauth": oauth,
            "upload_capabilities": upload_capabilities,
            "blockers": blockers,
            "next_action": self._account_next_action(
                blockers,
                ready_jobs=ready_jobs,
                public_jobs=public_jobs,
                topic_counts=topic_counts,
                job_counts=job_counts,
            ),
        }

    def _topic_counts(self, account_id: str) -> dict[str, Any]:
        with connect(self.settings) as db:
            rows = db.execute(
                """
                SELECT status, COUNT(*) AS count, MAX(COALESCE(total_score, 0)) AS max_score
                FROM topic_queue
                WHERE account_id = ?
                GROUP BY status
                """,
                (account_id,),
            ).fetchall()
        by_status = {str(row["status"] or "unknown"): int(row["count"] or 0) for row in rows}
        max_scores = {str(row["status"] or "unknown"): float(row["max_score"] or 0) for row in rows}
        return {
            "pending": by_status.get("pending", 0),
            "approved": by_status.get("approved", 0),
            "rejected": by_status.get("rejected", 0),
            "in_production": by_status.get("in_production", 0),
            "done": by_status.get("done", 0),
            "buffer_count": by_status.get("pending", 0) + by_status.get("approved", 0),
            "max_scores": max_scores,
        }

    def _job_counts(self, account_id: str) -> dict[str, int]:
        with connect(self.settings) as db:
            rows = db.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM pipeline_jobs
                WHERE account_id = ?
                GROUP BY status
                """,
                (account_id,),
            ).fetchall()
        by_status = {str(row["status"] or "unknown"): int(row["count"] or 0) for row in rows}
        return {
            **by_status,
            "active": sum(count for status, count in by_status.items() if status in ACTIVE_STATUSES),
            "ready_private": sum(count for status, count in by_status.items() if status in READY_PRIVATE_UPLOAD_STATUSES),
            "uploaded_private": by_status.get("uploaded_private", 0),
            "public": sum(count for status, count in by_status.items() if status in PUBLIC_STATUSES),
            "failed": sum(count for status, count in by_status.items() if "failed" in status or "error" in status),
        }

    def _ready_private_upload_jobs(self, account_id: str) -> list[dict[str, Any]]:
        with connect(self.settings) as db:
            rows = db.execute(
                """
                SELECT
                    j.id,
                    j.topic_id,
                    j.status,
                    j.qa_report,
                    j.qa_report_path,
                    j.upload_package_path,
                    j.artifact_dir,
                    j.audio_timestamps,
                    j.hooks,
                    j.script,
                    j.asset_plan,
                    j.video_manifest,
                    j.created_at,
                    j.updated_at,
                    t.title AS topic_title,
                    t.total_score AS topic_total_score
                FROM pipeline_jobs j
                LEFT JOIN topic_queue t ON t.id = j.topic_id
                WHERE j.account_id = ?
                    AND j.status = 'qa_passed'
                    AND j.upload_package_path IS NOT NULL
                ORDER BY COALESCE(j.updated_at, j.created_at, '') DESC
                LIMIT 20
                """,
                (account_id,),
            ).fetchall()
        jobs: list[dict[str, Any]] = []
        for row in rows_to_dicts(rows):
            package_path = Path(str(row.get("upload_package_path") or ""))
            qa = _load_qa_report(row)
            auto_uploadable = bool(qa.get("auto_uploadable")) if isinstance(qa, dict) else False
            if not package_path.exists() or not auto_uploadable:
                continue
            refreshed_manifest = backfill_visual_quality_plan_for_job(self.settings, row)
            if refreshed_manifest:
                row["video_manifest"] = refreshed_manifest
            quality_blockers = public_quality_blockers(self.settings, row)
            jobs.append(
                {
                    "job_id": row["id"],
                    "topic_id": row["topic_id"],
                    "topic_title": row.get("topic_title"),
                    "topic_total_score": row.get("topic_total_score"),
                    "upload_package_path": str(package_path),
                    "qa_report_path": row.get("qa_report_path"),
                    "artifact_dir": row.get("artifact_dir"),
                    "updated_at": row.get("updated_at"),
                    "auto_uploadable": auto_uploadable,
                    "public_quality_ready": not quality_blockers,
                    "public_quality_blockers": quality_blockers,
                    "public_quality_evidence": public_quality_evidence(row),
                }
            )
        return jobs

    def _recent_jobs(self, account_id: str) -> list[dict[str, Any]]:
        with connect(self.settings) as db:
            rows = db.execute(
                """
                SELECT j.id, j.topic_id, j.status, j.current_step, j.youtube_url, j.updated_at, t.title AS topic_title
                FROM pipeline_jobs j
                LEFT JOIN topic_queue t ON t.id = j.topic_id
                WHERE j.account_id = ?
                ORDER BY COALESCE(j.updated_at, j.created_at, '') DESC
                LIMIT 8
                """,
                (account_id,),
            ).fetchall()
        return rows_to_dicts(rows)

    def _active_job_details(self, account_id: str) -> list[dict[str, Any]]:
        with connect(self.settings) as db:
            rows = db.execute(
                """
                SELECT
                    j.id,
                    j.topic_id,
                    j.status,
                    j.current_step,
                    j.created_at,
                    j.updated_at,
                    t.title AS topic_title,
                    t.total_score AS topic_total_score
                FROM pipeline_jobs j
                LEFT JOIN topic_queue t ON t.id = j.topic_id
                WHERE j.account_id = ?
                  AND j.status IN ('queued', 'running')
                ORDER BY COALESCE(j.updated_at, j.created_at, '') DESC
                LIMIT 10
                """,
                (account_id,),
            ).fetchall()
        stale_after_seconds = max(1, int(self.settings.shorts_stale_job_after_minutes)) * 60
        jobs: list[dict[str, Any]] = []
        for row in rows_to_dicts(rows):
            updated_at = str(row.get("updated_at") or row.get("created_at") or "")
            age_seconds = _iso_age_seconds(updated_at)
            jobs.append(
                {
                    "account_id": account_id,
                    "job_id": str(row.get("id") or ""),
                    "topic_id": str(row.get("topic_id") or ""),
                    "topic_title": row.get("topic_title"),
                    "topic_total_score": row.get("topic_total_score"),
                    "status": row.get("status"),
                    "current_step": row.get("current_step"),
                    "created_at": row.get("created_at"),
                    "updated_at": row.get("updated_at"),
                    "heartbeat_age_seconds": age_seconds,
                    "stale_after_seconds": stale_after_seconds,
                    "stale_candidate": age_seconds is not None and age_seconds >= stale_after_seconds,
                }
            )
        return jobs

    def _public_promotion_jobs(self, account_id: str) -> list[dict[str, Any]]:
        with connect(self.settings) as db:
            rows = db.execute(
                """
                SELECT
                    j.id,
                    j.topic_id,
                    j.status,
                    j.youtube_video_id,
                    j.youtube_url,
                    j.upload_result,
                    j.audio_timestamps,
                    j.hooks,
                    j.script,
                    j.asset_plan,
                    j.video_manifest,
                    j.qa_report,
                    j.uploaded_at,
                    j.updated_at,
                    t.title AS topic_title,
                    t.total_score AS topic_total_score
                FROM pipeline_jobs j
                LEFT JOIN topic_queue t ON t.id = j.topic_id
                WHERE j.account_id = ?
                  AND j.status IN ('uploaded_private', 'public_publish_failed', 'public_publish_connection_required', 'public_publish_quota_exceeded')
                  AND j.youtube_video_id IS NOT NULL
                ORDER BY COALESCE(j.uploaded_at, j.updated_at, j.created_at, '') DESC
                LIMIT 20
                """,
                (account_id,),
            ).fetchall()
        jobs: list[dict[str, Any]] = []
        for row in rows_to_dicts(rows):
            upload_result = json_loads(str(row.get("upload_result") or ""), {})
            visibility = str(upload_result.get("visibility") or "").strip().lower() if isinstance(upload_result, dict) else ""
            if visibility and visibility != "private":
                continue
            refreshed_manifest = backfill_visual_quality_plan_for_job(self.settings, row)
            if refreshed_manifest:
                row["video_manifest"] = refreshed_manifest
            quality_blockers = public_quality_blockers(self.settings, row)
            jobs.append(
                {
                    "job_id": row["id"],
                    "topic_id": row["topic_id"],
                    "topic_title": row.get("topic_title"),
                    "topic_total_score": row.get("topic_total_score"),
                    "youtube_video_id": row.get("youtube_video_id"),
                    "youtube_url": row.get("youtube_url"),
                    "uploaded_at": row.get("uploaded_at"),
                    "updated_at": row.get("updated_at"),
                    "status": row.get("status"),
                    "visibility": visibility or "private",
                    "public_quality_blockers": quality_blockers,
                    "public_quality_evidence": public_quality_evidence(row),
                }
            )
        return jobs

    def _signal_counts(self, account_id: str) -> dict[str, int]:
        with connect(self.settings) as db:
            yt = db.execute("SELECT COUNT(*) AS count FROM yt_signals WHERE account_id = ?", (account_id,)).fetchone()
            competitor = db.execute(
                "SELECT COUNT(*) AS count FROM competitor_videos WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            gtrends_keywords = db.execute("SELECT COUNT(*) AS count FROM gtrends_signals").fetchone()
        return {
            "youtube_trend_videos": int(yt["count"] if yt else 0),
            "competitor_videos": int(competitor["count"] if competitor else 0),
            "google_trends_keywords": int(gtrends_keywords["count"] if gtrends_keywords else 0),
        }

    def _analytics_status(self, account_id: str) -> dict[str, Any]:
        now = utc_now_iso()
        with connect(self.settings) as db:
            own_count = db.execute(
                "SELECT COUNT(*) AS count, MAX(collected_at) AS last_collected_at FROM own_performance WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            followups = db.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM production_analytics_followups
                WHERE account_id = ?
                GROUP BY status
                """,
                (account_id,),
            ).fetchall()
            due_followups = db.execute(
                """
                SELECT COUNT(*) AS count
                FROM production_analytics_followups
                WHERE account_id = ?
                  AND status = 'scheduled'
                  AND due_at <= ?
                """,
                (account_id, now),
            ).fetchone()
        followup_counts = {str(row["status"] or "unknown"): int(row["count"] or 0) for row in followups}
        return {
            "own_performance_rows": int(own_count["count"] if own_count else 0),
            "last_own_performance_at": own_count["last_collected_at"] if own_count else None,
            "followups": followup_counts,
            "scheduled_followups": followup_counts.get("scheduled", 0),
            "due_followups": int(due_followups["count"] if due_followups else 0),
            "completed_followups": followup_counts.get("completed", 0),
        }

    def _upload_capabilities(self, account_id: str, oauth: dict[str, Any]) -> dict[str, Any]:
        granted = {_normalize_oauth_scope(str(scope)) for scope in (oauth.get("granted_scopes") or [])}
        warnings = self._recent_upload_warnings(account_id)
        thumbnail_warning = _first_matching_warning(warnings, ("custom video thumbnails", "youtube.thumbnail", "thumbnail upload failed"))
        thumbnail_blocked = thumbnail_warning is not None
        captions_missing_scope = _normalize_oauth_scope(YOUTUBE_OPTIONAL_CAPTION_SCOPE) not in granted
        captions_ready = bool(oauth.get("ready")) and not captions_missing_scope
        if not bool(oauth.get("checked")) and not bool(oauth.get("ready")) and not granted:
            captions_status = "not_checked"
        elif captions_ready:
            captions_status = "ready"
        else:
            captions_status = "missing_optional_scope"
        thumbnail_status = (
            "blocked_by_youtube_channel_permission"
            if thumbnail_blocked
            else ("ready" if warnings else "unknown")
        )
        return {
            "captions_api": {
                "ready": captions_ready,
                "status": captions_status,
                "required_scope": YOUTUBE_OPTIONAL_CAPTION_SCOPE,
                "mode": "api_upload" if not captions_missing_scope else "burned_in_subtitles",
                "reason": None
                if not captions_missing_scope
                else "Optional captions scope is missing; subtitles remain burned into the video.",
            },
            "custom_thumbnail": {
                "ready": thumbnail_status == "ready",
                "status": thumbnail_status,
                "mode": "api_upload" if thumbnail_status == "ready" else "youtube_default_frame",
                "last_warning": thumbnail_warning,
                "reason": _thumbnail_capability_reason(thumbnail_status),
            },
            "recent_warnings": warnings[:5],
        }

    def _recent_upload_warnings(self, account_id: str) -> list[str]:
        with connect(self.settings) as db:
            rows = db.execute(
                """
                SELECT upload_result
                FROM pipeline_jobs
                WHERE account_id = ?
                  AND upload_result IS NOT NULL
                ORDER BY COALESCE(uploaded_at, updated_at, created_at, '') DESC
                LIMIT 5
                """,
                (account_id,),
            ).fetchall()
        warnings: list[str] = []
        for row in rows:
            payload = json_loads(str(row["upload_result"] or ""), {})
            upload = payload.get("upload") if isinstance(payload, dict) else None
            raw_warnings = upload.get("warnings") if isinstance(upload, dict) else None
            if isinstance(raw_warnings, list):
                warnings.extend(str(item) for item in raw_warnings if str(item).strip())
        return list(dict.fromkeys(warnings))

    def _youtube_oauth_status(self, account: dict[str, Any], *, include_oauth: bool) -> dict[str, Any]:
        resolved = resolve_youtube_oauth_account(
            self.settings,
            str(account.get("account_id") or ""),
            session=self.session,
        )
        if resolved is None:
            return {
                "checked": False,
                "ready": False,
                "account_id": None,
                "status": "missing_oauth_account_mapping",
                "warnings": ["No youtube_oauth_account_id is mapped for this content account."],
            }
        oauth_account_id = resolved.account_id
        if not include_oauth or self.session is None:
            return {
                "checked": False,
                "ready": False,
                "account_id": oauth_account_id,
                "status": "not_checked",
                "mapping_source": resolved.source,
                "mapped_email": resolved.email,
                "warnings": ["OAuth status was not checked in this context."],
            }
        try:
            status = YouTubeConnectorService(self.session, self.settings, account_id=oauth_account_id).get_status().to_dict()
            return {"checked": True, "mapping_source": resolved.source, "mapped_email": resolved.email, **status}
        except Exception as exc:
            return {
                "checked": True,
                "ready": False,
                "account_id": oauth_account_id,
                "status": "error",
                "mapping_source": resolved.source,
                "mapped_email": resolved.email,
                "warnings": [f"{type(exc).__name__}: {exc}"],
            }

    def _account_blockers(
        self,
        *,
        account: dict[str, Any],
        topic_counts: dict[str, Any],
        job_counts: dict[str, int],
        ready_jobs: list[dict[str, Any]],
        public_jobs: list[dict[str, Any]],
        signal_counts: dict[str, int],
        analytics: dict[str, Any],
        oauth: dict[str, Any],
    ) -> list[str]:
        blockers: list[str] = []
        if not account.get("content_profile"):
            blockers.append("content_profile_missing")
        if not account.get("niches"):
            blockers.append("niches_missing")
        if signal_counts["youtube_trend_videos"] == 0 and signal_counts["google_trends_keywords"] == 0:
            blockers.append("trend_signals_missing")
        if int(topic_counts.get("buffer_count") or 0) < int(self.settings.shorts_autopilot_min_topic_buffer):
            blockers.append("topic_buffer_low")
        if int(topic_counts.get("approved") or 0) == 0 and not ready_jobs and int(job_counts.get("active") or 0) == 0:
            blockers.append("no_approved_topic_or_ready_job")
        public_ready_private_jobs = [job for job in ready_jobs if bool(job.get("public_quality_ready"))]
        if ready_jobs and not public_ready_private_jobs:
            blockers.append("private_upload_public_quality_failed")
        if public_ready_private_jobs and not bool(oauth.get("ready")):
            blockers.append("youtube_oauth_not_ready_for_private_upload")
        if public_ready_private_jobs and bool(self.settings.shorts_require_human_approval_before_upload):
            blockers.append("human_review_required_before_private_upload")
        if int(job_counts.get("uploaded_private") or 0) > 0 and int(analytics.get("own_performance_rows") or 0) == 0:
            blockers.append("own_channel_analytics_missing")
        if int(job_counts.get("uploaded_private") or 0) > 0 and not bool(self.settings.allow_public_upload):
            blockers.append("public_upload_disabled")
        if any(job.get("public_quality_blockers") for job in public_jobs):
            blockers.append("public_quality_gate_failed")
        return list(dict.fromkeys(blockers))

    def _account_next_action(
        self,
        blockers: list[str],
        *,
        ready_jobs: list[dict[str, Any]],
        public_jobs: list[dict[str, Any]],
        topic_counts: dict[str, Any],
        job_counts: dict[str, int],
    ) -> str:
        if "content_profile_missing" in blockers or "niches_missing" in blockers:
            return "configure_account_profile"
        if int(job_counts.get("active") or 0) > 0:
            return "wait_for_active_production_job"
        if "private_upload_public_quality_failed" in blockers:
            return _quality_recovery_action(_collect_quality_blockers(ready_jobs))
        if ready_jobs and "youtube_oauth_not_ready_for_private_upload" in blockers:
            return "connect_youtube_oauth"
        if ready_jobs and "human_review_required_before_private_upload" in blockers:
            return "review_and_call_upload_private_endpoint"
        if ready_jobs:
            return "call_upload_private_endpoint"
        if public_jobs and "public_quality_gate_failed" in blockers:
            return _quality_recovery_action(_collect_quality_blockers(public_jobs))
        if public_jobs and not bool(self.settings.allow_public_upload):
            return "enable_public_upload_after_review"
        if public_jobs:
            return "review_and_schedule_publication"
        if int(topic_counts.get("approved") or 0) > 0:
            return "run_production_autopilot"
        if int(topic_counts.get("buffer_count") or 0) > 0:
            return "review_or_approve_best_topic"
        if "trend_signals_missing" in blockers:
            return "run_intelligence_collectors"
        if "topic_buffer_low" in blockers:
            return "run_content_loop_generate_topics"
        if "own_channel_analytics_missing" in blockers:
            return "wait_or_sync_youtube_analytics"
        return "monitor_and_repeat_daily"

    def _totals(self, accounts: list[dict[str, Any]]) -> dict[str, int]:
        return {
            "accounts": len(accounts),
            "ready_private_upload_jobs": sum(int(item.get("ready_private_upload_count") or 0) for item in accounts),
            "public_promotion_jobs": sum(int(item.get("public_promotion_count") or 0) for item in accounts),
            "active_jobs": sum(int(item.get("job_counts", {}).get("active") or 0) for item in accounts),
            "uploaded_private_jobs": sum(int(item.get("job_counts", {}).get("uploaded_private") or 0) for item in accounts),
            "public_jobs": sum(int(item.get("job_counts", {}).get("public") or 0) for item in accounts),
            "topic_buffer": sum(int(item.get("topic_counts", {}).get("buffer_count") or 0) for item in accounts),
            "approved_topics": sum(int(item.get("topic_counts", {}).get("approved") or 0) for item in accounts),
            "due_analytics_followups": sum(int(item.get("analytics", {}).get("due_followups") or 0) for item in accounts),
        }

    def _overall_status(self, accounts: list[dict[str, Any]], totals: dict[str, int], cost_guard: dict[str, Any]) -> str:
        if bool(cost_guard.get("blocked")) or cost_guard.get("allowed") is False:
            return "budget_blocked"
        if not accounts:
            return "not_configured"
        if totals["active_jobs"] > 0:
            return "producing"
        if totals["ready_private_upload_jobs"] > 0:
            if any("youtube_oauth_not_ready_for_private_upload" in item.get("blockers", []) for item in accounts):
                return "ready_but_oauth_blocked"
            return "ready_for_private_review"
        if totals["public_promotion_jobs"] > 0:
            return "ready_for_public_review" if self.settings.allow_public_upload else "public_review_blocked"
        if totals["approved_topics"] > 0:
            return "ready_to_produce"
        if totals["topic_buffer"] > 0:
            return "needs_topic_approval"
        if any("topic_buffer_low" in item.get("blockers", []) for item in accounts):
            return "needs_topic_buffer"
        return "monitoring"

    def _voice_provider_status(self) -> dict[str, Any]:
        """Zwraca ostatni sprawdzony stan providerow glosu bez ujawniania sekretow."""
        return build_voice_provider_status(self.settings)

    def _global_blockers(
        self,
        accounts: list[dict[str, Any]],
        voice_provider: dict[str, Any],
        cost_guard: dict[str, Any],
    ) -> list[str]:
        blockers: list[str] = []
        if not accounts:
            blockers.append("no_enabled_content_accounts")
        if bool(cost_guard.get("blocked")) or cost_guard.get("allowed") is False:
            blockers.append("cost_guard_budget_blocked")
        if not self.settings.shorts_autopilot_enabled:
            blockers.append("shorts_autopilot_disabled")
        if not self.settings.allow_public_upload:
            blockers.append("public_upload_disabled")
        if (
            bool(self.settings.shorts_require_premium_voice_before_publication)
            and not bool(voice_provider.get("public_voice_ready"))
        ):
            blockers.append("premium_voice_provider_not_ready")
        for account in accounts:
            for blocker in account.get("blockers", []):
                if blocker not in blockers:
                    blockers.append(str(blocker))
        return blockers

    def _next_actions(
        self,
        accounts: list[dict[str, Any]],
        voice_provider: dict[str, Any],
        cost_guard: dict[str, Any],
    ) -> list[dict[str, str]]:
        if not accounts:
            return [{"scope": "global", "action": "configure_content_accounts"}]
        actions: list[dict[str, str]] = []
        if bool(cost_guard.get("blocked")) or cost_guard.get("allowed") is False:
            actions.append({"scope": "global", "action": "review_or_raise_monthly_api_budget"})
        if (
            bool(self.settings.shorts_require_premium_voice_before_publication)
            and not bool(voice_provider.get("public_voice_ready"))
        ):
            actions.append(
                {
                    "scope": "global",
                    "action": str(voice_provider.get("next_action") or "run_voice_provider_doctor"),
                }
            )
        for account in accounts:
            actions.append(
                {
                    "scope": str(account.get("account_id") or ""),
                    "action": str(account.get("next_action") or "monitor_and_repeat_daily"),
                }
            )
        return actions


class MoneyMachineActionService:
    """Wykonuje bezpieczny następny krok dla gotowych rolek bez automatycznej publikacji publicznej."""

    def __init__(self, settings: Settings, *, session: Session) -> None:
        self.settings = settings
        self.session = session

    def run_next(
        self,
        *,
        account_id: str | None = None,
        max_uploads: int = 1,
        per_account_limit: int = 1,
        execute: bool = False,
        approved_private_upload: bool = False,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(int(max_uploads), 10))
        safe_per_account_limit = max(1, min(int(per_account_limit), safe_limit))
        status = MoneyMachineStatusService(self.settings, session=self.session).build(
            account_id=account_id,
            include_oauth=True,
        )
        candidates = self._private_upload_candidates(
            status,
            limit=safe_limit,
            per_account_limit=safe_per_account_limit,
        )
        planned = [candidate for candidate in candidates if candidate["action"] == "upload_private"]
        blocked = [candidate for candidate in candidates if candidate["action"] != "upload_private"]
        if not execute:
            return {
                "status": "planned",
                "execute": False,
                "approved_private_upload": approved_private_upload,
                "per_account_limit": safe_per_account_limit,
                "planned_count": len(planned),
                "blocked_count": len(blocked),
                "planned": planned,
                "blocked": blocked,
                "money_machine": status,
            }
        if self.settings.shorts_require_human_approval_before_upload and not approved_private_upload:
            return {
                "status": "approval_required",
                "execute": False,
                "approved_private_upload": False,
                "per_account_limit": safe_per_account_limit,
                "planned_count": len(planned),
                "blocked_count": len(blocked) + len(planned),
                "planned": [],
                "blocked": [
                    {
                        **item,
                        "action": "review_required",
                        "reason": "approved_private_upload=true is required before private upload execution.",
                    }
                    for item in planned
                ]
                + blocked,
                "money_machine": status,
            }

        results: list[dict[str, Any]] = []
        for item in planned[:safe_limit]:
            job_id = str(item["job_id"])
            try:
                result = upload_private_from_package(settings=self.settings, session=self.session, job_id=job_id)
            except ProductionUploadError as exc:
                result = {"status": "error", "job_id": job_id, "message": str(exc)}
            results.append({"account_id": item["account_id"], "job_id": job_id, "result": result})
        return {
            "status": "completed" if results else "idle",
            "execute": True,
            "approved_private_upload": approved_private_upload,
            "per_account_limit": safe_per_account_limit,
            "executed_count": len(results),
            "blocked_count": len(blocked),
            "results": results,
            "blocked": blocked,
        }

    def run_publication(
        self,
        *,
        account_id: str | None = None,
        max_publications: int = 1,
        per_account_limit: int = 1,
        execute: bool = False,
        approved_public_promotion: bool = False,
        publish_at: str | None = None,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(int(max_publications), 10))
        safe_per_account_limit = max(1, min(int(per_account_limit), safe_limit))
        status = MoneyMachineStatusService(self.settings, session=self.session).build(
            account_id=account_id,
            include_oauth=True,
        )
        candidates = self._public_promotion_candidates(
            status,
            limit=safe_limit,
            per_account_limit=safe_per_account_limit,
        )
        planned = [candidate for candidate in candidates if candidate["action"] == "promote_public"]
        blocked = [candidate for candidate in candidates if candidate["action"] != "promote_public"]
        if not execute:
            return {
                "status": "planned",
                "execute": False,
                "approved_public_promotion": approved_public_promotion,
                "per_account_limit": safe_per_account_limit,
                "publish_at": publish_at,
                "planned_count": len(planned),
                "blocked_count": len(blocked),
                "planned": planned,
                "blocked": blocked,
                "money_machine": status,
            }
        if not approved_public_promotion:
            return {
                "status": "approval_required",
                "execute": False,
                "approved_public_promotion": False,
                "per_account_limit": safe_per_account_limit,
                "publish_at": publish_at,
                "planned_count": len(planned),
                "blocked_count": len(blocked) + len(planned),
                "planned": [],
                "blocked": [
                    {
                        **item,
                        "action": "public_review_required",
                        "reason": "approved_public_promotion=true is required before public scheduling.",
                    }
                    for item in planned
                ]
                + blocked,
                "money_machine": status,
            }

        results: list[dict[str, Any]] = []
        for item in planned[:safe_limit]:
            job_id = str(item["job_id"])
            try:
                result = promote_private_job_to_public(
                    settings=self.settings,
                    session=self.session,
                    job_id=job_id,
                    approved=True,
                    publish_at=publish_at,
                )
            except ProductionPublicationError as exc:
                result = {"status": "error", "job_id": job_id, "message": str(exc)}
            results.append({"account_id": item["account_id"], "job_id": job_id, "result": result})
        return {
            "status": "completed" if results else "idle",
            "execute": True,
            "approved_public_promotion": approved_public_promotion,
            "per_account_limit": safe_per_account_limit,
            "publish_at": publish_at,
            "executed_count": len(results),
            "blocked_count": len(blocked),
            "results": results,
            "blocked": blocked,
        }

    def _private_upload_candidates(self, status: dict[str, Any], *, limit: int, per_account_limit: int) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        upload_candidate_count = 0
        for account in status.get("accounts", []):
            account_id = str(account.get("account_id") or "")
            oauth_ready = bool(account.get("youtube_oauth", {}).get("ready"))
            account_upload_count = 0
            for job in account.get("ready_private_upload_jobs", []):
                if account_upload_count >= per_account_limit:
                    break
                item = {
                    "account_id": account_id,
                    "job_id": str(job.get("job_id") or ""),
                    "topic_id": str(job.get("topic_id") or ""),
                    "topic_title": str(job.get("topic_title") or ""),
                    "upload_package_path": str(job.get("upload_package_path") or ""),
                    "qa_report_path": str(job.get("qa_report_path") or ""),
                    "artifact_dir": str(job.get("artifact_dir") or ""),
                    "updated_at": str(job.get("updated_at") or ""),
                    "auto_uploadable": bool(job.get("auto_uploadable")),
                    "public_quality_ready": bool(job.get("public_quality_ready")),
                    "public_quality_evidence": job.get("public_quality_evidence") or {},
                }
                quality_blockers = [str(item) for item in (job.get("public_quality_blockers") or []) if str(item).strip()]
                if quality_blockers:
                    recovery_action = _quality_recovery_action(quality_blockers)
                    candidates.append(
                        {
                            **item,
                            "action": "public_quality_blocked",
                            "recovery_action": recovery_action,
                            "recovery_endpoint": f"POST /api/production/jobs/{item['job_id']}/regenerate",
                            "reason": (
                                "Private money-machine upload is reserved for public-ready Shorts; "
                                f"quality blockers: {', '.join(quality_blockers)}. Next action: {recovery_action}."
                            ),
                            "public_quality_blockers": quality_blockers,
                            "public_quality_evidence": job.get("public_quality_evidence") or {},
                        }
                    )
                elif not oauth_ready:
                    candidates.append({**item, "action": "connect_youtube_oauth", "reason": "YouTube OAuth is not ready."})
                else:
                    candidates.append(
                        {
                            **item,
                            "action": "upload_private",
                            "reason": "QA passed, public-quality gates passed, and OAuth is ready.",
                        }
                    )
                    account_upload_count += 1
                    upload_candidate_count += 1
                if upload_candidate_count >= limit:
                    return candidates
        return candidates

    def _public_promotion_candidates(self, status: dict[str, Any], *, limit: int, per_account_limit: int) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        promotion_candidate_count = 0
        for account in status.get("accounts", []):
            account_id = str(account.get("account_id") or "")
            oauth_ready = bool(account.get("youtube_oauth", {}).get("ready"))
            account_promotion_count = 0
            for job in account.get("public_promotion_jobs", []):
                if account_promotion_count >= per_account_limit:
                    break
                item = {
                    "account_id": account_id,
                    "job_id": str(job.get("job_id") or ""),
                    "topic_id": str(job.get("topic_id") or ""),
                    "topic_title": str(job.get("topic_title") or ""),
                    "youtube_video_id": str(job.get("youtube_video_id") or ""),
                    "youtube_url": str(job.get("youtube_url") or ""),
                    "public_quality_evidence": job.get("public_quality_evidence") or {},
                }
                quality_blockers = [str(item) for item in (job.get("public_quality_blockers") or []) if str(item).strip()]
                if not oauth_ready:
                    candidates.append({**item, "action": "connect_youtube_oauth", "reason": "YouTube OAuth is not ready."})
                elif not self.settings.allow_public_upload:
                    candidates.append({**item, "action": "enable_public_upload", "reason": "ALLOW_PUBLIC_UPLOAD is disabled."})
                elif quality_blockers:
                    recovery_action = _quality_recovery_action(quality_blockers)
                    candidates.append(
                        {
                            **item,
                            "action": "public_quality_blocked",
                            "recovery_action": recovery_action,
                            "recovery_endpoint": f"POST /api/production/jobs/{item['job_id']}/regenerate",
                            "reason": f"Public quality gate blockers: {', '.join(quality_blockers)}. Next action: {recovery_action}.",
                            "public_quality_blockers": quality_blockers,
                        }
                    )
                else:
                    candidates.append({**item, "action": "promote_public", "reason": "Private upload is ready for public scheduling."})
                    account_promotion_count += 1
                    promotion_candidate_count += 1
                if promotion_candidate_count >= limit:
                    return candidates
        return candidates


def build_money_machine_operator_summary(status: dict[str, Any], run_next: dict[str, Any] | None = None) -> dict[str, Any]:
    """Zbuduj zwięzły raport operatorski bez ogromnego payloadu statusowego."""
    accounts = status.get("accounts") if isinstance(status.get("accounts"), list) else []
    planned = run_next.get("planned") if isinstance(run_next, dict) and isinstance(run_next.get("planned"), list) else []
    blocked = run_next.get("blocked") if isinstance(run_next, dict) and isinstance(run_next.get("blocked"), list) else []
    upload_items = [_compact_job(item) for item in planned][:SUMMARY_PREVIEW_LIMIT]
    recovery_items = [
        _compact_job(item)
        for item in blocked
        if str(item.get("action") or "") == "public_quality_blocked"
    ][:SUMMARY_PREVIEW_LIMIT]
    account_summaries = [_compact_account_summary(account) for account in accounts]
    active_jobs = [
        job
        for account in account_summaries
        for job in (account.get("active_job_details") or [])
        if isinstance(job, dict)
    ]
    totals = status.get("totals") if isinstance(status.get("totals"), dict) else {}
    cost_guard = status.get("cost_guard") if isinstance(status.get("cost_guard"), dict) else {}
    return {
        "status": status.get("status"),
        "generated_at": status.get("generated_at"),
        "public_upload_enabled": bool(status.get("public_upload_enabled")),
        "human_approval_before_upload": bool(status.get("human_approval_before_upload")),
        "voice_provider_ready": bool((status.get("voice_provider") or {}).get("public_voice_ready")),
        "cost_guard": {
            "enabled": bool(cost_guard.get("enabled", True)),
            "allowed": cost_guard.get("allowed"),
            "blocked": bool(cost_guard.get("blocked")),
            "warning": bool(cost_guard.get("warning")),
            "currency": str(cost_guard.get("currency") or "PLN"),
            "estimated_action_cost_pln": float(cost_guard.get("estimated_action_cost_pln") or 0),
            "monthly_spent_pln": float(cost_guard.get("monthly_spent_pln") or 0),
            "monthly_limit_pln": cost_guard.get("monthly_limit_pln"),
            "monthly_remaining_pln": cost_guard.get("monthly_remaining_pln"),
            "daily_spent_pln": float(cost_guard.get("daily_spent_pln") or 0),
            "daily_limit_pln": cost_guard.get("daily_limit_pln"),
            "daily_remaining_pln": cost_guard.get("daily_remaining_pln"),
        },
        "totals": {
            "accounts": int(totals.get("accounts") or 0),
            "ready_private_upload_jobs": int(totals.get("ready_private_upload_jobs") or 0),
            "public_ready_private_upload_jobs": len(upload_items),
            "blocked_private_upload_jobs": len(blocked),
            "public_promotion_jobs": int(totals.get("public_promotion_jobs") or 0),
            "active_jobs": int(totals.get("active_jobs") or 0),
            "topic_buffer": int(totals.get("topic_buffer") or 0),
            "approved_topics": int(totals.get("approved_topics") or 0),
            "due_analytics_followups": int(totals.get("due_analytics_followups") or 0),
        },
        "next_operator_action": _operator_next_action(upload_items, recovery_items, account_summaries, status),
        "active_jobs": active_jobs,
        "private_upload_ready": upload_items,
        "recovery_ready": recovery_items,
        "accounts": account_summaries,
        "next_actions": status.get("next_actions") if isinstance(status.get("next_actions"), list) else [],
        "global_blockers": status.get("global_blockers") if isinstance(status.get("global_blockers"), list) else [],
        "notes": _operator_notes(status, account_summaries),
    }


def _load_qa_report(row: dict[str, Any]) -> dict[str, Any]:
    inline = json_loads(str(row.get("qa_report") or ""), None)
    if isinstance(inline, dict):
        return inline
    path = Path(str(row.get("qa_report_path") or ""))
    if path.exists():
        try:
            parsed = json_loads(path.read_text(encoding="utf-8"), {})
            return parsed if isinstance(parsed, dict) else {}
        except OSError:
            return {}
    return {}


def _iso_age_seconds(value: str) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=utc_now().tzinfo)
    return max(0, int((utc_now() - parsed.astimezone(utc_now().tzinfo)).total_seconds()))


def _normalize_oauth_scope(scope: str) -> str:
    return str(scope or "").strip().casefold()


def _first_matching_warning(warnings: list[str], markers: tuple[str, ...]) -> str | None:
    lowered_markers = tuple(marker.casefold() for marker in markers)
    for warning in warnings:
        lowered = warning.casefold()
        if any(marker in lowered for marker in lowered_markers):
            return warning
    return None


def _thumbnail_capability_reason(status: str) -> str | None:
    if status == "ready":
        return None
    if status == "unknown":
        return "No recent YouTube thumbnail upload result is available for this account."
    return "YouTube account/channel does not currently allow custom thumbnail upload."


def _collect_quality_blockers(jobs: list[dict[str, Any]]) -> list[str]:
    blockers: list[str] = []
    for job in jobs:
        for blocker in job.get("public_quality_blockers") or []:
            value = str(blocker).strip()
            if value and value not in blockers:
                blockers.append(value)
    return blockers


def _quality_recovery_action(blockers: list[str]) -> str:
    blocker_set = {str(item).strip() for item in blockers if str(item).strip()}
    if not blocker_set:
        return "review_and_call_upload_private_endpoint"
    if any("voice" in blocker for blocker in blocker_set):
        return "regenerate_with_premium_voice"
    visual_markers = (
        "scene_concreteness",
        "generated_image_visual",
        "premium_visual_backend",
        "video_manifest",
        "visual",
    )
    if any(any(marker in blocker for marker in visual_markers) for blocker in blocker_set):
        return "regenerate_visuals_with_auto_repair"
    if any("generated_image_text" in blocker for blocker in blocker_set):
        return "regenerate_visuals_with_no_text_guardrails"
    if any("qa_" in blocker for blocker in blocker_set):
        return "rerun_qa_and_review_report"
    return "regenerate_with_premium_voice_and_premium_visual_backend"


def _compact_job(item: dict[str, Any]) -> dict[str, Any]:
    blockers = [str(blocker) for blocker in (item.get("public_quality_blockers") or []) if str(blocker).strip()]
    payload: dict[str, Any] = {
        "account_id": str(item.get("account_id") or ""),
        "job_id": str(item.get("job_id") or ""),
        "topic_id": str(item.get("topic_id") or ""),
        "topic_title": str(item.get("topic_title") or ""),
        "action": str(item.get("action") or ""),
        "reason": str(item.get("reason") or ""),
        "public_quality_blockers": blockers,
        "recovery_action": str(item.get("recovery_action") or ""),
        "recovery_endpoint": str(item.get("recovery_endpoint") or ""),
        "upload_package_path": str(item.get("upload_package_path") or ""),
        "qa_report_path": str(item.get("qa_report_path") or ""),
    }
    evidence = item.get("public_quality_evidence") if isinstance(item.get("public_quality_evidence"), dict) else {}
    if evidence:
        payload["quality"] = {
            "voice": evidence.get("voice_quality_tier"),
            "video_backend": evidence.get("video_backend"),
            "operating_standards": evidence.get("operating_standards"),
            "scene_concreteness_risk_count": evidence.get("scene_concreteness_risk_count"),
            "generated_image_text_risk_count": evidence.get("generated_image_text_risk_count"),
            "generated_image_visual_risk_count": evidence.get("generated_image_visual_risk_count"),
        }
    return payload


def _compact_account_summary(account: dict[str, Any]) -> dict[str, Any]:
    ready_jobs = account.get("ready_private_upload_jobs") if isinstance(account.get("ready_private_upload_jobs"), list) else []
    public_ready = [job for job in ready_jobs if bool(job.get("public_quality_ready"))]
    oauth = account.get("youtube_oauth") if isinstance(account.get("youtube_oauth"), dict) else {}
    upload_capabilities = account.get("upload_capabilities") if isinstance(account.get("upload_capabilities"), dict) else {}
    topic_counts = account.get("topic_counts") if isinstance(account.get("topic_counts"), dict) else {}
    job_counts = account.get("job_counts") if isinstance(account.get("job_counts"), dict) else {}
    analytics = account.get("analytics") if isinstance(account.get("analytics"), dict) else {}
    captions = upload_capabilities.get("captions_api") if isinstance(upload_capabilities.get("captions_api"), dict) else {}
    thumbnail = upload_capabilities.get("custom_thumbnail") if isinstance(upload_capabilities.get("custom_thumbnail"), dict) else {}
    active_job_details = account.get("active_job_details") if isinstance(account.get("active_job_details"), list) else []
    return {
        "account_id": str(account.get("account_id") or ""),
        "channel_title": account.get("channel_title"),
        "next_action": str(account.get("next_action") or ""),
        "ready_private_upload_count": len(public_ready),
        "blocked_private_upload_count": max(0, len(ready_jobs) - len(public_ready)),
        "active_jobs": int(job_counts.get("active") or 0),
        "active_job_details": [_compact_active_job(job) for job in active_job_details[:3] if isinstance(job, dict)],
        "topic_buffer": int(topic_counts.get("buffer_count") or 0),
        "pending_topics": int(topic_counts.get("pending") or 0),
        "approved_topics": int(topic_counts.get("approved") or 0),
        "max_pending_score": float((topic_counts.get("max_scores") or {}).get("pending") or 0),
        "oauth_ready": bool(oauth.get("ready")),
        "captions_api_ready": bool(captions.get("ready")),
        "custom_thumbnail_ready": bool(thumbnail.get("ready")),
        "own_performance_rows": int(analytics.get("own_performance_rows") or 0),
        "blockers": account.get("blockers") if isinstance(account.get("blockers"), list) else [],
    }


def _compact_active_job(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "account_id": str(item.get("account_id") or ""),
        "job_id": str(item.get("job_id") or ""),
        "topic_id": str(item.get("topic_id") or ""),
        "topic_title": str(item.get("topic_title") or ""),
        "status": str(item.get("status") or ""),
        "current_step": str(item.get("current_step") or ""),
        "updated_at": item.get("updated_at"),
        "heartbeat_age_seconds": item.get("heartbeat_age_seconds"),
        "stale_after_seconds": item.get("stale_after_seconds"),
        "stale_candidate": bool(item.get("stale_candidate")),
    }


def _operator_next_action(
    upload_items: list[dict[str, Any]],
    recovery_items: list[dict[str, Any]],
    account_summaries: list[dict[str, Any]],
    status: dict[str, Any],
) -> str:
    cost_guard = status.get("cost_guard") if isinstance(status.get("cost_guard"), dict) else {}
    if bool(cost_guard.get("blocked")) or cost_guard.get("allowed") is False:
        return "review_or_raise_monthly_api_budget"
    if upload_items:
        return "review_and_execute_private_upload"
    if any(int(account.get("active_jobs") or 0) > 0 for account in account_summaries):
        return "wait_for_active_production_job"
    if recovery_items:
        return "execute_recovery_regeneration"
    if any(
        int(account.get("topic_buffer") or 0) > 0 and int(account.get("approved_topics") or 0) == 0
        for account in account_summaries
    ):
        return "review_or_approve_best_topic"
    if any(not bool(account.get("oauth_ready")) for account in account_summaries):
        return "connect_youtube_oauth"
    if int((status.get("totals") or {}).get("topic_buffer") or 0) == 0:
        return "generate_topics_from_intelligence"
    return "monitor_and_repeat_daily"


def _operator_notes(status: dict[str, Any], account_summaries: list[dict[str, Any]]) -> list[str]:
    notes: list[str] = []
    cost_guard = status.get("cost_guard") if isinstance(status.get("cost_guard"), dict) else {}
    if bool(cost_guard.get("blocked")) or cost_guard.get("allowed") is False:
        notes.append("API spend budget blocks the next estimated render; raise the limit, lower cloud usage, or wait for the next period.")
    elif bool(cost_guard.get("warning")):
        notes.append("API spend budget is near the configured warning threshold.")
    if status.get("human_approval_before_upload") is not False:
        notes.append("Private upload execution still requires approved_private_upload=true.")
    if not bool(status.get("public_upload_enabled")):
        notes.append("Public upload is disabled; public promotion remains supervised/off.")
    if any(
        int(account.get("topic_buffer") or 0) > 0 and int(account.get("approved_topics") or 0) == 0
        for account in account_summaries
    ):
        notes.append("Topic buffer has candidates, but production waits for at least one approved topic.")
    if any(not bool(account.get("custom_thumbnail_ready")) for account in account_summaries):
        notes.append("At least one YouTube channel cannot set custom thumbnails yet; YouTube default frames will be used.")
    if any(not bool(account.get("captions_api_ready")) for account in account_summaries):
        notes.append("YouTube captions API scope is missing for at least one account; burned-in subtitles remain active.")
    return notes
