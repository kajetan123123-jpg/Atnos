from __future__ import annotations

import asyncio
import os
from copy import deepcopy
from typing import Any

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

router = APIRouter(tags=["system"])


def _load_hardware_guard_service():
    from atnos.services.hardware_guard_service import HardwareGuardService

    return HardwareGuardService


class RuntimeHardwareResponse(BaseModel):
    free_vram_mb: int | None = None
    used_vram_mb: int | None = None
    total_vram_mb: int | None = None
    free_ram_mb: int | None = None
    used_ram_mb: int | None = None
    total_ram_mb: int | None = None
    cpu_percent: float | None = None
    gpu_name: str | None = None
    free_disk_gb: int | None = None
    disk_path: str | None = None
    status: str
    notes: str | None = None


class HealthLiveResponse(BaseModel):
    status: str
    app: str
    version: str
    environment: str


class HealthReadyResponse(BaseModel):
    status: str
    ready: bool
    startup_phase: str
    startup_error: str | None
    startup_elapsed_ms: int | None
    startup_last_checkpoint: str | None
    app: str
    version: str
    environment: str
    deployment_mode: str
    restart_protocol: str = "manual-only"
    background_automation_enabled: bool = True
    process_id: int | None = None
    process_started_at: str | None = None
    code_revision: str | None = None
    code_root: str | None = None


