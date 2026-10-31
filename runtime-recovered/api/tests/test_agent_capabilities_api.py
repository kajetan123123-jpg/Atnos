from __future__ import annotations

from pathlib import Path
import sys
import unittest

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import create_test_app, enable_local_file_write, make_temp_dir


class AgentCapabilitiesApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.app = create_test_app(self.root)
        self.addCleanup(self.app.state.database.dispose)

    def test_preview_and_execute_docx_artifact(self) -> None:
        with TestClient(self.app) as client:
            enable_local_file_write(client.app)

            preview_response = client.post(
                "/agent/preview",
                json={
                    "action": "generate_docx_document",
                    "conversation_id": 1,
                    "payload": {
                        "title": "Decyzje projektu",
                        "sections": [
                            {
                                "heading": "Decyzje",
                                "paragraphs": [
                                    "Wchodzimy w local-first.",
                                    "Zostaje tylko Chat i Ustawienia.",
                                ],
                            }
                        ],
                    },
                },
            )
            self.assertEqual(preview_response.status_code, 200)
            preview_json = preview_response.json()["preview"]
            self.assertEqual(preview_json["action"], "generate_docx_document")
            self.assertIn("dokument Word", preview_json["summary"])

            execute_response = client.post(
                "/agent/execute",
                json={
                    "action": "generate_docx_document",
                    "conversation_id": 1,
                    "payload": preview_json["payload"],
                },
            )
            self.assertEqual(execute_response.status_code, 200)
            artifact = execute_response.json()["artifact"]
            self.assertEqual(artifact["file_extension"], ".docx")
            self.assertTrue((self.root / artifact["storage_path"]).exists())

    def test_preview_and_execute_scheduled_task(self) -> None:
        app = create_test_app(self.root, scheduled_tasks_enabled=True)
        self.addCleanup(app.state.database.dispose)
        with TestClient(app) as client:
            preview_response = client.post(
                "/agent/preview",
                json={
                    "action": "schedule_recurring_task",
                    "project_id": 1,
                    "payload": {
                        "request_text": "kazdy poniedzialek o 9 zrob przeglad projektu",
                    },
                },
            )
            self.assertEqual(preview_response.status_code, 200)
            preview = preview_response.json()["preview"]
            self.assertEqual(preview["action"], "schedule_recurring_task")
            self.assertEqual(preview["payload"]["cron_spec"], "0 9 * * 1")

            execute_response = client.post(
                "/agent/execute",
                json={
                    "action": "schedule_recurring_task",
                    "project_id": 1,
                    "payload": preview["payload"],
                },
            )
            self.assertEqual(execute_response.status_code, 200)
            task = execute_response.json()["scheduled_task"]
            self.assertEqual(task["project_id"], 1)
            self.assertEqual(task["cron_spec"], "0 9 * * 1")
