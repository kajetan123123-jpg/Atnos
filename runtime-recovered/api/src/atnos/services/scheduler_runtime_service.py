from __future__ import annotations

import asyncio
from logging import getLogger
import threading
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from atnos.core.config import Settings
    from atnos.db.session import DatabaseManager
    from atnos.plugins.registry import PluginRegistry
    from atnos.providers.registry import ProviderRegistry
    from atnos.services.summary_writeback_service import SummaryWritebackService

logger = getLogger(__name__)
_ACTIVE_RUNTIME: "SchedulerRuntimeService | None" = None
SCHEDULER_TICK_SECONDS = 10.0
PRODUCTION_ANALYTICS_FOLLOWUP_LIMIT = 5
INTELLIGENCE_REFRESH_JOB_ID = "system:intelligence-refresh"
PRODUCTION_ANALYTICS_FOLLOWUP_JOB_ID = "system:production-analytics-followups"
PRODUCTION_AUTOPILOT_JOB_ID = "system:production-autopilot"
INVESTMENT_PAPER_AUTOPILOT_JOB_ID = "system:investment-paper-autopilot"
PRODUCTION_QUALITY_REVIEW_JOB_ID = "system:production-quality-review"
PRODUCTION_QUALITY_REMEDIATION_JOB_ID = "system:production-quality-remediation"
PROACTIVE_MORNING_BRIEFING_JOB_ID = "system:proactive-morning-briefing"
INTELLIGENCE_REFRESH_INTERVAL_MINUTES = 15


def run_scheduled_task_job(task_id: int) -> None:
    if _ACTIVE_RUNTIME is not None:
        _ACTIVE_RUNTIME._run_scheduled_task(task_id)


def run_intelligence_refresh_job() -> None:
    if _ACTIVE_RUNTIME is not None:
        _ACTIVE_RUNTIME.process_intelligence_refresh_once()


def run_production_analytics_followup_job() -> None:
    if _ACTIVE_RUNTIME is not None:
        _ACTIVE_RUNTIME.process_production_analytics_followups_once()


def run_production_autopilot_job() -> None:
    if _ACTIVE_RUNTIME is not None:
        _ACTIVE_RUNTIME.process_production_autopilot_once()


def run_production_quality_review_job() -> None:
    if _ACTIVE_RUNTIME is not None:
        _ACTIVE_RUNTIME.process_production_quality_review_once()


def run_production_quality_remediation_job() -> None:
    if _ACTIVE_RUNTIME is not None:
        _ACTIVE_RUNTIME.process_production_quality_remediation_once()


def run_investment_paper_autopilot_job() -> None:
    if _ACTIVE_RUNTIME is not None:
        _ACTIVE_RUNTIME.process_investment_paper_autopilot_once()


def run_proactive_morning_briefing_job() -> None:
    if _ACTIVE_RUNTIME is not None:
        _ACTIVE_RUNTIME.process_proactive_morning_briefing_once()


