from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from atnos.core.config import Settings
from atnos.services.n8n_runtime_status_service import N8nRuntimeStatusService


def make_settings(root: Path) -> Settings:
    return Settings(app_env="test", app_root=root, data_dir=root / "data", n8n_data_dir=root / "data" / "n8n")


def create_runtime_files(root: Path) -> None:
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "Run-YoutubeDailyOps.ps1").write_text("Write-Host 'fallback'\n", encoding="utf-8")
    workflows = root / "automations" / "n8n" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "workflow.json").write_text(json.dumps({"name": "test", "nodes": []}), encoding="utf-8")


def fake_completed(command: list[str], returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)


class N8nRuntimeStatusTest(unittest.TestCase):
    def test_reports_virtualization_disabled_when_docker_daemon_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_runtime_files(root)
            settings = make_settings(root)

            def runner(command: list[str], timeout: int):
                joined = " ".join(command)
                if "Get-CimInstance" in joined:
                    return fake_completed(command, stdout="False\n")
                if "info" in command:
                    return fake_completed(command, returncode=1, stderr="Docker daemon unavailable")
                return fake_completed(command)

            with patch("atnos.services.n8n_runtime_status_service.sys.platform", "win32"), patch(
                "atnos.services.n8n_runtime_status_service.shutil.which",
                side_effect=lambda name: f"C:/fake/{name}.exe",
            ):
                status = N8nRuntimeStatusService(settings, runner=runner).snapshot()

            self.assertEqual(status.docker_status, "docker_unavailable_virtualization_disabled")
            self.assertFalse(status.docker_ready)
            self.assertFalse(status.virtualization_firmware_enabled)
            self.assertEqual(status.fallback_runner_status, "available")
            self.assertEqual(status.workflow_templates_status, "ready")

    def test_reports_running_when_docker_container_is_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            create_runtime_files(root)
            settings = make_settings(root)

            def runner(command: list[str], timeout: int):
                joined = " ".join(command)
                if "Get-CimInstance" in joined:
                    return fake_completed(command, stdout="True\n")
                if "info" in command:
                    return fake_completed(command, stdout='{"ServerVersion":"fake"}')
                if "ps" in command:
                    return fake_completed(command, stdout="atnos-n8n\n")
                return fake_completed(command)

            with patch("atnos.services.n8n_runtime_status_service.sys.platform", "win32"), patch(
                "atnos.services.n8n_runtime_status_service.shutil.which",
                side_effect=lambda name: f"C:/fake/{name}.exe",
            ):
                status = N8nRuntimeStatusService(settings, runner=runner).snapshot()

            self.assertEqual(status.docker_status, "running")
            self.assertTrue(status.docker_ready)
            self.assertTrue(status.container_running)

    def test_real_workflow_templates_parse(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        workflow_dir = repo_root / "automations" / "n8n" / "workflows"
        workflow_files = sorted(workflow_dir.glob("*.json"))

        self.assertGreaterEqual(len(workflow_files), 4)
        for path in workflow_files:
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertIsInstance(payload, dict, path.name)
            self.assertIn("nodes", payload, path.name)


if __name__ == "__main__":
    unittest.main()
