from __future__ import annotations

import asyncio
from typing import Any

from atnos.core.config import Settings
from atnos.jarvis.core.module import JarvisModule
from atnos.jarvis.types import JarvisEvent
from atnos.services.voice_synthesis_service import VoiceSynthesisService


class VoiceOutputModule(JarvisModule):
    def __init__(self, *, event_bus, config_store, settings: Settings) -> None:
        super().__init__(name="voice_output", event_bus=event_bus, config_store=config_store)
        self.settings = settings
        self._queue: asyncio.Queue[tuple[str, str | None]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._current_voice: str | None = None
        self._service = VoiceSynthesisService(settings)
        self._last_error: str | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        await self.event_bus.subscribe(JarvisEvent.SPEAK_REQUESTED, self._handle_speak_requested)
        self._task = asyncio.create_task(self._run_queue(), name="jarvis-voice-output")

    async def stop(self) -> None:
        await self.event_bus.unsubscribe(JarvisEvent.SPEAK_REQUESTED, self._handle_speak_requested)
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
            "queued_items": self._queue.qsize(),
            "voice": self._current_voice or self.config_store.current().voice_output.voice_preset,
            "last_error": self._last_error,
        }

    async def speak(self, text: str) -> None:
        await self._queue.put((text, self._current_voice))

    def speak_async(self, text: str) -> None:
        asyncio.create_task(self.speak(text))

    def set_voice(self, preset: str) -> None:
        self._current_voice = preset

    async def _handle_speak_requested(self, payload: Any) -> None:
        if isinstance(payload, str):
            await self.speak(payload)
        elif isinstance(payload, dict):
            await self._queue.put((str(payload.get("text") or ""), payload.get("voice")))

    async def _run_queue(self) -> None:
        while True:
            text, voice_name = await self._queue.get()
            try:
                await asyncio.to_thread(
                    self._service.synthesize,
                    text=text,
                    conversation_id=None,
                    voice_name=voice_name or self._current_voice,
                )
            except Exception as exc:
                self._last_error = str(exc)
