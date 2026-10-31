from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import time
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import SecretStr

from atnos.repositories.local_file_grant_repository import LocalFileGrantRepository
from atnos.repositories.project_repository import ProjectRepository
from atnos.repositories.user_repository import UserRepository
from atnos.services.browser_recipe_runner_service import BrowserRecipeApprovalRequiredError

from .test_support import bootstrap_test_app, create_test_app


class ApprovalGateForBrowserActionsApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory(prefix="atnos-agent-preview-")
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.app = create_test_app(self.root)
        bootstrap_test_app(self.app)
        self.capability_token = "approval-gate-local-capability"
        self.app.state.settings.local_host_capability_token = SecretStr(self.capability_token)
        self.app.state.settings.local_host_capability_expires_at = int(time.time()) + 3600
        self.capability_headers = {"X-Atnos-Local-Capability": self.capability_token}
        with self.app.state.database.session_scope() as session:
            self.project_id = ProjectRepository(session).create("Agent Preview Project").id
        self.addCleanup(self.app.state.database.dispose)

    def _create_grant(self) -> int:
        workspace_root = (self.root / "workspace").resolve()
        workspace_root.mkdir(parents=True, exist_ok=True)
        with self.app.state.database.session_scope() as session:
            owner = UserRepository(session).get_owner()
            assert owner is not None
            grant = LocalFileGrantRepository(session).create(
                user_id=owner.id,
                grant_root=str(workspace_root),
                mode="read_write",
                status="active",
                max_file_size_mb=10,
            )
            return grant.id

    def test_browser_navigate_returns_preview_when_approval_is_required(self) -> None:
        with TestClient(self.app) as client, patch(
            "atnos.services.agent_execution_service.BrowserRecipeRunnerService.run",
            side_effect=BrowserRecipeApprovalRequiredError(
                "Whitelist approval required.",
                code="approval_required",
                details={"host": "youtube.com", "url": "https://youtube.com/"},
            ),
        ):
            response = client.post(
                "/agent/browser/run",
                json={
                    "project_id": self.project_id,
                    "recipe": {
                        "steps": [
                            {"type": "navigate", "url": "https://youtube.com/"},
                        ]
                    }
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["preview"]["kind"], "browser_navigate")
        self.assertEqual(payload["run"]["status"], "waiting_for_input")
        self.assertEqual(payload["preview"]["preview_payload"]["host"], "youtube.com")

    def test_file_write_preview_requires_approval_before_creating_file(self) -> None:
        grant_id = self._create_grant()
        target = self.root / "workspace" / "notes" / "plan.md"

        with TestClient(self.app) as client:
            preview_response = client.post(
                "/agent/action-previews",
                json={
                    "kind": "file_write",
                    "project_id": self.project_id,
                    "title": "Write campaign plan",
                    "summary": "Atnos chce zapisac plan kampanii.",
                    "impact_summary": "Powstanie nowy plik lokalny w grant root.",
                    "preview_payload": {
                        "kind": "file_write",
                        "grant_id": grant_id,
                        "path": "notes/plan.md",
                        "content": "# Campaign plan\n\nready",
                    },
                },
            )
            self.assertEqual(preview_response.status_code, 200)
            preview = preview_response.json()["preview"]
            self.assertFalse(target.exists())

            approve_response = client.post(
                f"/agent/action-previews/{preview['execution_run_id']}/approve",
                headers=self.capability_headers,
            )

        self.assertEqual(approve_response.status_code, 200)
        self.assertEqual(approve_response.json()["run"]["status"], "done")
        self.assertTrue(target.exists())
        self.assertIn("Campaign plan", target.read_text(encoding="utf-8"))

    def test_file_delete_preview_requires_approval_before_removing_file(self) -> None:
        grant_id = self._create_grant()
        target = self.root / "workspace" / "notes" / "delete-me.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("remove", encoding="utf-8")

        with TestClient(self.app) as client:
            preview_response = client.post(
                "/agent/action-previews",
                json={
                    "kind": "file_delete",
                    "project_id": self.project_id,
                    "title": "Delete local note",
                    "summary": "Atnos chce usunac plik lokalny.",
                    "impact_summary": "Ta akcja usunie plik z grant root.",
                    "preview_payload": {
                        "grant_id": grant_id,
                        "path": "notes/delete-me.md",
                    },
                },
            )
            self.assertEqual(preview_response.status_code, 200)
            preview = preview_response.json()["preview"]
            self.assertTrue(target.exists())

            denied_response = client.post(f"/agent/action-previews/{preview['execution_run_id']}/approve")
            self.assertEqual(denied_response.status_code, 401)
            self.assertTrue(target.exists())

            approve_response = client.post(
                f"/agent/action-previews/{preview['execution_run_id']}/approve",
                headers=self.capability_headers,
            )

        self.assertEqual(approve_response.status_code, 200)
        self.assertEqual(approve_response.json()["run"]["status"], "done")
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