def _runtime_identity() -> dict[str, Any]:
    """Non-secret identity so operators can tell which API process is live."""
    from datetime import datetime, timezone
    from pathlib import Path

    started_at: str | None = None
    try:
        import psutil

        proc = psutil.Process(os.getpid())
        started_at = datetime.fromtimestamp(proc.create_time(), tz=timezone.utc).isoformat()
    except Exception:
        started_at = None

    revision: str | None = None
    code_root: str | None = None
    try:
        # health.py lives at api/src/atnos/api/routes/health.py → parents[4] == api root
        api_root = Path(__file__).resolve().parents[4]
        code_root = str(api_root)
        marker = api_root / "src" / "atnos" / "integrations" / "capability_workflow_service.py"
        if marker.is_file():
            revision = f"capability_workflow_service@mtime:{int(marker.stat().st_mtime)}"
        try:
            import subprocess

            completed = subprocess.run(
                ["git", "-C", str(api_root), "rev-parse", "--short", "HEAD"],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
            if completed.returncode != 0:
                completed = subprocess.run(
                    ["git", "-C", r"E:\Atnos\App", "rev-parse", "--short", "HEAD"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
            if completed.returncode == 0 and completed.stdout.strip():
                revision = completed.stdout.strip()
        except Exception:
            pass
    except Exception:
        revision = None
        code_root = None

    return {
        "process_id": os.getpid(),
        "process_started_at": started_at,
        "code_revision": revision,
        "code_root": code_root,
    }


class HealthResponse(BaseModel):
    status: str
    app: str
    version: str
    environment: str
    deployment_mode: str
    database: str
    providers_registered: int
    plugins_registered: int
    runtime_capabilities: dict[str, Any] | None = None
    runtime_capabilities_source: str = "background_probe_cache"
    runtime_capabilities_checked_at: str | None = None
    runtime_capabilities_refreshing: bool = False


@router.get("/health/live", response_model=HealthLiveResponse)
def health_live(request: Request) -> HealthLiveResponse:
    settings = request.app.state.settings
    return HealthLiveResponse(
        status="ok",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env,
    )


@router.get("/health/ready", response_model=HealthReadyResponse)
def health_ready(request: Request, response: Response) -> HealthReadyResponse:
    settings = request.app.state.settings
    startup_ready = bool(getattr(request.app.state, "startup_ready", False))
    startup_phase = str(getattr(request.app.state, "startup_phase", "unknown"))
    startup_error = getattr(request.app.state, "startup_error", None)
    startup_elapsed_ms = getattr(request.app.state, "startup_elapsed_ms", None)
    startup_last_checkpoint = getattr(request.app.state, "startup_last_checkpoint", None)
    status_label = "ok" if startup_ready else "failed" if startup_error else "starting"
    if not startup_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    identity = _runtime_identity()
    return HealthReadyResponse(
        restart_protocol=os.environ.get("ATNOS_RESTART_PROTOCOL", "manual-only"),
        status=status_label,
        ready=startup_ready,
        startup_phase=startup_phase,
        startup_error=str(startup_error) if startup_error else None,
        startup_elapsed_ms=int(startup_elapsed_ms) if startup_elapsed_ms is not None else None,
        startup_last_checkpoint=(
            str(startup_last_checkpoint) if startup_last_checkpoint else None
        ),
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env,
        deployment_mode=settings.deployment_mode,
        background_automation_enabled=settings.background_automation_enabled,
        process_id=identity.get("process_id") if isinstance(identity.get("process_id"), int) else None,
        process_started_at=identity.get("process_started_at")
        if isinstance(identity.get("process_started_at"), str)
        else None,
        code_revision=identity.get("code_revision") if isinstance(identity.get("code_revision"), str) else None,
        code_root=identity.get("code_root") if isinstance(identity.get("code_root"), str) else None,
    )


@router.get("/runtime/hardware", response_model=RuntimeHardwareResponse)
def runtime_hardware(request: Request) -> RuntimeHardwareResponse:
    HardwareGuardService = _load_hardware_guard_service()
    settings = request.app.state.settings
    database = request.app.state.database
    with database.session_scope() as session:
        guard = HardwareGuardService(session, settings)
        try:
            snapshot = guard.capture(
                snapshot_type="status-hud",
                persist=False,
            )
        except Exception:
            snapshot = guard.latest()
    if snapshot is None:
        return RuntimeHardwareResponse(status="unknown", notes="No hardware snapshot available yet.")
    return RuntimeHardwareResponse(
        free_vram_mb=snapshot.free_vram_mb,
        used_vram_mb=snapshot.used_vram_mb,
        total_vram_mb=snapshot.total_vram_mb,
        free_ram_mb=snapshot.free_ram_mb,
        used_ram_mb=snapshot.used_ram_mb,
        total_ram_mb=snapshot.total_ram_mb,
        cpu_percent=snapshot.cpu_percent,
        gpu_name=snapshot.gpu_name,
        free_disk_gb=snapshot.free_disk_gb,
        disk_path=snapshot.disk_path,
        status=snapshot.status,
        notes=snapshot.notes,
    )


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    settings = request.app.state.settings
    database = request.app.state.database
    providers = request.app.state.providers
    plugins = request.app.state.plugins

    # Deep capability probes can launch/check ComfyUI and take tens of seconds. A
    # health request must never wait for them: return the last verified snapshot
    # and schedule a single refresh in the background instead.
    runtime_capabilities = deepcopy(
        getattr(request.app.state, "runtime_capability_flags", None)
    )
    refresh_task = getattr(request.app.state, "health_capability_refresh_task", None)
    startup_task = getattr(request.app.state, "optional_capability_task", None)
    active_task = refresh_task if refresh_task and not refresh_task.done() else startup_task
    refresh = getattr(request.app.state, "refresh_optional_runtime_capabilities", None)
    if callable(refresh) and not (active_task and not active_task.done()):
        refresh_task = asyncio.create_task(refresh())
        refresh_task.add_done_callback(
            lambda task: None if task.cancelled() else task.exception()
        )
        request.app.state.health_capability_refresh_task = refresh_task
        active_task = refresh_task

    database_status = "ok" if database.ping() else "error"

    return HealthResponse(
        status="ok" if database_status == "ok" else "degraded",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env,
        deployment_mode=settings.deployment_mode,
        database=database_status,
        providers_registered=providers.count,
        plugins_registered=plugins.count,
        runtime_capabilities=runtime_capabilities,
        runtime_capabilities_checked_at=getattr(
            request.app.state,
            "runtime_capability_last_refreshed_wall",
            None,
        ),
        runtime_capabilities_refreshing=bool(active_task and not active_task.done()),
    )
