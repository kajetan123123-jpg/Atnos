from __future__ import annotations

from dataclasses import dataclass
import json
import shutil
import subprocess
import sys
from typing import Any, Callable

from atnos.core.config import Settings


CommandRunner = Callable[[list[str], int], subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class N8nRuntimeStatus:
    docker_status: str
    docker_ready: bool
    docker_available: bool
    docker_message: str
    virtualization_firmware_enabled: bool | None
    docker_executable: str | None
    docker_compose_executable: str | None
    container_running: bool
    fallback_runner_status: str
    fallback_runner_available: bool
    fallback_runner_path: str
    workflow_templates_status: str
    workflow_templates_count: int
    workflow_templates_dir: str
    workflow_template_errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "docker_status": self.docker_status,
            "docker_ready": self.docker_ready,
            "docker_available": self.docker_available,
            "docker_message": self.docker_message,
            "virtualization_firmware_enabled": self.virtualization_firmware_enabled,
            "docker_executable": self.docker_executable,
            "docker_compose_executable": self.docker_compose_executable,
            "container_running": self.container_running,
            "fallback_runner_status": self.fallback_runner_status,
            "fallback_runner_available": self.fallback_runner_available,
            "fallback_runner_path": self.fallback_runner_path,
            "workflow_templates_status": self.workflow_templates_status,
            "workflow_templates_count": self.workflow_templates_count,
            "workflow_templates_dir": self.workflow_templates_dir,
            "workflow_template_errors": list(self.workflow_template_errors),
        }