def get_scheduler_runtime_status() -> dict[str, object]:
    """Return a safe runtime snapshot for readiness and UI diagnostics."""
    runtime = _ACTIVE_RUNTIME
    task = runtime._task if runtime is not None else None
    scheduler = runtime._scheduler if runtime is not None else None
    internal_loop_running = bool(task is not None and not task.done())
    apscheduler_running = bool(scheduler is not None and getattr(scheduler, "running", False))
    system_job_ids: list[str] = []
    if scheduler is not None:
        try:
            system_job_ids = [str(job.id) for job in scheduler.get_jobs()]
        except Exception:
            system_job_ids = []
    production_followups_job_running = PRODUCTION_ANALYTICS_FOLLOWUP_JOB_ID in system_job_ids
    intelligence_refresh_job_running = INTELLIGENCE_REFRESH_JOB_ID in system_job_ids
    production_autopilot_job_running = PRODUCTION_AUTOPILOT_JOB_ID in system_job_ids
    investment_paper_autopilot_job_running = INVESTMENT_PAPER_AUTOPILOT_JOB_ID in system_job_ids
    production_quality_review_job_running = PRODUCTION_QUALITY_REVIEW_JOB_ID in system_job_ids
    production_quality_remediation_job_running = PRODUCTION_QUALITY_REMEDIATION_JOB_ID in system_job_ids
    proactive_morning_briefing_job_running = PROACTIVE_MORNING_BRIEFING_JOB_ID in system_job_ids
    return {
        "active_runtime": runtime is not None,
        "internal_loop_running": internal_loop_running,
        "apscheduler_running": apscheduler_running,
        "system_job_ids": system_job_ids,
        "pollers": [
            "production_publication",
            "trusted_auto",
            "trend_watch",
            "production_autopilot",
            "investment_paper_autopilot",
            "production_quality_review",
            "production_quality_remediation",
            "production_analytics_followups",
            "intelligence_refresh",
            "proactive_morning_briefing",
        ],
        "production_autopilot_automatic": internal_loop_running
        or (apscheduler_running and production_autopilot_job_running),
        "investment_paper_autopilot_automatic": internal_loop_running
        or (apscheduler_running and investment_paper_autopilot_job_running),
        "production_analytics_followups_automatic": internal_loop_running
        or (apscheduler_running and production_followups_job_running),
        "production_quality_review_automatic": internal_loop_running
        or (apscheduler_running and production_quality_review_job_running),
        "production_quality_remediation_automatic": internal_loop_running
        or (apscheduler_running and production_quality_remediation_job_running),
        "intelligence_refresh_automatic": internal_loop_running
        or (apscheduler_running and intelligence_refresh_job_running),
        "proactive_morning_briefing_automatic": apscheduler_running and proactive_morning_briefing_job_running,
        "scheduled_task_cron_jobs_automatic": apscheduler_running,
        "production_publication_automatic": bool(runtime and runtime._publication_task and not runtime._publication_task.done()),
        "production_publication_last_tick": runtime._publication_last_tick if runtime else None,
        "production_publication_last_error": runtime._publication_last_error if runtime else None,
    }


