from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import create_test_app, make_temp_dir

from atnos.db.models.enums import (
    RemoteDisplayMode,
    RemoteNodeStatus,
    RemoteNodeTransport,
    RemoteNodeTrustMode,
)
from atnos.repositories.remote_node_repository import RemoteNodeRepository


class RemoteNodesApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.app = create_test_app(self.root)
        self.addCleanup(self.app.state.database.dispose)

    def test_bootstrap_remote_node_creates_worker_record(self) -> None:
        with patch(
            "atnos.services.remote_node_service.RemoteNodeSshService.probe",
            return_value={
                "hostname": "ubuntu-lab",
                "session_type": "x11",
                "display": ":0",
                "wayland_display": None,
                "sudo_available": True,
            },
        ), patch(
            "atnos.services.remote_node_service.RemoteNodeService._run_bootstrap_commands",
            return_value=None,
        ), patch(
            "atnos.services.remote_node_service.RemoteNodeService._upload_worker_artifacts",
            return_value=None,
        ), patch(
            "atnos.services.remote_node_service.RemoteNodeService._wait_for_enrollment",
            return_value=True,
        ):
            with TestClient(self.app) as client:
                response = client.post(
                    "/remote-nodes/bootstrap",
                    json={
                        "host": "198.51.100.229",
                        "ssh_port": 22,
                        "user": "atnos",
                        "private_key_path": "C:/Users/test/.ssh/atnos_lab",
                        "control_plane_base_url": "http://127.0.0.1:8000",
                        "label": "Ubuntu Worker",
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["bootstrap_status"], "enrolling")
        worker = payload["remote_worker"]
        self.assertEqual(worker["label"], "Ubuntu Worker")
        self.assertEqual(worker["host"], "198.51.100.229")
        self.assertEqual(worker["status"], "enrolling")
        self.assertEqual(worker["display_mode"], "x11")

    def test_bootstrap_remote_node_rejects_missing_passwordless_sudo(self) -> None:
        with patch(
            "atnos.services.remote_node_service.RemoteNodeSshService.probe",
            return_value={
                "hostname": "ubuntu-lab",
                "session_type": "wayland",
                "sudo_available": False,
            },
        ):
            with TestClient(self.app) as client:
                response = client.post(
                    "/remote-nodes/bootstrap",
                    json={
                        "host": "198.51.100.229",
                        "ssh_port": 22,
                        "user": "atnos",
                        "private_key_path": "C:/Users/test/.ssh/atnos_lab",
                        "control_plane_base_url": "http://127.0.0.1:8000",
                    },
                )

        self.assertEqual(response.status_code, 400)
        self.assertIn("passwordless sudo", response.text)

    def test_dispatch_remote_execution_routes_to_connected_worker(self) -> None:
        self.app.state.remote_node_hub.send = AsyncMock()

        with TestClient(self.app) as client:
            node_id = self._create_connected_remote_node()
            response = client.post(
                "/remote-execution/dispatch",
                json={
                    "task_kind": "shell",
                    "preferred_node_id": node_id,
                    "payload": {
                        "label": "Remote whoami",
                        "steps": [{"action": "shell", "command": "whoami", "timeout_s": 30}],
                    },
                },
            )

        self.assertEqual(response.status_code, 200)
        run = response.json()["remote_execution_run"]
        self.assertEqual(run["assigned_node_id"], node_id)
        self.assertEqual(run["routing_target"], "remote")
        self.assertEqual(run["status"], "dispatching")
        self.app.state.remote_node_hub.send.assert_awaited_once()

    def test_dispatch_remote_execution_falls_back_locally_when_remote_is_unavailable(self) -> None:
        with TestClient(self.app) as client:
            response = client.post(
                "/remote-execution/dispatch",
                json={
                    "task_kind": "shell",
                    "payload": {
                        "label": "Local fallback probe",
                        "steps": [
                            {
                                "action": "shell",
                                "command": "Write-Output 'remote-fallback-ok'",
                                "timeout_s": 30,
                            }
                        ],
                    },
                },
            )

        self.assertEqual(response.status_code, 200)
        run = response.json()["remote_execution_run"]
        self.assertEqual(run["routing_target"], "local")
        self.assertEqual(run["status"], "completed")
        self.assertIn("No connected remote worker", run["fallback_reason"])
        self.assertEqual(len(run["steps"]), 1)
        self.assertEqual(run["steps"][0]["executor"], "local_executor")

    def test_pause_resume_and_revoke_update_remote_worker_status(self) -> None:
        with TestClient(self.app) as client:
            node_id = self._create_connected_remote_node()
            pause_response = client.post(f"/remote-nodes/{node_id}/pause")
            self.assertEqual(pause_response.status_code, 200)
            self.assertEqual(pause_response.json()["remote_worker"]["status"], "paused")

            resume_response = client.post(f"/remote-nodes/{node_id}/resume")
            self.assertEqual(resume_response.status_code, 200)
            self.assertEqual(resume_response.json()["remote_worker"]["status"], "disconnected")

            revoke_response = client.post(f"/remote-nodes/{node_id}/revoke")
            self.assertEqual(revoke_response.status_code, 200)
            self.assertEqual(revoke_response.json()["remote_worker"]["status"], "revoked")

    def _create_connected_remote_node(self) -> int:
        with self.app.state.database.session_scope() as session:
            repo = RemoteNodeRepository(session)
            node = repo.create_node(
                label="Ubuntu Worker",
                node_type="ubuntu-worker",
                platform="linux",
                hostname="ubuntu-lab",
                host="198.51.100.229",
                ssh_port=22,
                ssh_user="atnos",
                status=RemoteNodeStatus.CONNECTED,
                trust_mode=RemoteNodeTrustMode.TRUSTED_AUTO,
                transport=RemoteNodeTransport.WEBSOCKET,
                display_mode=RemoteDisplayMode.X11,
                browser_default_target="personal_profile",
                public_capabilities_json={},
                metadata_json={"session_type": "x11"},
                pause_requested=False,
                last_error=None,
                last_seen_at=datetime.now(UTC),
                revoked_at=None,
            )
            repo.create_credential(
                node_id=node.id,
                enrollment_token_hash=None,
                credential_token_hash="hashed-token",
                cert_fingerprint=None,
                revoked_at=None,
                rotation_due_at=datetime.now(UTC) + timedelta(days=30),
                last_rotated_at=datetime.now(UTC),
            )
            repo.create_capability_snapshot(
                node_id=node.id,
                cpu_percent=13.2,
                load_avg_json=[0.2, 0.3, 0.4],
                ram_total_mb=16000,
                ram_free_mb=11000,
                disk_free_gb=180.0,
                network_online=True,
                sudo_available=True,
                ollama_ready=True,
                browser_ready=True,
                desktop_bridge_ready=True,
                ffmpeg_ready=True,
                docker_ready=True,
                display_mode=RemoteDisplayMode.X11,
                browser_mode="personal_profile",
                python_version="3.12.3",
                ffmpeg_version="6.1",
                raw_payload_json={},
                captured_at=datetime.now(UTC),
            )
            return node.id


if __name__ == "__main__":
    unittest.main()
