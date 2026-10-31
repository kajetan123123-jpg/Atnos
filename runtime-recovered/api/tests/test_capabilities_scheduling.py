from __future__ import annotations

from pathlib import Path
import sys
import unittest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import build_test_settings, make_temp_dir

from atnos.db.migrations import upgrade_database
from atnos.db.session import DatabaseManager
from atnos.services.bootstrap_service import BootstrapService
from atnos.services.scheduled_task_service import ScheduledTaskService


class SchedulingCapabilitiesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.settings = build_test_settings(self.root)
        self.database = DatabaseManager(self.settings)
        self.addCleanup(self.database.dispose)
        self.database.initialize()
        upgrade_database(self.settings)
        with self.database.session_scope() as session:
            BootstrapService(session, self.settings).ensure_local_first_defaults()

    def test_parses_polish_schedule_and_persists_task(self) -> None:
        with self.database.session_scope() as session:
            service = ScheduledTaskService(session, self.settings)
            preview = service.parse_natural_schedule(
                request_text="kazdy poniedzialek o 9 zrob przeglad projektu",
            )
            self.assertEqual(preview.cron_spec, "0 9 * * 1")
            task = service.create_task(
                conversation_id=None,
                project_id=None,
                user_id=None,
                prompt_template=preview.prompt_template,
                cron_spec=preview.cron_spec,
            )
            serialized = service.serialize_task(task)

        self.assertEqual(serialized["cron_spec"], "0 9 * * 1")
        self.assertTrue(serialized["is_active"])

    def test_rejects_second_level_schedule(self) -> None:
        with self.database.session_scope() as session:
            service = ScheduledTaskService(session, self.settings)
            with self.assertRaisesRegex(Exception, "1 minuta"):
                service.validate_cron_spec("* * * * *")