class SchedulerRuntimeService:
    def __init__(
        self,
        database: "DatabaseManager",
        settings: "Settings",
        *,
        provider_registry: "ProviderRegistry",
        plugin_registry: "PluginRegistry",
        summary_writeback_factory: type["SummaryWritebackService"],
    ) -> None:
        self.database = database
        self.settings = settings
        self.provider_registry = provider_registry
        self.plugin_registry = plugin_registry
        self.summary_writeback_factory = summary_writeback_factory
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._publication_task: asyncio.Task[None] | None = None
        self._publication_last_tick: float | None = None
        self._publication_last_error: str | None = None
        self._scheduler: Any | None = None
        self._production_resume_tasks: set[asyncio.Task[dict[str, Any]]] = set()
        self._trusted_auto_kick_lock = threading.Lock()
        self._production_analytics_followup_lock = threading.Lock()
        self._last_production_analytics_followup_at = 0.0
        self._last_intelligence_refresh_at = 0.0
        self._last_production_autopilot_at = 0.0
        self._last_production_quality_review_at = 0.0
        self._last_production_quality_remediation_at = 0.0
        self._last_investment_paper_autopilot_at = 0.0

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        global _ACTIVE_RUNTIME
        _ACTIVE_RUNTIME = self
        from atnos.services.trusted_auto_service import TrustedAutoService

        with self.database.session_scope() as session:
            recovery = TrustedAutoService(session, self.settings).recover_interrupted_runs()
        if any(recovery.values()):
            logger.warning(
                "Recovered interrupted trusted-auto state after restart | jobs=%s runs=%s steps=%s",
                recovery["recovered_jobs"],
                recovery["recovered_runs"],
                recovery["recovered_steps"],
            )
        try:
            from atnos.production.job_recovery import ProductionJobRecoveryService

            production_recovery = ProductionJobRecoveryService(self.settings).recover_stale_jobs(
                resume_video_jobs=True,
                recover_all_active=str(self.settings.app_env or "").strip().lower() != "test",
            )
            if int(production_recovery.get("recovered_count") or 0) > 0:
                logger.warning(
                    "Recovered stale production jobs after scheduler restart | jobs=%s topics=%s",
                    production_recovery.get("recovered_count"),
                    production_recovery.get("restored_topic_count"),
                )
            self._schedule_production_resumes(production_recovery.get("resume_job_ids") or [])
        except Exception:
            logger.exception("Production stale-job recovery failed during scheduler startup.")
        self._start_scheduled_tasks_runtime()
        self._task = asyncio.create_task(self._loop(), name="atnos-scheduler-runtime")
        if self.settings.app_env != "test":
            self._publication_task = asyncio.create_task(self._publication_loop(), name="atnos-publication-runtime")

    async def stop(self) -> None:
        self._stop.set()
        if self._publication_task is not None:
            await self._publication_task
            self._publication_task = None
        if self._task is not None:
            await self._task
            self._task = None
        if self._production_resume_tasks:
            for task in tuple(self._production_resume_tasks):
                task.cancel()
            await asyncio.gather(*tuple(self._production_resume_tasks), return_exceptions=True)
        if self._scheduler is not None:
            self._scheduler.remove_all_jobs()
            self._scheduler.shutdown(wait=True)
            self._scheduler = None
        global _ACTIVE_RUNTIME
        if _ACTIVE_RUNTIME is self:
            _ACTIVE_RUNTIME = None

    def _schedule_production_resumes(self, job_ids: list[str]) -> None:
        for job_id in dict.fromkeys(str(value) for value in job_ids if str(value).strip()):
            task = asyncio.create_task(
                self._resume_production_job(job_id),
                name=f"atnos-production-resume-{job_id}",
            )
            self._production_resume_tasks.add(task)
            task.add_done_callback(self._production_resume_tasks.discard)

    async def _publication_loop(self) -> None:
        from atnos.production.publication_coordinator import PublicationCoordinator

        service = PublicationCoordinator(self.settings, self.database)
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(service.tick)
                self._publication_last_tick = time.time()
                self._publication_last_error = None
            except Exception:
                self._publication_last_error = "Nie udalo sie sprawdzic kolejki publikacji. Sprawdz log runtime."
                logger.exception("Publication coordinator tick failed.")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=60)
            except asyncio.TimeoutError:
                continue

    async def _resume_production_job(self, job_id: str) -> dict[str, Any]:
        from atnos.production.job_resume import resume_video_job

        result = await resume_video_job(self.settings, job_id)
        if str(result.get("status") or "") == "error":
            logger.error("Automatic production resume failed | job_id=%s error=%s", job_id, result.get("error"))
        else:
            logger.info("Automatic production resume finished | job_id=%s status=%s", job_id, result.get("status"))
        return result

    def refresh_scheduled_task(self, task_id: int) -> None:
        if self._scheduler is None:
            return
        from apscheduler.triggers.cron import CronTrigger
        from atnos.services.scheduled_task_service import ScheduledTaskService

        with self.database.session_scope() as session:
            service = ScheduledTaskService(session, self.settings)
            task = service.tasks.get_by_id(task_id)
            job_id = self._scheduled_task_job_id(task_id)
            if task is None or not task.is_active:
                try:
                    self._scheduler.remove_job(job_id)
                except Exception:
                    pass
                return

            cron_spec = service.validate_cron_spec(task.cron_spec)
            job = self._scheduler.add_job(
                run_scheduled_task_job,
                trigger=CronTrigger.from_crontab(cron_spec, timezone="Europe/Warsaw"),
                id=job_id,
                kwargs={"task_id": task.id},
                replace_existing=True,
                coalesce=True,
                misfire_grace_time=60,
            )
            service.update_runtime_state(task.id, next_run_at=job.next_run_time)

    def remove_scheduled_task(self, task_id: int) -> None:
        if self._scheduler is None:
            return
        try:
            self._scheduler.remove_job(self._scheduled_task_job_id(task_id))
        except Exception:
            return

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                runs = await self._process_trusted_auto_tick()
                if runs:
                    logger.info("Trusted auto scheduler processed %s run(s).", len(runs))
            except Exception:
                logger.exception("Trusted auto scheduler tick failed.")
            try:
                snapshot = await self._run_trend_watch_tick()
                if snapshot is not None:
                    logger.info(
                        "Trend watch refreshed | query=%s | signals=%s",
                        snapshot.get("topic_query"),
                        dict(snapshot.get("summary") or {}).get("signals_count"),
                    )
            except Exception:
                logger.exception("Trend watch scheduler tick failed.")
            try:
                followups = await self._run_production_analytics_followups_tick()
                if followups:
                    logger.info("Production analytics follow-up processed %s item(s).", len(followups))
            except Exception:
                logger.exception("Production analytics follow-up tick failed.")
            try:
                quality_review = await self._run_production_quality_review_tick()
                if quality_review and int(quality_review.get("blocked_count") or 0) > 0:
                    logger.warning(
                        "Production quality review blocked %s job(s).",
                        quality_review.get("blocked_count"),
                    )
            except Exception:
                logger.exception("Production quality review tick failed.")
            try:
                remediation = await self._run_production_quality_remediation_tick()
                if remediation and int(remediation.get("started_count") or 0) > 0:
                    logger.warning(
                        "Production quality remediation started %s regeneration job(s).",
                        remediation.get("started_count"),
                    )
            except Exception:
                logger.exception("Production quality remediation tick failed.")
            try:
                autopilot = await self._run_production_autopilot_tick()
                if autopilot and int(autopilot.get("started_count") or 0) > 0:
                    logger.info("Production autopilot started %s job(s).", autopilot.get("started_count"))
            except Exception:
                logger.exception("Production autopilot tick failed.")
            try:
                investment_autopilot = await self._run_investment_paper_autopilot_tick()
                if investment_autopilot and investment_autopilot.get("status") == "completed":
                    logger.info(
                        "Investment paper autopilot completed | run=%s trades=%s alert=%s",
                        investment_autopilot.get("id"),
                        investment_autopilot.get("executed_trade_count"),
                        (investment_autopilot.get("alerts") or {}).get("required")
                        if isinstance(investment_autopilot.get("alerts"), dict)
                        else None,
                    )
            except Exception:
                logger.exception("Investment paper autopilot tick failed.")
            try:
                refreshed = await self._run_intelligence_refresh_tick()
                if refreshed:
                    logger.info("Intelligence refresh tick completed.")
            except Exception:
                logger.exception("Intelligence refresh scheduler tick failed.")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=SCHEDULER_TICK_SECONDS)
            except asyncio.TimeoutError:
                continue

    async def _process_trusted_auto_tick(self) -> list[dict[str, object]]:
        return await asyncio.to_thread(self.process_trusted_auto_once)

    async def _run_trend_watch_tick(self) -> dict[str, object] | None:
        def run_watch() -> dict[str, object] | None:
            from atnos.services.trend_watch_service import TrendWatchService

            with self.database.session_scope() as session:
                return TrendWatchService(session, self.settings).run_due_watch_if_needed()

        return await asyncio.to_thread(run_watch)

    async def _run_production_analytics_followups_tick(self) -> list[dict[str, object]]:
        def run_followups() -> list[dict[str, object]]:
            return self.process_production_analytics_followups_once()

        return await asyncio.to_thread(run_followups)

    async def _run_intelligence_refresh_tick(self) -> bool:
        now = time.monotonic()
        min_interval = INTELLIGENCE_REFRESH_INTERVAL_MINUTES * 60
        if self._last_intelligence_refresh_at and now - self._last_intelligence_refresh_at < min_interval:
            return False
        self._last_intelligence_refresh_at = now
        return await asyncio.to_thread(self.process_intelligence_refresh_once)

    async def _run_production_autopilot_tick(self) -> dict[str, object] | None:
        if self.settings.app_env == "test" or not self.settings.shorts_autopilot_enabled:
            return None
        now = time.monotonic()
        min_interval = self.settings.shorts_autopilot_interval_minutes * 60
        if self._last_production_autopilot_at and now - self._last_production_autopilot_at < min_interval:
            return None
        self._last_production_autopilot_at = now
        return await asyncio.to_thread(self.process_production_autopilot_once)

    async def _run_production_quality_review_tick(self) -> dict[str, object] | None:
        if self.settings.app_env == "test" or not self.settings.shorts_quality_review_scheduler_enabled:
            return None
        now = time.monotonic()
        min_interval = self.settings.shorts_quality_review_interval_minutes * 60
        if self._last_production_quality_review_at and now - self._last_production_quality_review_at < min_interval:
            return None
        self._last_production_quality_review_at = now
        return await asyncio.to_thread(self.process_production_quality_review_once)

    async def _run_production_quality_remediation_tick(self) -> dict[str, object] | None:
        if self.settings.app_env == "test" or not self.settings.shorts_quality_auto_remediation_enabled:
            return None
        now = time.monotonic()
        min_interval = self.settings.shorts_quality_auto_remediation_interval_minutes * 60
        if (
            self._last_production_quality_remediation_at
            and now - self._last_production_quality_remediation_at < min_interval
        ):
            return None
        self._last_production_quality_remediation_at = now
        return await asyncio.to_thread(self.process_production_quality_remediation_once)

    async def _run_investment_paper_autopilot_tick(self) -> dict[str, object] | None:
        if self.settings.app_env == "test" or not self.settings.investment_paper_autopilot_enabled:
            return None
        now = time.monotonic()
        min_interval = self.settings.investment_paper_autopilot_interval_minutes * 60
        if self._last_investment_paper_autopilot_at and now - self._last_investment_paper_autopilot_at < min_interval:
            return None
        self._last_investment_paper_autopilot_at = now
        return await asyncio.to_thread(self.process_investment_paper_autopilot_once)

    def process_trusted_auto_once(self) -> list[dict[str, object]]:
        from atnos.services.trusted_auto_service import TrustedAutoService

        with self._trusted_auto_kick_lock:
            with self.database.session_scope() as session:
                return TrustedAutoService(session, self.settings).process_due_jobs()

    def process_intelligence_refresh_once(self) -> bool:
        from atnos.intelligence.scheduler import IntelligenceRefreshService

        asyncio.run(IntelligenceRefreshService(self.database, self.settings).refresh_due())
        return True

    def process_production_analytics_followups_once(self) -> list[dict[str, object]]:
        from atnos.production.private_upload import run_due_analytics_followups

        # Both the lightweight runtime loop and APScheduler can wake up at the
        # same time. One guarded pass is enough; repeating it only hammers OAuth
        # when a channel has expired authorization.
        with self._production_analytics_followup_lock:
            now = time.monotonic()
            if now - self._last_production_analytics_followup_at < SCHEDULER_TICK_SECONDS * 0.8:
                return []
            self._last_production_analytics_followup_at = now
            with self.database.session_scope() as session:
                return run_due_analytics_followups(
                    settings=self.settings,
                    session=session,
                    limit=PRODUCTION_ANALYTICS_FOLLOWUP_LIMIT,
                )

    def process_production_autopilot_once(self) -> dict[str, object]:
        from atnos.production.content_loop import AutonomousContentLoopService

        return asyncio.run(
            AutonomousContentLoopService(
                self.settings,
                session_scope=self.database.session_scope,
            ).run_once(
                max_jobs=self.settings.shorts_autopilot_max_jobs_per_tick,
            )
        )

    def process_production_quality_review_once(self) -> dict[str, object]:
        from atnos.production.quality_scheduler import ProductionQualityScheduler

        return ProductionQualityScheduler(self.settings).audit_due_jobs(
            limit=self.settings.shorts_quality_review_max_jobs_per_tick
        )

    def process_production_quality_remediation_once(self) -> dict[str, object]:
        from atnos.production.quality_remediation import ProductionQualityRemediationService

        return asyncio.run(
            ProductionQualityRemediationService(self.settings).run_due(
                limit=self.settings.shorts_quality_auto_remediation_max_jobs_per_tick
            )
        )

    def process_investment_paper_autopilot_once(self) -> dict[str, object]:
        from atnos.investment.cockpit import InvestmentCockpitService
        from atnos.investment.notifications import dispatch_investment_autopilot_alert_email

        result = InvestmentCockpitService(self.settings).run_scheduled_paper_autopilot(force=False)
        alerts = result.get("alerts") if isinstance(result.get("alerts"), dict) else {}
        if bool(alerts.get("required")):
            with self.database.session_scope() as session:
                result["gmail_alert"] = dispatch_investment_autopilot_alert_email(
                    self.settings,
                    session,
                    result,
                )
        return result

    def process_proactive_morning_briefing_once(self) -> dict[str, object]:
        from atnos.services.proactive_briefing_service import ProactiveBriefingService
        from atnos.services.runtime_settings_service import RuntimeSettingsService

        with self.database.session_scope() as session:
            controls = RuntimeSettingsService(session, self.settings).snapshot()
            if not bool(controls.scheduler_enabled):
                return {"status": "skipped", "reason": "scheduler_disabled"}
            service = ProactiveBriefingService(
                session,
                settings=self.settings,
                provider_registry=self.provider_registry,
            )
            if not service.should_emit_scheduled_morning():
                return {"status": "skipped", "reason": "already_briefed_today"}
            result = service.create_briefing_message(trigger="morning_cron")
            return {
                "status": "delivered",
                "conversation_id": result.conversation_id,
                "assistant_message_id": result.assistant_message_id,
            }

    def kick_trusted_auto(self) -> None:
        thread = threading.Thread(
            target=self.process_trusted_auto_once,
            name="atnos-trusted-auto-kick",
            daemon=True,
        )
        thread.start()

    def _start_scheduled_tasks_runtime(self) -> None:
        if not self.settings.scheduled_tasks_enabled:
            logger.info("Scheduled tasks runtime disabled by settings.")
            return
        if self._scheduler is not None and self._scheduler.running:
            return
        from apscheduler.jobstores.memory import MemoryJobStore
        from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
        from apscheduler.schedulers.background import BackgroundScheduler

        jobstore_url = self.settings.resolved_database_url
        if jobstore_url.startswith("sqlite:///"):
            # APScheduler updates next_run_time every tick. Keep that write lock
            # separate from long chat/application transactions. Task definitions
            # remain in the application DB and are reconciled below as before.
            jobstore_path = self.settings.runtime_dir / "scheduler-jobs.sqlite"
            jobstore_path.parent.mkdir(parents=True, exist_ok=True)
            jobstore_url = f"sqlite:///{jobstore_path.as_posix()}"
        jobstore = (
            MemoryJobStore()
            if self.settings.app_env == "test"
            else SQLAlchemyJobStore(url=jobstore_url)
        )
        self._scheduler = BackgroundScheduler(
            jobstores={"default": jobstore},
            timezone="Europe/Warsaw",
        )
        self._scheduler.start()
        self._scheduler.add_job(
            run_intelligence_refresh_job,
            "interval",
            minutes=INTELLIGENCE_REFRESH_INTERVAL_MINUTES,
            id=INTELLIGENCE_REFRESH_JOB_ID,
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=60,
        )
        self._scheduler.add_job(
            run_production_analytics_followup_job,
            "interval",
            seconds=SCHEDULER_TICK_SECONDS,
            id=PRODUCTION_ANALYTICS_FOLLOWUP_JOB_ID,
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=60,
        )
        self._scheduler.add_job(
            run_production_autopilot_job,
            "interval",
            minutes=self.settings.shorts_autopilot_interval_minutes,
            id=PRODUCTION_AUTOPILOT_JOB_ID,
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=60,
        )
        self._scheduler.add_job(
            run_production_quality_review_job,
            "interval",
            minutes=self.settings.shorts_quality_review_interval_minutes,
            id=PRODUCTION_QUALITY_REVIEW_JOB_ID,
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=60,
        )
        self._scheduler.add_job(
            run_production_quality_remediation_job,
            "interval",
            minutes=self.settings.shorts_quality_auto_remediation_interval_minutes,
            id=PRODUCTION_QUALITY_REMEDIATION_JOB_ID,
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=60,
        )
        self._scheduler.add_job(
            run_investment_paper_autopilot_job,
            "interval",
            minutes=self.settings.investment_paper_autopilot_interval_minutes,
            id=INVESTMENT_PAPER_AUTOPILOT_JOB_ID,
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=60,
        )
        from apscheduler.triggers.cron import CronTrigger

        self._scheduler.add_job(
            run_proactive_morning_briefing_job,
            trigger=CronTrigger(hour=8, minute=0, timezone="Europe/Warsaw"),
            id=PROACTIVE_MORNING_BRIEFING_JOB_ID,
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=3600,
        )
        self._sync_all_scheduled_tasks()

    def _sync_all_scheduled_tasks(self) -> None:
        if self._scheduler is None:
            return
        from apscheduler.triggers.cron import CronTrigger
        from atnos.services.scheduled_task_service import ScheduledTaskService

        scheduled_tasks: list[tuple[int, str, bool]] = []
        with self.database.session_scope() as session:
            service = ScheduledTaskService(session, self.settings)
            all_tasks = service.tasks.list_all()
            for task in all_tasks:
                task_id = int(task.id)
                cron_spec = service.validate_cron_spec(task.cron_spec) if bool(task.is_active) else str(task.cron_spec)
                scheduled_tasks.append((task_id, cron_spec, bool(task.is_active)))

        active_ids: set[int] = set()
        next_run_updates: list[tuple[int, Any]] = []
        for task_id, cron_spec, is_active in scheduled_tasks:
            if not is_active:
                continue
            active_ids.add(task_id)
            job = self._scheduler.add_job(
                run_scheduled_task_job,
                trigger=CronTrigger.from_crontab(cron_spec, timezone="Europe/Warsaw"),
                id=self._scheduled_task_job_id(task_id),
                kwargs={"task_id": task_id},
                replace_existing=True,
                coalesce=True,
                misfire_grace_time=60,
            )
            next_run_updates.append((task_id, job.next_run_time))

        if next_run_updates:
            with self.database.session_scope() as session:
                service = ScheduledTaskService(session, self.settings)
                for task_id, next_run_at in next_run_updates:
                    task = service.tasks.get_by_id(task_id)
                    if task is None:
                        continue
                    task.next_run_at = next_run_at
                    service.tasks.save(task)

        existing = [job.id for job in self._scheduler.get_jobs() if job.id.startswith("scheduled-task:")]
        for job_id in existing:
            _, task_id_text = job_id.split(":", maxsplit=1)
            if int(task_id_text) not in active_ids:
                try:
                    self._scheduler.remove_job(job_id)
                except Exception:
                    pass

    def _run_scheduled_task(self, task_id: int) -> None:
        logger.info("Running scheduled task %s.", task_id)
        try:
            from atnos.db.models.enums import MessageRole
            from atnos.services.chat_orchestration_service import ChatOrchestrationService
            from atnos.services.conversation_service import ConversationTurnRequest
            from atnos.services.scheduled_task_service import ScheduledTaskService

            with self.database.session_scope() as session:
                scheduled_tasks = ScheduledTaskService(session, self.settings)
                task = scheduled_tasks.tasks.get_by_id(task_id)
                if task is None or not task.is_active:
                    return
                service = ChatOrchestrationService(
                    session,
                    settings=self.settings,
                    provider_registry=self.provider_registry,
                    plugin_registry=self.plugin_registry,
                    summary_writeback_factory=self.summary_writeback_factory,
                )
                result = service.handle_turn(
                    ConversationTurnRequest(
                        content=task.prompt_template,
                        conversation_id=task.conversation_id,
                        conversation_title="Zaplanowane zadanie" if task.conversation_id is None else None,
                        project_id=task.project_id,
                        prompt_improved=False,
                        force_external=False,
                        attachments=tuple(),
                        message_role=MessageRole.SYSTEM,
                        message_metadata_json={"scheduled_task_id": task.id, "injected": True},
                        assistant_metadata_json={"triggered_by": task.id},
                    )
                )
                next_run = None
                if self._scheduler is not None:
                    job = self._scheduler.get_job(self._scheduled_task_job_id(task_id))
                    next_run = job.next_run_time if job is not None else None
                if task.conversation_id is None:
                    task.conversation_id = result.conversation.id
                scheduled_tasks.update_runtime_state(
                    task.id,
                    next_run_at=next_run,
                    last_run_at=result.assistant_message.created_at,
                    last_run_status="completed",
                )
        except Exception:
            logger.exception("Scheduled task %s failed.", task_id)
            try:
                with self.database.session_scope() as session:
                    scheduled_tasks = ScheduledTaskService(session, self.settings)
                    next_run = None
                    if self._scheduler is not None:
                        job = self._scheduler.get_job(self._scheduled_task_job_id(task_id))
                        next_run = job.next_run_time if job is not None else None
                    scheduled_tasks.update_runtime_state(
                        task_id,
                        next_run_at=next_run,
                        last_run_status="failed",
                    )
            except Exception:
                logger.exception("Failed to persist scheduled task failure state for %s.", task_id)

    @staticmethod
    def _scheduled_task_job_id(task_id: int) -> str:
        return f"scheduled-task:{task_id}"
