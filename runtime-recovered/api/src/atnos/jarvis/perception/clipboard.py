from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from atnos.jarvis.core.module import JarvisModule
from atnos.jarvis.types import ClipboardPayload, JarvisEvent


class ClipboardMonitorModule(JarvisModule):
    def __init__(self, *, event_bus, config_store) -> None:
        super().__init__(name="clipboard", event_bus=event_bus, config_store=config_store)
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._last_value: str | None = None
        self._available = True
        self._last_error: str | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        try:
            import pyperclip  # type: ignore # noqa: F401
        except Exception as exc:
            self._available = False
            self._last_error = str(exc)
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop(), name="jarvis-clipboard")

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
            "available": self._available,
            "last_error": self._last_error,
        }

    async def _loop(self) -> None:
        import pyperclip  # type: ignore

        while not self._stop_event.is_set():
            try:
                value = await asyncio.to_thread(pyperclip.paste)
                if value and value != self._last_value:
                    self._last_value = value
                    payload = ClipboardPayload(kind=self._classify(value), value=value)
                    await self.event_bus.publish(JarvisEvent.CLIPBOARD_CHANGED, payload)
                await asyncio.wait_for(self._stop_event.wait(), timeout=max(0.5, self.config_store.current().clipboard.interval_s))
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = str(exc)
                await asyncio.sleep(1.0)

    @staticmethod
    def _classify(value: str) -> str:
        candidate = value.strip()
        parsed = urlparse(candidate)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return "url"
        if Path(candidate).exists():
            return "path"
        return "text"
