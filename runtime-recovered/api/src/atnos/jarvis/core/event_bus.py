from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from collections.abc import Awaitable, Callable
from logging import getLogger
from typing import Any

from atnos.jarvis.types import JarvisEvent

logger = getLogger(__name__)

Subscriber = Callable[[Any], Awaitable[None] | None]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[Subscriber]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def subscribe(self, event: JarvisEvent | str, callback: Subscriber) -> None:
        event_name = str(event)
        async with self._lock:
            if callback not in self._subscribers[event_name]:
                self._subscribers[event_name].append(callback)

    async def unsubscribe(self, event: JarvisEvent | str, callback: Subscriber) -> None:
        event_name = str(event)
        async with self._lock:
            if callback in self._subscribers[event_name]:
                self._subscribers[event_name].remove(callback)

    async def publish(self, event: JarvisEvent | str, payload: Any) -> None:
        event_name = str(event)
        async with self._lock:
            callbacks = tuple(self._subscribers.get(event_name, ()))
        for callback in callbacks:
            try:
                result = callback(payload)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception("Jarvis event subscriber failed | event=%s", event_name)

    async def subscriber_count(self, event: JarvisEvent | str) -> int:
        event_name = str(event)
        async with self._lock:
            return len(self._subscribers.get(event_name, ()))

    async def snapshot(self) -> dict[str, int]:
        async with self._lock:
            return {key: len(value) for key, value in self._subscribers.items()}
