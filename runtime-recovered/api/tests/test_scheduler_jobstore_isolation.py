"""Real SQLite jobstore tests, with dispatch paused and no model or media work."""
import sqlite3
from unittest.mock import patch

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import text

from test_support import create_test_app
from atnos.services.scheduler_runtime_service import SchedulerRuntimeService, INTELLIGENCE_REFRESH_JOB_ID
from atnos.services.summary_writeback_service import SummaryWritebackService
from atnos.db.base import Base
from atnos.db.models.scheduled_task import ScheduledTask


def test_scheduler_timing_write_does_not_contend_with_chat_transaction(tmp_path):
    app = create_test_app(tmp_path, scheduled_tasks_enabled=True)
    app.state.settings.app_env = 'development'  # Exercise the real persistent jobstore.
    runtime = SchedulerRuntimeService(app.state.database, app.state.settings,
        provider_registry=app.state.providers, plugin_registry=app.state.plugins,
        summary_writeback_factory=SummaryWritebackService)
    original_start = BackgroundScheduler.start
    try:
        with patch.object(BackgroundScheduler, 'start', lambda scheduler: original_start(scheduler, paused=True)), \
             patch.object(runtime, '_sync_all_scheduled_tasks'):
            runtime._start_scheduled_tasks_runtime()
        jobstore = runtime._scheduler._jobstores['default']
        with jobstore.engine.connect() as conn:
            conn.execute(text('PRAGMA busy_timeout=30'))
        # A chat's open write transaction must not prevent a scheduler timing update.
        with sqlite3.connect(app.state.settings.sqlite_file) as writer:
            writer.execute('CREATE TABLE IF NOT EXISTS isolation_probe(id INTEGER)')
            writer.commit()
            writer.execute('BEGIN IMMEDIATE')
            try:
                runtime._scheduler.modify_job(INTELLIGENCE_REFRESH_JOB_ID, name='Verified timing update')
            finally:
                writer.rollback()
        assert runtime._scheduler.get_job(INTELLIGENCE_REFRESH_JOB_ID).name == 'Verified timing update'
        assert jobstore.engine.url.database != str(app.state.settings.sqlite_file)
    finally:
        if runtime._scheduler is not None:
            runtime._scheduler.shutdown(wait=True)
        app.state.database.dispose()


def test_task_definitions_and_run_history_survive_jobstore_restart(tmp_path):
    app = create_test_app(tmp_path, scheduled_tasks_enabled=True)
    app.state.settings.app_env = 'development'
    Base.metadata.create_all(app.state.database.engine)
    with app.state.database.session_scope() as session:
        task = ScheduledTask(prompt_template='Isolated test; never execute', cron_spec='0 8 * * *',
                             is_active=True, last_run_status='completed')
        inactive = ScheduledTask(prompt_template='Inactive test', cron_spec='0 9 * * *', is_active=False)
        session.add_all([task, inactive])
        session.flush()
        task_id, inactive_id = task.id, inactive.id
    original_start = BackgroundScheduler.start
    try:
        for _ in range(2):
            runtime = SchedulerRuntimeService(app.state.database, app.state.settings,
                provider_registry=app.state.providers, plugin_registry=app.state.plugins,
                summary_writeback_factory=SummaryWritebackService)
            try:
                with patch.object(BackgroundScheduler, 'start', lambda scheduler: original_start(scheduler, paused=True)):
                    runtime._start_scheduled_tasks_runtime()
                assert runtime._scheduler.get_job(f'scheduled-task:{task_id}') is not None
                assert runtime._scheduler.get_job(f'scheduled-task:{inactive_id}') is None
                with app.state.database.session_scope() as session:
                    restored = session.get(ScheduledTask, task_id)
                    assert restored.prompt_template == 'Isolated test; never execute'
                    assert restored.last_run_status == 'completed'
                    assert restored.next_run_at is not None
            finally:
                if runtime._scheduler is not None:
                    runtime._scheduler.shutdown(wait=True)
    finally:
        app.state.database.dispose()
