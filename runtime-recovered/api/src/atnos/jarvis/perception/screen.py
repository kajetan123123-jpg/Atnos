from __future__ import annotations

import asyncio
import ctypes
from ctypes import wintypes
from io import BytesIO
from typing import Any

from atnos.jarvis.core.module import JarvisModule
from atnos.jarvis.core.ollama import JarvisOllamaClient
from atnos.jarvis.types import JarvisEvent, ScreenContext


class ScreenPerceptionModule(JarvisModule):
    def __init__(self, *, event_bus, config_store, ollama_client: JarvisOllamaClient) -> None:
        super().__init__(name="screen", event_bus=event_bus, config_store=config_store)
        self.ollama_client = ollama_client
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._last_signature: tuple[str, str, str] | None = None
        self._last_cursor: tuple[int, int] | None = None
        self._available = True
        self._last_error: str | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop(), name="jarvis-screen")

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
            "last_signature": self._last_signature,
        }

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                config = self.config_store.current().screen
                if config.enabled:
                    context = await self._capture_context()
                    signature = (context.active_app, context.visible_text[:160], context.user_activity)
                    if signature != self._last_signature and context.confidence >= config.min_emit_confidence:
                        self._last_signature = signature
                        await self.event_bus.publish(JarvisEvent.SCREEN_CONTEXT_UPDATED, context)
                await asyncio.wait_for(self._stop_event.wait(), timeout=max(0.5, self.config_store.current().screen.interval_s))
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._available = False
                self._last_error = str(exc)
                await asyncio.sleep(2.0)

    async def _capture_context(self) -> ScreenContext:
        title = self._foreground_window_title() or "Unknown app"
        cursor = self._cursor_position()
        user_activity = "active" if cursor != self._last_cursor else "idle"
        self._last_cursor = cursor
        image_bytes = await asyncio.to_thread(self._capture_png)
        vision_payload = await self.ollama_client.generate_vision_json(
            prompt=(
                "Return compact JSON with keys active_app, visible_text, confidence. "
                "Describe only what is clearly visible on screen."
            ),
            image_bytes=image_bytes,
        )
        visible_text = str(vision_payload.get("visible_text") or "").strip()
        active_app = str(vision_payload.get("active_app") or title).strip() or title
        try:
            confidence = float(vision_payload.get("confidence") or 0.45)
        except (TypeError, ValueError):
            confidence = 0.45
        return ScreenContext(
            active_app=active_app,
            visible_text=visible_text,
            user_activity=user_activity,
            confidence=max(0.0, min(confidence, 1.0)),
        )

    @staticmethod
    def _capture_png() -> bytes:
        import mss  # type: ignore
        from PIL import Image  # type: ignore

        with mss.mss() as sct:
            monitor = sct.monitors[1]
            grab = sct.grab(monitor)
            image = Image.frombytes("RGB", grab.size, grab.bgra, "raw", "BGRX")
            buffer = BytesIO()
            image.save(buffer, format="PNG")
            return buffer.getvalue()

    @staticmethod
    def _foreground_window_title() -> str:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value.strip()

    @staticmethod
    def _cursor_position() -> tuple[int, int]:
        class POINT(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

        point = POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
        return point.x, point.y
