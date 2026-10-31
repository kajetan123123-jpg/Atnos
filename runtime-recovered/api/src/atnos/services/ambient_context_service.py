from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import ctypes
import socket
import threading
from typing import TYPE_CHECKING, Any

import psutil

if TYPE_CHECKING:
    from atnos.core.config import Settings
    from atnos.providers.registry import ProviderRegistry


class RuntimeSettingsService:
    def __init__(self, *args, **kwargs) -> None:
        from atnos.services.runtime_settings_service import RuntimeSettingsService as RealService

        self._impl = RealService(*args, **kwargs)

    def snapshot(self, *args, **kwargs):
        return self._impl.snapshot(*args, **kwargs)


class HardwareGuardService:
    def __init__(self, *args, **kwargs) -> None:
        from atnos.services.hardware_guard_service import HardwareGuardService as RealService

        self._impl = RealService(*args, **kwargs)

    def capture(self, *args, **kwargs):
        return self._impl.capture(*args, **kwargs)


class ProjectRepository:
    def __init__(self, *args, **kwargs) -> None:
        from atnos.repositories.project_repository import ProjectRepository as RealRepository

        self._impl = RealRepository(*args, **kwargs)

    def list_all(self, *args, **kwargs):
        return self._impl.list_all(*args, **kwargs)


@dataclass(frozen=True, slots=True)
class AmbientContextSnapshot:
    collected_at: str
    foreground_window_title: str | None
    active_app_name: str | None
    time_of_day: str
    day_of_week: str
    cpu_percent: float | None
    ram_used_mb: int | None
    ram_total_mb: int | None
    network_online: bool
    free_vram_mb: int | None
    used_vram_mb: int | None
    total_vram_mb: int | None
    active_project_id: int | None
    active_project_name: str | None
    last_user_interaction_at: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class AmbientContextService:
    """
    Privacy contract:
    - Collect window title only, never window content.
    - Keep everything local and in-memory only.
    - Respect explicit opt-in and global off-switch from Settings.
    - Never persist snapshots to the database.
    """

    POLL_INTERVAL_S = 30.0

    def __init__(self, database, *, settings: Settings, provider_registry: ProviderRegistry) -> None:
        self.database = database
        self.settings = settings
        self.provider_registry = provider_registry
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshot: AmbientContextSnapshot | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        try:
            self.collect_once()
        except Exception:
            pass
        self._thread = threading.Thread(target=self._run_loop, name="atnos-ambient-context", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None

    def get_snapshot(self) -> AmbientContextSnapshot | None:
        with self._lock:
            return self._snapshot

    def collect_once(self) -> AmbientContextSnapshot:
        with self.database.session_scope() as session:
            runtime_settings = RuntimeSettingsService(session, self.settings)
            controls = runtime_settings.snapshot()
            hardware = HardwareGuardService(session, self.settings).capture(
                snapshot_type="ambient-context",
                persist=False,
            )
            project_repo = ProjectRepository(session)
            active_project = self._resolve_active_project(project_repo)
            ram = psutil.virtual_memory()
            snapshot = AmbientContextSnapshot(
                collected_at=datetime.now(UTC).isoformat(),
                foreground_window_title=self._read_foreground_window_title(),
                active_app_name=self._read_foreground_process_name(),
                time_of_day=datetime.now().strftime("%H:%M"),
                day_of_week=datetime.now().strftime("%A"),
                cpu_percent=float(psutil.cpu_percent(interval=None)),
                ram_used_mb=int((ram.total - ram.available) / (1024 * 1024)),
                ram_total_mb=int(ram.total / (1024 * 1024)),
                network_online=self._network_online(),
                free_vram_mb=hardware.free_vram_mb,
                used_vram_mb=hardware.used_vram_mb,
                total_vram_mb=hardware.total_vram_mb,
                active_project_id=getattr(active_project, "id", None),
                active_project_name=getattr(active_project, "name", None),
                last_user_interaction_at=(
                    controls.last_user_interaction_at.isoformat()
                    if controls.last_user_interaction_at is not None
                    else None
                ),
            )
        with self._lock:
            self._snapshot = snapshot
        return snapshot

    def _run_loop(self) -> None:
        while not self._stop_event.wait(self.POLL_INTERVAL_S):
            try:
                self.collect_once()
            except Exception:
                continue

    @staticmethod
    def _read_foreground_window_title() -> str | None:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return None
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.strip()
        return title or None

    @staticmethod
    def _read_foreground_process_name() -> str | None:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        process_id = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if not process_id.value:
            return None
        try:
            return psutil.Process(process_id.value).name()
        except Exception:
            return None

    @staticmethod
    def _network_online() -> bool:
        try:
            stats = psutil.net_if_stats()
            if any(item.isup for item in stats.values()):
                return True
        except Exception:
            pass
        try:
            socket.gethostbyname("localhost")
            return True
        except Exception:
            return False

    @staticmethod
    def _resolve_active_project(project_repo: ProjectRepository) -> Any:
        projects = project_repo.list_all()
        return projects[0] if projects else None
