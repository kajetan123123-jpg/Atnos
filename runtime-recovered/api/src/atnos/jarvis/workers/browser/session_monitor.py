from __future__ import annotations

import asyncio
from typing import Any

from atnos.jarvis.core.module import JarvisModule
from atnos.jarvis.types import JarvisEvent


class BrowserSessionMonitor(JarvisModule):
    def __init__(self, *, event_bus, config_store, session_manager) -> None:
        super().__init__(name="browser_session_monitor", event_bus=event_bus, config_store=config_store)
        self.session_manager = session_manager
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._last_checked: list[str] = []

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop(), name="jarvis-browser-session-monitor")

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
            "last_checked": list(self._last_checked),
        }

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._last_checked = []
                for service in self.session_manager.known_services():
                    self._last_checked.append(service)
                    valid = await self.session_manager.is_session_valid(service)
                    if not valid:
                        await self.session_manager.mark_session_expired(service)
                        await self.event_bus.publish(JarvisEvent.SESSION_EXPIRED, {"service": service})
                    elif not self.session_manager.status().get(service, {}).get("ready", False):
                        await self.session_manager.reload_context(service)
                await asyncio.wait_for(self._stop_event.wait(), timeout=max(30.0, self.config_store.current().sessions.validation_interval_s))
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(10.0)
