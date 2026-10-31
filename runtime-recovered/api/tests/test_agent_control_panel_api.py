from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.repositories.execution_run_repository import ExecutionRunRepository
from atnos.repositories.project_repository import ProjectRepository
from test_support import create_test_app, make_temp_dir


class AgentControlPanelApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        key_path = self.root / "atnos_lab"
        key_path.write_text("fake-key", encoding="utf-8")
        self.app = create_test_app(self.root)
        self.app.state.settings.atnos_ubuntu_host = "198.51.100.216"
        self.app.state.settings.atnos_ubuntu_key = key_path
        self.addCleanup(self.app.state.database.dispose)

    def test_status_tools_logs_and_pending_approvals_endpoints(self) -> None:
        with TestClient(self.app) as client:
            self._ensure_project(client.app)
            status_response = client.get("/agent/status")
            self.assertEqual(status_response.status_code, 200)
            status_payload = status_response.json()
            self.assertIn("agent", status_payload)
            self.assertIn("ubuntu", status_payload)
            self.assertIn("browser", status_payload)
            self.assertIn("short_pipeline", status_payload)
            self.assertIn("monetization_factory", status_payload)
            self.assertIn("recent_artifacts", status_payload)
            self.assertTrue(status_payload["ubuntu"]["ready"])
            self.assertEqual(status_payload["monetization_factory"]["revenue_vector"], "automated_shorts_factory")
            self.assertTrue(status_payload["monetization_factory"]["private_upload_only"])
            self.assertIn("hook_variants.json", status_payload["monetization_factory"]["required_artifacts"])

            tools_response = client.get("/agent/tools")
            self.assertEqual(tools_response.status_code, 200)
            tool_keys = {item["key"] for item in tools_response.json()["tools"]}
            self.assertIn("monetization_shorts_factory", tool_keys)
            self.assertIn("ubuntu_control", tool_keys)
            self.assertIn("llm_provider_router", tool_keys)
            self.assertIn("github_connector", tool_keys)
            self.assertIn("linear_connector", tool_keys)
            self.assertIn("screen_capture_visual_qa", tool_keys)

            logs_response = client.get("/agent/logs/recent")
            self.assertEqual(logs_response.status_code, 200)
            self.assertIn("decisions", logs_response.json())
            self.assertIn("tool_calls", logs_response.json())

            preview_response = client.post(
                "/agent/action-previews",
                json={
                    "kind": "file_write",
                    "title": "Test approval",
                    "summary": "Write a controlled test file.",
                    "impact_summary": "No external side effect.",
                    "preview_payload": {"path": "data/test.md"},
                    "risk_level": "medium",
                },
            )
            self.assertEqual(preview_response.status_code, 200)
            pending_response = client.get("/agent/pending-approvals")
            self.assertEqual(pending_response.status_code, 200)
            self.assertEqual(len(pending_response.json()["pending_approvals"]), 1)

    def test_approve_and_reject_action_wrappers(self) -> None:
        with TestClient(self.app) as client:
            self._ensure_project(client.app)
            preview_response = client.post(
                "/agent/action-previews",
                json={
                    "kind": "file_write",
                    "title": "Approve me",
                    "summary": "Approve wrapper smoke.",
                    "preview_payload": {},
                },
            )
            run_id = preview_response.json()["preview"]["execution_run_id"]
            with patch(
                "atnos.services.agent_control_panel_service.AgentExecutionService",
            ) as execution_service:
                execution_service.return_value.execute_approved_preview.return_value = {
                    "execution_run_id": run_id,
                    "status": "done",
                }
                approve_response = client.post("/agent/approve-action", json={"run_id": run_id})
            self.assertEqual(approve_response.status_code, 200)
            self.assertTrue(approve_response.json()["ok"])
            self.assertEqual(approve_response.json()["run"]["status"], "done")

            preview_response = client.post(
                "/agent/action-previews",
                json={
                    "kind": "file_write",
                    "title": "Reject me",
                    "summary": "Reject wrapper smoke.",
                    "preview_payload": {},
                },
            )
            run_id = preview_response.json()["preview"]["execution_run_id"]
            reject_response = client.post(
                "/agent/reject-action",
                json={"run_id": run_id, "reason": "not now"},
            )
            self.assertEqual(reject_response.status_code, 200)
            self.assertTrue(reject_response.json()["ok"])
            self.assertEqual(reject_response.json()["preview"]["status"], "blocked")

    def test_emergency_stop_interrupts_active_runs_and_logs_without_deleting_data(self) -> None:
        with TestClient(self.app) as client:
            self._ensure_project(client.app)
            with client.app.state.database.session_scope() as session:
                project = ProjectRepository(session).list_all()[0]
                run = ExecutionRunRepository(session).create_run(
                    project_id=project.id,
                    scheduler_job_id=None,
                    account_id=None,
                    trust_grant_id=None,
                    run_type="agent.test",
                    status="running",
                    goal="Long running test task",
                    connector_key=None,
                    platform_key=None,
                    risk_level="medium",
                    input_json={},
                    output_json={},
                    artifacts_json={},
                    error_message=None,
                    started_at=None,
                    finished_at=None,
                )
                run_id = run.id

            with patch(
                "atnos.services.agent_control_panel_service.stop_remote_atnos_processes",
                return_value={"exit_code": 0, "stdout": "stopped", "stderr": ""},
            ):
                response = client.post("/agent/emergency-stop")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["ubuntu_stop"]["ok"])
            self.assertEqual(payload["interrupted_runs"][0]["execution_run_id"], run_id)

            with client.app.state.database.session_scope() as session:
                saved = ExecutionRunRepository(session).get_run(run_id)
                self.assertIsNotNone(saved)
                self.assertEqual(saved.status, "interrupted")
                self.assertIn("Emergency stop", saved.error_message)

            decisions_log = client.app.state.settings.agent_decisions_log_path.read_text(encoding="utf-8")
            self.assertIn("agent_emergency_stop", decisions_log)

    @staticmethod
    def _ensure_project(app) -> None:
        with app.state.database.session_scope() as session:
            projects = ProjectRepository(session)
            if not projects.list_all():
                projects.create("Atnos test project")


if __name__ == "__main__":
    unittest.main()
