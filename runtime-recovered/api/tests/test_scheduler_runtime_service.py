from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import create_test_app, make_temp_dir

from atnos.services.scheduler_runtime_service import (
    INTELLIGENCE_REFRESH_JOB_ID,
    INVESTMENT_PAPER_AUTOPILOT_JOB_ID,
    PRODUCTION_AUTOPILOT_JOB_ID,
    PRODUCTION_ANALYTICS_FOLLOWUP_JOB_ID,
    PRODUCTION_QUALITY_REVIEW_JOB_ID,
    PRODUCTION_QUALITY_REMEDIATION_JOB_ID,
    SchedulerRuntimeService,
    get_scheduler_runtime_status,
)
from atnos.services.summary_writeback_service import SummaryWritebackService


class SchedulerRuntimeServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_process_trusted_auto_tick_offloads_sync_work(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        service = SchedulerRuntimeService(
            app.state.database,
            app.state.settings,
            provider_registry=app.state.providers,
            plugin_registry=app.state.plugins,
            summary_writeback_factory=SummaryWritebackService,
        )

        mocked_to_thread = AsyncMock(return_value=[{"execution_run_id": 1}])
        with patch(
            "atnos.services.scheduler_runtime_service.asyncio.to_thread",
            mocked_to_thread,
        ):
            result = asyncio.run(service._process_trusted_auto_tick())

        self.assertEqual(result, [{"execution_run_id": 1}])
        mocked_to_thread.assert_awaited_once()

    def test_run_trend_watch_tick_offloads_sync_work(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        service = SchedulerRuntimeService(
            app.state.database,
            app.state.settings,
            provider_registry=app.state.providers,
            plugin_registry=app.state.plugins,
            summary_writeback_factory=SummaryWritebackService,
        )

        mocked_to_thread = AsyncMock(return_value={"topic_query": "ai", "summary": {"signals_count": 3}})
        with patch(
            "atnos.services.scheduler_runtime_service.asyncio.to_thread",
            mocked_to_thread,
        ):
            result = asyncio.run(service._run_trend_watch_tick())

        self.assertEqual(result, {"topic_query": "ai", "summary": {"signals_count": 3}})
        mocked_to_thread.assert_awaited_once()

    def test_run_production_analytics_followups_tick_offloads_due_followups(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        service = SchedulerRuntimeService(
            app.state.database,
            app.state.settings,
            provider_registry=app.state.providers,
            plugin_registry=app.state.plugins,
            summary_writeback_factory=SummaryWritebackService,
        )

        mocked_to_thread = AsyncMock(return_value=[{"followup_id": 1, "status": "completed"}])
        with patch(
            "atnos.services.scheduler_runtime_service.asyncio.to_thread",
            mocked_to_thread,
        ):
            result = asyncio.run(service._run_production_analytics_followups_tick())

        self.assertEqual(result, [{"followup_id": 1, "status": "completed"}])
        mocked_to_thread.assert_awaited_once()

    def test_process_production_analytics_followups_once_runs_due_followups(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        service = SchedulerRuntimeService(
            app.state.database,
            app.state.settings,
            provider_registry=app.state.providers,
            plugin_registry=app.state.plugins,
            summary_writeback_factory=SummaryWritebackService,
        )

        captured: dict[str, object] = {}

        def fake_run_due(*, settings, session, limit):
            captured["settings"] = settings
            captured["session"] = session
            captured["limit"] = limit
            return [{"followup_id": 7, "status": "completed"}]

        with patch(
            "atnos.production.private_upload.run_due_analytics_followups",
            fake_run_due,
        ):
            result = service.process_production_analytics_followups_once()

        self.assertEqual(result, [{"followup_id": 7, "status": "completed"}])
        self.assertIs(captured["settings"], app.state.settings)
        self.assertEqual(captured["limit"], 5)

    def test_run_intelligence_refresh_tick_offloads_and_throttles_refresh(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        service = SchedulerRuntimeService(
            app.state.database,
            app.state.settings,
            provider_registry=app.state.providers,
            plugin_registry=app.state.plugins,
            summary_writeback_factory=SummaryWritebackService,
        )

        mocked_to_thread = AsyncMock(return_value=True)
        with patch(
            "atnos.services.scheduler_runtime_service.asyncio.to_thread",
            mocked_to_thread,
        ):
            first = asyncio.run(service._run_intelligence_refresh_tick())
            second = asyncio.run(service._run_intelligence_refresh_tick())

        self.assertTrue(first)
        self.assertFalse(second)
        mocked_to_thread.assert_awaited_once()

    def test_start_scheduled_tasks_runtime_registers_system_jobs(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        app.state.settings.scheduled_tasks_enabled = True

        service = SchedulerRuntimeService(
            app.state.database,
            app.state.settings,
            provider_registry=app.state.providers,
            plugin_registry=app.state.plugins,
            summary_writeback_factory=SummaryWritebackService,
        )
        self.addCleanup(lambda: service._scheduler.shutdown(wait=False) if service._scheduler is not None else None)

        with patch.object(service, "_sync_all_scheduled_tasks", lambda: None):
            service._start_scheduled_tasks_runtime()

        self.assertIsNotNone(service._scheduler)
        assert service._scheduler is not None
        self.assertIsNotNone(service._scheduler.get_job(INTELLIGENCE_REFRESH_JOB_ID))
        self.assertIsNotNone(service._scheduler.get_job(PRODUCTION_ANALYTICS_FOLLOWUP_JOB_ID))
        self.assertIsNotNone(service._scheduler.get_job(PRODUCTION_AUTOPILOT_JOB_ID))
        self.assertIsNotNone(service._scheduler.get_job(PRODUCTION_QUALITY_REVIEW_JOB_ID))
        self.assertIsNotNone(service._scheduler.get_job(PRODUCTION_QUALITY_REMEDIATION_JOB_ID))
        self.assertIsNotNone(service._scheduler.get_job(INVESTMENT_PAPER_AUTOPILOT_JOB_ID))

    def test_restart_recovery_schedules_each_video_resume_once(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        service = SchedulerRuntimeService(
            app.state.database,
            app.state.settings,
            provider_registry=app.state.providers,
            plugin_registry=app.state.plugins,
            summary_writeback_factory=SummaryWritebackService,
        )

        async def exercise() -> None:
            resume = AsyncMock(return_value={"id": "job-a", "status": "qa_passed"})
            with patch.object(service, "_resume_production_job", resume):
                service._schedule_production_resumes(["job-a", "job-a", "job-b"])
                tasks = tuple(service._production_resume_tasks)
                self.assertEqual(len(tasks), 2)
                await asyncio.gather(*tasks)
            self.assertEqual(sorted(call.args[0] for call in resume.await_args_list), ["job-a", "job-b"])

        asyncio.run(exercise())

    def test_runtime_status_counts_apscheduler_system_jobs_as_automatic_pollers(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        app.state.settings.scheduled_tasks_enabled = True

        service = SchedulerRuntimeService(
            app.state.database,
            app.state.settings,
            provider_registry=app.state.providers,
            plugin_registry=app.state.plugins,
            summary_writeback_factory=SummaryWritebackService,
        )
        self.addCleanup(lambda: service._scheduler.shutdown(wait=False) if service._scheduler is not None else None)

        with patch.object(service, "_sync_all_scheduled_tasks", lambda: None):
            service._start_scheduled_tasks_runtime()
        import atnos.services.scheduler_runtime_service as scheduler_runtime_service

        previous = scheduler_runtime_service._ACTIVE_RUNTIME
        scheduler_runtime_service._ACTIVE_RUNTIME = service
        self.addCleanup(lambda: setattr(scheduler_runtime_service, "_ACTIVE_RUNTIME", previous))

        status = get_scheduler_runtime_status()

        self.assertTrue(status["apscheduler_running"])
        self.assertIn(INTELLIGENCE_REFRESH_JOB_ID, status["system_job_ids"])
        self.assertIn(PRODUCTION_ANALYTICS_FOLLOWUP_JOB_ID, status["system_job_ids"])
        self.assertIn(PRODUCTION_AUTOPILOT_JOB_ID, status["system_job_ids"])
        self.assertIn(PRODUCTION_QUALITY_REVIEW_JOB_ID, status["system_job_ids"])
        self.assertIn(PRODUCTION_QUALITY_REMEDIATION_JOB_ID, status["system_job_ids"])
        self.assertIn(INVESTMENT_PAPER_AUTOPILOT_JOB_ID, status["system_job_ids"])
        self.assertTrue(status["intelligence_refresh_automatic"])
        self.assertTrue(status["production_analytics_followups_automatic"])
        self.assertTrue(status["production_autopilot_automatic"])
        self.assertTrue(status["production_quality_review_automatic"])
        self.assertTrue(status["production_quality_remediation_automatic"])
        self.assertTrue(status["investment_paper_autopilot_automatic"])


if __name__ == "__main__":
    unittest.main()
