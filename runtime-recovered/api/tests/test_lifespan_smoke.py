from __future__ import annotations

from pathlib import Path
import sys
import unittest

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import create_test_app, make_temp_dir


class LifespanSmokeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.root = Path(self.temp_dir.__enter__())

    def tearDown(self) -> None:
        self.temp_dir.__exit__(None, None, None)

    def test_cold_start_health_is_clean(self) -> None:
        app = create_test_app(self.root, scheduled_tasks_enabled=True)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app) as client:
            response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["database"], "ok")
        self.assertGreaterEqual(payload["providers_registered"], 5)
