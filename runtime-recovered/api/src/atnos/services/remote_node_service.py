from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path
import secrets
import time
from typing import Any

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.db.models.enums import (
    RemoteDisplayMode,
    RemoteExecutionExecutor,
    RemoteExecutionOrigin,
    RemoteExecutionStatus,
    RemoteExecutionStepStatus,
    RemoteNodeStatus,
    RemoteNodeTransport,
    RemoteNodeTrustMode,
    RemoteRoutingTarget,
)
from atnos.repositories.remote_node_repository import RemoteNodeRepository
from atnos.services.local_host_control_service import LocalHostControlError, LocalHostControlService
from atnos.services.remote_execution_router_service import RemoteExecutionRouterService
from atnos.services.remote_node_ssh_service import RemoteNodeSshError, RemoteNodeSshService, RemoteSshConfig


class RemoteNodeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RemoteBootstrapRequest:
    host: str
    ssh_port: int
    user: str
    private_key_path: Path
    control_plane_base_url: str
    label: str | None = None


class RemoteNodeService:
    DEFAULT_REMOTE_WORKDIR = "/home/atnos/atnos"

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.remote_nodes = RemoteNodeRepository(session)

    def list_nodes_payload(self) -> dict[str, object]:
        return {
            "remote_workers": [
                self.serialize_node(item, include_runs=True)
                for item in self.remote_nodes.list_nodes()
            ],
        }

    def get_node_payload(self, node_id: int) -> dict[str, object]:
        node = self._require_node(node_id)
        return {"remote_worker": self.serialize_node(node, include_runs=True)}

    def bootstrap_node(self, payload: RemoteBootstrapRequest) -> dict[str, object]:
        label = (payload.label or f"ubuntu-{payload.host}").strip()
        node = self.remote_nodes.create_node(
            label=label,
            node_type="ubuntu-worker",
            platform="linux",
            host=payload.host,
            ssh_port=payload.ssh_port,
            ssh_user=payload.user,
            status=RemoteNodeStatus.PROVISIONING,
            trust_mode=RemoteNodeTrustMode.TRUSTED_AUTO,
            transport=RemoteNodeTransport.WEBSOCKET,
            display_mode=RemoteDisplayMode.UNKNOWN,
            browser_default_target="personal_profile",
            public_capabilities_json={},
            metadata_json={},
            pause_requested=False,
            last_error=None,
        )
        enrollment_token = self._issue_enrollment_token(node.id)

        ssh = RemoteNodeSshService(
            RemoteSshConfig(
                host=payload.host,
                port=payload.ssh_port,
                user=payload.user,
                private_key_path=payload.private_key_path,
                workdir=self.DEFAULT_REMOTE_WORKDIR,
                connect_timeout_s=20.0,
                command_timeout_s=240.0,
            )
        )
        try:
            probe = ssh.probe()
            node.hostname = probe.get("hostname")
            node.display_mode = self._display_mode_from_probe(probe)
            node.metadata_json = {
                "session_type": probe.get("session_type"),
                "display": probe.get("display"),
                "wayland_display": probe.get("wayland_display"),
                "bootstrap_host": payload.host,
                "control_plane_base_url": payload.control_plane_base_url,
            }
            self.remote_nodes.save_node(node)
            if not probe.get("sudo_available"):
                raise RemoteNodeError("Remote node does not expose passwordless sudo for user 'atnos'.")

            self._run_bootstrap_commands(ssh)
            self._upload_worker_artifacts(ssh, node_id=node.id, enrollment_token=enrollment_token, control_plane_base_url=payload.control_plane_base_url)
            node.status = RemoteNodeStatus.ENROLLING
            self.remote_nodes.save_node(node)
            worker_ready = self._wait_for_enrollment(node.id, timeout_s=25.0)
            if not worker_ready:
                node.last_error = "Bootstrap finished, but the remote worker did not enroll within the expected window."
                self.remote_nodes.save_node(node)
        except (RemoteNodeError, RemoteNodeSshError) as exc:
            node.status = RemoteNodeStatus.ERROR
            node.last_error = str(exc)
            self.remote_nodes.save_node(node)
            raise

        return {
            "remote_worker": self.serialize_node(node, include_runs=True),
            "bootstrap_status": "enrolling" if node.status == RemoteNodeStatus.ENROLLING else node.status.value,
        }

    def enroll(
        self,
        *,
        node_id: int,
        enrollment_token: str,
        hostname: str | None,
        public_capabilities_json: dict[str, object] | None,
        cert_fingerprint: str | None,
    ) -> dict[str, object]:
        node = self._require_node(node_id)
        credential = self._require_credential(node.id)
        if credential.revoked_at is not None:
            raise RemoteNodeError("Remote node credentials have been revoked.")
        if not credential.enrollment_token_hash or not self._matches_token(credential.enrollment_token_hash, enrollment_token):
            raise RemoteNodeError("Invalid enrollment token.")
        credential.enrollment_token_hash = None
        issued_token = self._mint_raw_token("rnode")
        credential.credential_token_hash = self._hash_token(issued_token)
        credential.cert_fingerprint = cert_fingerprint
        credential.last_rotated_at = datetime.now(UTC)
        credential.rotation_due_at = datetime.now(UTC) + timedelta(days=30)
        self.remote_nodes.save_credential(credential)

        node.hostname = hostname or node.hostname
        node.public_capabilities_json = public_capabilities_json or {}
        node.status = RemoteNodeStatus.CONNECTED
        node.last_seen_at = datetime.now(UTC)
        node.last_error = None
        self.remote_nodes.save_node(node)

        return {
            "node_id": node.id,
            "credential_token": issued_token,
            "websocket_path": f"/remote-nodes/ws/{node.id}",
            "remote_worker": self.serialize_node(node),
        }

    def rotate_credentials(self, *, node_id: int, credential_token: str) -> dict[str, object]:
        credential = self._require_credential(node_id)
        if credential.credential_token_hash is None or not self._matches_token(credential.credential_token_hash, credential_token):
            raise RemoteNodeError("Invalid credential token.")
        new_token = self._mint_raw_token("rnode")
        credential.credential_token_hash = self._hash_token(new_token)
        credential.last_rotated_at = datetime.now(UTC)
        credential.rotation_due_at = datetime.now(UTC) + timedelta(days=30)
        self.remote_nodes.save_credential(credential)
        return {"node_id": node_id, "credential_token": new_token}

    def authorize_connection(self, *, node_id: int, credential_token: str) -> None:
        credential = self._require_credential(node_id)
        node = self._require_node(node_id)
        if node.status == RemoteNodeStatus.REVOKED or credential.revoked_at is not None:
            raise RemoteNodeError("Remote node has been revoked.")
        if credential.credential_token_hash is None or not self._matches_token(credential.credential_token_hash, credential_token):
            raise RemoteNodeError("Invalid remote node credential.")

    def register_connection(self, *, node_id: int, remote_addr: str | None) -> dict[str, object]:
        node = self._require_node(node_id)
        self.remote_nodes.deactivate_sessions(node.id)
        session = self.remote_nodes.create_session(
            node_id=node.id,
            transport=RemoteNodeTransport.WEBSOCKET,
            remote_addr=remote_addr,
            connected_at=datetime.now(UTC),
            last_heartbeat_at=datetime.now(UTC),
            is_active=True,
        )
        node.status = RemoteNodeStatus.CONNECTED
        node.last_seen_at = datetime.now(UTC)
        node.last_error = None
        self.remote_nodes.save_node(node)
        return {"session_id": session.id, "remote_worker": self.serialize_node(node)}

    def disconnect(self, *, node_id: int) -> None:
        node = self._require_node(node_id)
        active = self.remote_nodes.get_active_session(node.id)
        if active is not None:
            active.is_active = False
            active.disconnected_at = datetime.now(UTC)
            self.remote_nodes.save_session(active)
        if node.status != RemoteNodeStatus.REVOKED:
            node.status = RemoteNodeStatus.DISCONNECTED
            self.remote_nodes.save_node(node)

    def record_heartbeat(self, *, node_id: int) -> dict[str, object]:
        node = self._require_node(node_id)
        active = self.remote_nodes.get_active_session(node.id)
        now = datetime.now(UTC)
        if active is not None:
            active.last_heartbeat_at = now
            self.remote_nodes.save_session(active)
        node.last_seen_at = now
        if node.status not in {RemoteNodeStatus.PAUSED, RemoteNodeStatus.REVOKED}:
            node.status = RemoteNodeStatus.CONNECTED
        self.remote_nodes.save_node(node)
        return {"pause_requested": node.pause_requested}

    def record_capability_snapshot(self, *, node_id: int, payload: dict[str, Any]) -> dict[str, object]:
        node = self._require_node(node_id)
        display_mode = self._display_mode_from_snapshot(payload)
        snapshot = self.remote_nodes.create_capability_snapshot(
            node_id=node.id,
            cpu_percent=self._optional_float(payload.get("cpu_percent")),
            load_avg_json=self._float_list(payload.get("load_avg")),
            ram_total_mb=self._optional_int(payload.get("ram_total_mb")),
            ram_free_mb=self._optional_int(payload.get("ram_free_mb")),
            disk_free_gb=self._optional_float(payload.get("disk_free_gb")),
            network_online=self._optional_bool(payload.get("network_online")),
            sudo_available=bool(payload.get("sudo_available")),
            ollama_ready=bool(payload.get("ollama_ready")),
            browser_ready=bool(payload.get("browser_ready")),
            desktop_bridge_ready=bool(payload.get("desktop_bridge_ready")),
            ffmpeg_ready=bool(payload.get("ffmpeg_ready")),
            docker_ready=bool(payload.get("docker_ready")),
            display_mode=display_mode,
            browser_mode=str(payload.get("browser_mode") or "personal_profile"),
            python_version=self._optional_text(payload.get("python_version")),
            ffmpeg_version=self._optional_text(payload.get("ffmpeg_version")),
            raw_payload_json=payload,
            captured_at=datetime.now(UTC),
        )
        node.display_mode = display_mode
        node.public_capabilities_json = {
            "sudo_available": snapshot.sudo_available,
            "browser_ready": snapshot.browser_ready,
            "desktop_bridge_ready": snapshot.desktop_bridge_ready,
            "ollama_ready": snapshot.ollama_ready,
            "docker_ready": snapshot.docker_ready,
            "ffmpeg_ready": snapshot.ffmpeg_ready,
            "display_mode": snapshot.display_mode.value,
            "browser_mode": snapshot.browser_mode,
        }
        node.last_seen_at = snapshot.captured_at
        self.remote_nodes.save_node(node)
        return self.serialize_node(node)

    def pause_node(self, node_id: int) -> dict[str, object]:
        node = self._require_node(node_id)
        node.pause_requested = True
        node.status = RemoteNodeStatus.PAUSED
        self.remote_nodes.save_node(node)
        return {"remote_worker": self.serialize_node(node)}

    def resume_node(self, node_id: int) -> dict[str, object]:
        node = self._require_node(node_id)
        node.pause_requested = False
        if node.revoked_at is None:
            node.status = RemoteNodeStatus.DISCONNECTED
        self.remote_nodes.save_node(node)
        return {"remote_worker": self.serialize_node(node)}

    def revoke_node(self, node_id: int) -> dict[str, object]:
        node = self._require_node(node_id)
        credential = self._require_credential(node.id)
        now = datetime.now(UTC)
        credential.revoked_at = now
        credential.enrollment_token_hash = None
        credential.credential_token_hash = None
        self.remote_nodes.save_credential(credential)
        node.revoked_at = now
        node.pause_requested = True
        node.status = RemoteNodeStatus.REVOKED
        self.remote_nodes.save_node(node)
        self.remote_nodes.deactivate_sessions(node.id)
        return {"remote_worker": self.serialize_node(node)}

    def dispatch_run(
        self,
        *,
        task_kind: str,
        payload: dict[str, Any],
        preferred_node_id: int | None = None,
        origin: RemoteExecutionOrigin = RemoteExecutionOrigin.MANUAL,
        requested_by: str | None = "owner",
    ) -> tuple[dict[str, object], dict[str, object] | None]:
        router = RemoteExecutionRouterService(self.session)
        decision = router.route(task_kind=task_kind, payload=payload, preferred_node_id=preferred_node_id)
        run = self.remote_nodes.create_run(
            origin=origin,
            requested_by=requested_by,
            task_kind=task_kind,
            routing_target=decision.target,
            assigned_node_id=decision.node_id,
            status=RemoteExecutionStatus.QUEUED if decision.target == RemoteRoutingTarget.REMOTE else RemoteExecutionStatus.LOCAL_REQUIRED,
            label=self._optional_text(payload.get("label")),
            request_payload_json=payload,
            result_json={},
            error_json={},
            fallback_reason=decision.reason if decision.target == RemoteRoutingTarget.LOCAL else None,
            started_at=None,
            finished_at=None,
        )
        if decision.target == RemoteRoutingTarget.LOCAL:
            self._execute_locally(run, payload, reason=decision.reason)
            return self.serialize_run(run, include_steps=True), None

        dispatch_frame = {
            "type": "run.dispatch",
            "payload": {
                "run_id": run.id,
                "task_kind": task_kind,
                "request": payload,
            },
        }
        run.status = RemoteExecutionStatus.DISPATCHING
        self.remote_nodes.save_run(run)
        return self.serialize_run(run, include_steps=True), dispatch_frame

    def cancel_run(self, run_id: int) -> dict[str, object]:
        run = self._require_run(run_id)
        if run.status not in {
            RemoteExecutionStatus.QUEUED,
            RemoteExecutionStatus.DISPATCHING,
            RemoteExecutionStatus.RUNNING,
        }:
            return {"remote_execution_run": self.serialize_run(run, include_steps=True)}
        run.status = RemoteExecutionStatus.CANCELLED
        run.finished_at = datetime.now(UTC)
        self.remote_nodes.save_run(run)
        return {"remote_execution_run": self.serialize_run(run, include_steps=True)}

    def handle_worker_frame(self, node_id: int, frame: dict[str, object]) -> None:
        frame_type = str(frame.get("type") or "")
        payload = frame.get("payload") if isinstance(frame.get("payload"), dict) else {}
        if frame_type == "heartbeat":
            self.record_heartbeat(node_id=node_id)
            return
        if frame_type == "capability.snapshot":
            self.record_capability_snapshot(node_id=node_id, payload=payload)
            return
        if frame_type == "run.started":
            run = self._require_run(int(payload.get("run_id")))
            run.status = RemoteExecutionStatus.RUNNING
            run.started_at = datetime.now(UTC)
            self.remote_nodes.save_run(run)
            return
        if frame_type == "run.step":
            self._record_remote_step(node_id=node_id, payload=payload)
            return
        if frame_type == "run.completed":
            run = self._require_run(int(payload.get("run_id")))
            run.status = RemoteExecutionStatus.COMPLETED
            run.finished_at = datetime.now(UTC)
            run.result_json = payload.get("result_json") if isinstance(payload.get("result_json"), dict) else {}
            self.remote_nodes.save_run(run)
            return
        if frame_type == "run.failed":
            run = self._require_run(int(payload.get("run_id")))
            run.status = RemoteExecutionStatus.FAILED
            run.finished_at = datetime.now(UTC)
            run.error_json = payload.get("error_json") if isinstance(payload.get("error_json"), dict) else {}
            self.remote_nodes.save_run(run)
            return

    def serialize_node(self, node, *, include_runs: bool = False) -> dict[str, object]:
        snapshot = self.remote_nodes.latest_capability_snapshot(node.id)
        runs = self.remote_nodes.list_runs_for_node(node.id, limit=12) if include_runs else []
        return {
            "node_id": node.id,
            "label": node.label,
            "node_type": node.node_type,
            "platform": node.platform,
            "hostname": node.hostname,
            "host": node.host,
            "ssh_port": node.ssh_port,
            "ssh_user": node.ssh_user,
            "status": node.status.value,
            "trust_mode": node.trust_mode.value,
            "transport": node.transport.value,
            "display_mode": node.display_mode.value,
            "browser_default_target": node.browser_default_target,
            "public_capabilities": node.public_capabilities_json,
            "metadata_json": node.metadata_json,
            "pause_requested": node.pause_requested,
            "revoked_at": node.revoked_at.isoformat() if node.revoked_at is not None else None,
            "last_seen_at": node.last_seen_at.isoformat() if node.last_seen_at is not None else None,
            "last_error": node.last_error,
            "capability_snapshot": self.serialize_capability_snapshot(snapshot) if snapshot is not None else None,
            "recent_runs": [self.serialize_run(item, include_steps=False) for item in runs],
        }

    def serialize_capability_snapshot(self, snapshot) -> dict[str, object]:
        return {
            "snapshot_id": snapshot.id,
            "cpu_percent": snapshot.cpu_percent,
            "load_avg": snapshot.load_avg_json,
            "ram_total_mb": snapshot.ram_total_mb,
            "ram_free_mb": snapshot.ram_free_mb,
            "disk_free_gb": snapshot.disk_free_gb,
            "network_online": snapshot.network_online,
            "sudo_available": snapshot.sudo_available,
            "ollama_ready": snapshot.ollama_ready,
            "browser_ready": snapshot.browser_ready,
            "desktop_bridge_ready": snapshot.desktop_bridge_ready,
            "ffmpeg_ready": snapshot.ffmpeg_ready,
            "docker_ready": snapshot.docker_ready,
            "display_mode": snapshot.display_mode.value,
            "browser_mode": snapshot.browser_mode,
            "python_version": snapshot.python_version,
            "ffmpeg_version": snapshot.ffmpeg_version,
            "captured_at": snapshot.captured_at.isoformat(),
            "raw_payload_json": snapshot.raw_payload_json,
        }

    def serialize_run(self, run, *, include_steps: bool) -> dict[str, object]:
        steps = self.remote_nodes.list_steps_for_run(run.id) if include_steps else []
        node = self.remote_nodes.get_node(run.assigned_node_id) if run.assigned_node_id is not None else None
        return {
            "run_id": run.id,
            "origin": run.origin.value,
            "requested_by": run.requested_by,
            "task_kind": run.task_kind,
            "routing_target": run.routing_target.value,
            "assigned_node_id": run.assigned_node_id,
            "assigned_node_label": node.label if node is not None else None,
            "status": run.status.value,
            "label": run.label,
            "request_payload_json": run.request_payload_json,
            "result_json": run.result_json,
            "error_json": run.error_json,
            "fallback_reason": run.fallback_reason,
            "started_at": run.started_at.isoformat() if run.started_at is not None else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at is not None else None,
            "created_at": run.created_at.isoformat(),
            "updated_at": run.updated_at.isoformat(),
            "steps": [self.serialize_step(step) for step in steps],
        }

    @staticmethod
    def serialize_step(step) -> dict[str, object]:
        return {
            "step_id": step.id,
            "step_index": step.step_index,
            "executor": step.executor.value,
            "action_kind": step.action_kind,
            "command": step.command,
            "browser_target": step.browser_target,
            "sudo": step.sudo,
            "status": step.status.value,
            "stdout": step.stdout,
            "stderr": step.stderr,
            "exit_code": step.exit_code,
            "result_json": step.result_json,
            "started_at": step.started_at.isoformat() if step.started_at is not None else None,
            "finished_at": step.finished_at.isoformat() if step.finished_at is not None else None,
        }

    def _issue_enrollment_token(self, node_id: int) -> str:
        raw = self._mint_raw_token("enroll")
        credential = self.remote_nodes.get_credential(node_id)
        if credential is None:
            credential = self.remote_nodes.create_credential(
                node_id=node_id,
                enrollment_token_hash=self._hash_token(raw),
                credential_token_hash=None,
                cert_fingerprint=None,
                revoked_at=None,
                rotation_due_at=None,
                last_rotated_at=None,
            )
        else:
            credential.enrollment_token_hash = self._hash_token(raw)
            credential.revoked_at = None
            self.remote_nodes.save_credential(credential)
        return raw

    def _run_bootstrap_commands(self, ssh: RemoteNodeSshService) -> None:
        commands: list[tuple[str, bool]] = [
            ("sudo -n apt-get update -y", False),
            (
                "sudo -n DEBIAN_FRONTEND=noninteractive apt-get install -y "
                "ca-certificates curl gnupg docker.io docker-compose-plugin jq ffmpeg xdg-utils xdotool python3 python3-venv python3-pip",
                False,
            ),
            ("sudo -n systemctl enable --now docker", False),
            ("sudo -n usermod -aG docker atnos || true", False),
            ("sudo -n loginctl enable-linger atnos || true", False),
            (f"mkdir -p {self.DEFAULT_REMOTE_WORKDIR}/logs {self.DEFAULT_REMOTE_WORKDIR}/remote-worker", False),
        ]
        for command, sudo in commands:
            result = ssh.run(command, sudo=sudo, timeout_s=600)
            if result.exit_code != 0:
                raise RemoteNodeError(
                    f"Bootstrap command failed ({command}): {result.stderr or result.stdout}"
                )

    def _upload_worker_artifacts(
        self,
        ssh: RemoteNodeSshService,
        *,
        node_id: int,
        enrollment_token: str,
        control_plane_base_url: str,
    ) -> None:
        artifact_root = self.settings.app_root / "runtime-recovered" / "remote-worker"
        worker_core_dir = artifact_root / "worker-core"
        desktop_bridge_dir = artifact_root / "desktop-bridge"
        remote_root = f"{self.DEFAULT_REMOTE_WORKDIR}/remote-worker"
        ssh.upload_tree(worker_core_dir, f"{remote_root}/worker-core")
        ssh.upload_tree(desktop_bridge_dir, f"{remote_root}/desktop-bridge")
        env_content = "\n".join(
            [
                f"ATNOS_CONTROL_PLANE_BASE_URL={control_plane_base_url}",
                f"ATNOS_REMOTE_NODE_ID={node_id}",
                f"ATNOS_REMOTE_NODE_LABEL=ubuntu-worker-{node_id}",
                f"ATNOS_ENROLLMENT_TOKEN={enrollment_token}",
                f"ATNOS_REMOTE_LOG_DIR={self.DEFAULT_REMOTE_WORKDIR}/logs",
                "ATNOS_BROWSER_DEFAULT_TARGET=personal_profile",
                "ATNOS_DESKTOP_BRIDGE_URL=http://127.0.0.1:8766",
                "",
            ]
        )
        ssh.upload_text(f"{remote_root}/worker-core/.env", env_content)
        ssh.upload_text(f"{remote_root}/desktop-bridge/.env", env_content)

        uid_result = ssh.run("id -u atnos")
        if uid_result.exit_code != 0:
            raise RemoteNodeError("Failed to resolve Ubuntu UID for user 'atnos'.")
        user_uid = uid_result.stdout.strip()

        service_content = "\n".join(
            [
                "[Unit]",
                "Description=Atnos Desktop Bridge",
                "After=network-online.target",
                "",
                "[Service]",
                f"WorkingDirectory={remote_root}/desktop-bridge",
                "EnvironmentFile=%h/atnos/remote-worker/desktop-bridge/.env",
                "ExecStart=/usr/bin/python3 bridge.py",
                "Restart=always",
                "RestartSec=3",
                "",
                "[Install]",
                "WantedBy=default.target",
                "",
            ]
        )
        ssh.run("mkdir -p ~/.config/systemd/user")
        ssh.upload_text("/home/atnos/.config/systemd/user/atnos-desktop-bridge.service", service_content)

        compose_up = (
            f"cd {remote_root}/worker-core && sudo -n docker compose up -d --build"
        )
        compose_result = ssh.run(compose_up, timeout_s=900)
        if compose_result.exit_code != 0:
            raise RemoteNodeError(f"Failed to start worker-core: {compose_result.stderr or compose_result.stdout}")

        user_systemd = (
            f"XDG_RUNTIME_DIR=/run/user/{user_uid} "
            f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{user_uid}/bus "
            "systemctl --user daemon-reload && "
            f"XDG_RUNTIME_DIR=/run/user/{user_uid} "
            f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{user_uid}/bus "
            "systemctl --user enable --now atnos-desktop-bridge.service"
        )
        bridge_result = ssh.run(f"sudo -n -u atnos bash -lc '{user_systemd}'", timeout_s=180)
        if bridge_result.exit_code != 0:
            node = self._require_node(node_id)
            node.last_error = (
                "Desktop bridge service did not start cleanly. "
                f"stderr: {bridge_result.stderr or bridge_result.stdout}"
            )
            self.remote_nodes.save_node(node)

    def _wait_for_enrollment(self, node_id: int, *, timeout_s: float) -> bool:
        started = time.monotonic()
        while time.monotonic() - started < timeout_s:
            self.session.expire_all()
            node = self.remote_nodes.get_node(node_id)
            if node is not None and node.status == RemoteNodeStatus.CONNECTED:
                return True
            time.sleep(1.0)
        return False

    def _record_remote_step(self, *, node_id: int, payload: dict[str, Any]) -> None:
        run = self._require_run(int(payload.get("run_id")))
        if run.assigned_node_id != node_id:
            raise RemoteNodeError("Worker attempted to write step output for a run assigned to another node.")
        step_id = self._optional_int(payload.get("step_id"))
        if step_id is not None:
            step = next(
                (item for item in self.remote_nodes.list_steps_for_run(run.id) if item.id == step_id),
                None,
            )
        else:
            step = None
        if step is None:
            step = self.remote_nodes.create_step(
                run_id=run.id,
                step_index=self._optional_int(payload.get("step_index")) or len(self.remote_nodes.list_steps_for_run(run.id)),
                executor=RemoteExecutionExecutor(str(payload.get("executor") or RemoteExecutionExecutor.WORKER_CORE.value)),
                action_kind=str(payload.get("action_kind") or "shell"),
                command=self._optional_text(payload.get("command")),
                browser_target=self._optional_text(payload.get("browser_target")),
                sudo=bool(payload.get("sudo")),
                status=RemoteExecutionStepStatus.QUEUED,
                stdout=None,
                stderr=None,
                exit_code=None,
                result_json={},
                started_at=None,
                finished_at=None,
            )
        step.status = RemoteExecutionStepStatus(str(payload.get("status") or step.status.value))
        step.stdout = self._optional_text(payload.get("stdout"))
        step.stderr = self._optional_text(payload.get("stderr"))
        step.exit_code = self._optional_int(payload.get("exit_code"))
        step.result_json = payload.get("result_json") if isinstance(payload.get("result_json"), dict) else {}
        step.started_at = self._coerce_datetime(payload.get("started_at")) or step.started_at or datetime.now(UTC)
        if step.status in {RemoteExecutionStepStatus.COMPLETED, RemoteExecutionStepStatus.FAILED, RemoteExecutionStepStatus.CANCELLED}:
            step.finished_at = self._coerce_datetime(payload.get("finished_at")) or datetime.now(UTC)
        self.remote_nodes.save_step(step)

    def _execute_locally(self, run, payload: dict[str, Any], *, reason: str) -> None:
        run.started_at = datetime.now(UTC)
        steps = payload.get("steps")
        if not isinstance(steps, list) or not steps:
            run.status = RemoteExecutionStatus.FAILED
            run.finished_at = datetime.now(UTC)
            run.error_json = {"error": "Local fallback requires a non-empty 'steps' list."}
            run.fallback_reason = reason
            self.remote_nodes.save_run(run)
            return

        collected_results: list[dict[str, object]] = []
        for index, raw_step in enumerate(steps):
            if not isinstance(raw_step, dict):
                continue
            action_kind = str(raw_step.get("action") or "shell")
            step = self.remote_nodes.create_step(
                run_id=run.id,
                step_index=index,
                executor=RemoteExecutionExecutor.LOCAL_EXECUTOR,
                action_kind=action_kind,
                command=self._optional_text(raw_step.get("command")),
                browser_target=self._optional_text(raw_step.get("profile_target")),
                sudo=False,
                status=RemoteExecutionStepStatus.RUNNING,
                stdout=None,
                stderr=None,
                exit_code=None,
                result_json={},
                started_at=datetime.now(UTC),
                finished_at=None,
            )
            if action_kind != "shell":
                step.status = RemoteExecutionStepStatus.FAILED
                step.stderr = f"Local fallback does not support action '{action_kind}'."
                step.finished_at = datetime.now(UTC)
                self.remote_nodes.save_step(step)
                run.status = RemoteExecutionStatus.FAILED
                run.finished_at = datetime.now(UTC)
                run.error_json = {"error": step.stderr}
                run.fallback_reason = reason
                self.remote_nodes.save_run(run)
                return
            try:
                result = LocalHostControlService(self.session, settings=self.settings).run_powershell(
                    str(raw_step.get("command") or ""),
                    timeout_s=int(raw_step.get("timeout_s") or 120),
                    workdir=str(raw_step.get("workdir") or self.settings.app_root),
                )
            except LocalHostControlError as exc:
                step.stderr = str(exc)
                step.exit_code = 1
                step.status = RemoteExecutionStepStatus.FAILED
                step.finished_at = datetime.now(UTC)
                self.remote_nodes.save_step(step)
                run.status = RemoteExecutionStatus.FAILED
                run.finished_at = datetime.now(UTC)
                run.error_json = {"error": str(exc)}
                run.fallback_reason = reason
                self.remote_nodes.save_run(run)
                return
            step.stdout = result.stdout
            step.stderr = result.stderr
            step.exit_code = result.exit_code
            step.status = (
                RemoteExecutionStepStatus.COMPLETED
                if result.exit_code == 0
                else RemoteExecutionStepStatus.FAILED
            )
            step.finished_at = datetime.now(UTC)
            self.remote_nodes.save_step(step)
            collected_results.append(self.serialize_step(step))
            if result.exit_code != 0:
                run.status = RemoteExecutionStatus.FAILED
                run.finished_at = datetime.now(UTC)
                run.error_json = {"error": result.stderr or result.stdout}
                run.fallback_reason = reason
                self.remote_nodes.save_run(run)
                return

        run.status = RemoteExecutionStatus.COMPLETED
        run.finished_at = datetime.now(UTC)
        run.result_json = {"steps": collected_results}
        run.fallback_reason = reason
        self.remote_nodes.save_run(run)

    def _require_node(self, node_id: int):
        node = self.remote_nodes.get_node(node_id)
        if node is None:
            raise RemoteNodeError(f"Unknown remote node '{node_id}'.")
        return node

    def _require_credential(self, node_id: int):
        credential = self.remote_nodes.get_credential(node_id)
        if credential is None:
            raise RemoteNodeError(f"No credential row exists for remote node '{node_id}'.")
        return credential

    def _require_run(self, run_id: int):
        run = self.remote_nodes.get_run(run_id)
        if run is None:
            raise RemoteNodeError(f"Unknown remote execution run '{run_id}'.")
        return run

    @staticmethod
    def _mint_raw_token(prefix: str) -> str:
        return f"{prefix}_{secrets.token_urlsafe(32)}"

    @staticmethod
    def _hash_token(raw: str) -> str:
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def _matches_token(cls, expected_hash: str, raw: str) -> bool:
        return secrets.compare_digest(expected_hash, cls._hash_token(raw))

    @staticmethod
    def _display_mode_from_probe(probe: dict[str, Any]) -> RemoteDisplayMode:
        session_type = str(probe.get("session_type") or "").strip().lower()
        if session_type == "x11":
            return RemoteDisplayMode.X11
        if session_type == "wayland":
            return RemoteDisplayMode.WAYLAND
        return RemoteDisplayMode.UNKNOWN

    @staticmethod
    def _display_mode_from_snapshot(payload: dict[str, Any]) -> RemoteDisplayMode:
        raw = str(payload.get("display_mode") or payload.get("session_type") or "").strip().lower()
        if raw == "x11":
            return RemoteDisplayMode.X11
        if raw == "wayland":
            return RemoteDisplayMode.WAYLAND
        return RemoteDisplayMode.UNKNOWN

    @staticmethod
    def _optional_text(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _optional_int(value: object) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_float(value: object) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_bool(value: object) -> bool | None:
        if value is None:
            return None
        return bool(value)

    @staticmethod
    def _float_list(value: object) -> list[float] | None:
        if not isinstance(value, list):
            return None
        items: list[float] = []
        for entry in value:
            try:
                items.append(float(entry))
            except (TypeError, ValueError):
                continue
        return items

    @staticmethod
    def _coerce_datetime(value: object) -> datetime | None:
        if value is None or not isinstance(value, str) or not value.strip():
            return None
        text = value.strip()
        try:
            normalized = text.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
