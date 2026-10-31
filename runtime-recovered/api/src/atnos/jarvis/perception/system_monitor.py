from __future__ import annotations

import asyncio
import shutil
from typing import Any

from atnos.core.config import Settings
from atnos.jarvis.core.module import JarvisModule
from atnos.jarvis.types import JarvisEvent, ResourceSnapshot


class SystemMonitorModule(JarvisModule):
    def __init__(self, *, event_bus, config_store, settings: Settings) -> None:
        super().__init__(name="system_monitor", event_bus=event_bus, config_store=config_store)
        self.settings = settings
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._last_snapshot: ResourceSnapshot | None = None
        self._last_error: str | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop(), name="jarvis-system-monitor")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def status(self) -> dict[str, Any]:
        return {
            "running": self._task is not None and not self._task.done(),
            "last_snapshot": self._last_snapshot.to_dict() if self._last_snapshot else None,
            "last_error": self._last_error,
        }

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                snapshot = await asyncio.to_thread(self._collect)
                self._last_snapshot = snapshot
                await self.event_bus.publish(JarvisEvent.RESOURCE_SNAPSHOT_UPDATED, snapshot)
                threshold = self.config_store.current().system_monitor.vram_low_threshold_mb
                if snapshot.gpu_vram_free_mb is not None and snapshot.gpu_vram_free_mb < threshold:
                    await self.event_bus.publish(JarvisEvent.RESOURCES_LOW, snapshot)
                await asyncio.wait_for(self._stop_event.wait(), timeout=max(1.0, self.config_store.current().system_monitor.interval_s))
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = str(exc)
                await asyncio.sleep(2.0)

    def _collect(self) -> ResourceSnapshot:
        import psutil  # type: ignore

        cpu_percent = float(psutil.cpu_percent(interval=0.1))
        vm = psutil.virtual_memory()
        disk = shutil.disk_usage(self.settings.app_root)
        gpu_vram_free_mb = self._probe_gpu_vram()
        return ResourceSnapshot(
            cpu_percent=cpu_percent,
            ram_free_mb=int(vm.available / (1024 * 1024)),
            disk_free_mb=int(disk.free / (1024 * 1024)),
            gpu_vram_free_mb=gpu_vram_free_mb,
        )

    @staticmethod
    def _probe_gpu_vram() -> int | None:
        try:
            import pynvml  # type: ignore

            pynvml.nvmlInit()
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            return int(info.free / (1024 * 1024))
        except Exception:
            return None