class N8nRuntimeStatusService:
    def __init__(self, settings: Settings, *, runner: CommandRunner | None = None) -> None:
        self.settings = settings
        self.runner = runner or self._run_command
        self.app_root = settings.app_root.resolve()
        self.fallback_runner_path = self.app_root / "scripts" / "Run-YoutubeDailyOps.ps1"
        self.workflow_templates_dir = self.app_root / "automations" / "n8n" / "workflows"

    def snapshot(self, *, probe_docker: bool = True) -> N8nRuntimeStatus:
        workflows_status, workflows_count, workflow_errors = self._workflow_templates_status()
        fallback_available = self.fallback_runner_path.exists() and self.fallback_runner_path.is_file()
        fallback_status = "available" if fallback_available else "missing"
        docker_executable = shutil.which("docker")
        docker_compose_executable = shutil.which("docker-compose")

        if not probe_docker:
            return N8nRuntimeStatus(
                docker_status="not_checked",
                docker_ready=False,
                docker_available=bool(docker_executable),
                docker_message="Docker probe skipped for this runtime call.",
                virtualization_firmware_enabled=None,
                docker_executable=docker_executable,
                docker_compose_executable=docker_compose_executable,
                container_running=False,
                fallback_runner_status=fallback_status,
                fallback_runner_available=fallback_available,
                fallback_runner_path=str(self.fallback_runner_path),
                workflow_templates_status=workflows_status,
                workflow_templates_count=workflows_count,
                workflow_templates_dir=str(self.workflow_templates_dir),
                workflow_template_errors=tuple(workflow_errors),
            )

        virtualization_enabled = self._virtualization_firmware_enabled()
        if not docker_executable:
            docker_status = (
                "docker_unavailable_virtualization_disabled"
                if virtualization_enabled is False
                else "docker_unavailable"
            )
            return self._status(
                docker_status=docker_status,
                docker_message="Docker CLI is not available on PATH.",
                virtualization_enabled=virtualization_enabled,
                docker_executable=None,
                docker_compose_executable=docker_compose_executable,
                fallback_status=fallback_status,
                fallback_available=fallback_available,
                workflows_status=workflows_status,
                workflows_count=workflows_count,
                workflow_errors=workflow_errors,
            )

        docker_info = self._safe_run([docker_executable, "info", "--format", "{{json .}}"], timeout=6)
        if docker_info is None or docker_info.returncode != 0:
            docker_status = (
                "docker_unavailable_virtualization_disabled"
                if virtualization_enabled is False
                else "docker_daemon_unavailable"
            )
            stderr = "" if docker_info is None else (docker_info.stderr or docker_info.stdout or "").strip()
            message = stderr or "Docker daemon did not answer within timeout."
            return self._status(
                docker_status=docker_status,
                docker_message=message[:500],
                virtualization_enabled=virtualization_enabled,
                docker_executable=docker_executable,
                docker_compose_executable=docker_compose_executable,
                fallback_status=fallback_status,
                fallback_available=fallback_available,
                workflows_status=workflows_status,
                workflows_count=workflows_count,
                workflow_errors=workflow_errors,
            )

        container_running = self._is_container_running(docker_executable)
        docker_status = "running" if container_running else "docker_daemon_running_container_stopped"
        docker_message = "n8n container is running." if container_running else "Docker daemon is running, but atnos-n8n is not running."
        return self._status(
            docker_status=docker_status,
            docker_message=docker_message,
            virtualization_enabled=virtualization_enabled,
            docker_executable=docker_executable,
            docker_compose_executable=docker_compose_executable,
            container_running=container_running,
            fallback_status=fallback_status,
            fallback_available=fallback_available,
            workflows_status=workflows_status,
            workflows_count=workflows_count,
            workflow_errors=workflow_errors,
        )

    def _status(
        self,
        *,
        docker_status: str,
        docker_message: str,
        virtualization_enabled: bool | None,
        docker_executable: str | None,
        docker_compose_executable: str | None,
        fallback_status: str,
        fallback_available: bool,
        workflows_status: str,
        workflows_count: int,
        workflow_errors: list[str],
        container_running: bool = False,
    ) -> N8nRuntimeStatus:
        return N8nRuntimeStatus(
            docker_status=docker_status,
            docker_ready=docker_status == "running",
            docker_available=bool(docker_executable),
            docker_message=docker_message,
            virtualization_firmware_enabled=virtualization_enabled,
            docker_executable=docker_executable,
            docker_compose_executable=docker_compose_executable,
            container_running=container_running,
            fallback_runner_status=fallback_status,
            fallback_runner_available=fallback_available,
            fallback_runner_path=str(self.fallback_runner_path),
            workflow_templates_status=workflows_status,
            workflow_templates_count=workflows_count,
            workflow_templates_dir=str(self.workflow_templates_dir),
            workflow_template_errors=tuple(workflow_errors),
        )

    def _is_container_running(self, docker_executable: str) -> bool:
        result = self._safe_run(
            [
                docker_executable,
                "ps",
                "--filter",
                "name=atnos-n8n",
                "--filter",
                "status=running",
                "--format",
                "{{.Names}}",
            ],
            timeout=6,
        )
        if result is None or result.returncode != 0:
            return False
        return "atnos-n8n" in result.stdout.splitlines()

    def _workflow_templates_status(self) -> tuple[str, int, list[str]]:
        if not self.workflow_templates_dir.exists():
            return "missing", 0, [f"Workflow directory is missing: {self.workflow_templates_dir}"]
        errors: list[str] = []
        count = 0
        for path in sorted(self.workflow_templates_dir.glob("*.json")):
            count += 1
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"{path.name}: {type(exc).__name__}: {exc}")
        if errors:
            return "invalid", count, errors
        return ("ready" if count else "empty"), count, []

    def _virtualization_firmware_enabled(self) -> bool | None:
        if sys.platform != "win32":
            return None
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if not powershell:
            return None
        result = self._safe_run(
            [
                powershell,
                "-NoProfile",
                "-Command",
                "(Get-CimInstance -ClassName Win32_Processor | Select-Object -First 1 -ExpandProperty VirtualizationFirmwareEnabled)",
            ],
            timeout=6,
        )
        if result is None or result.returncode != 0:
            return None
        value = result.stdout.strip().lower()
        if value == "true":
            return True
        if value == "false":
            return False
        return None

    def _safe_run(self, command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str] | None:
        try:
            return self.runner(command, timeout)
        except (OSError, subprocess.TimeoutExpired):
            return None

    @staticmethod
    def _run_command(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
