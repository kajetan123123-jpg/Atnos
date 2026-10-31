from __future__ import annotations

import asyncio

from atnos.jarvis.core.event_bus import EventBus
from atnos.jarvis.types import JarvisEvent


class TestJarvisEventBus:
    def test_publish_subscribe_and_unsubscribe(self) -> None:
        received: list[dict[str, str]] = []

        async def scenario() -> None:
            bus = EventBus()

            async def handler(payload) -> None:
                received.append(payload)

            await bus.subscribe(JarvisEvent.TASK_COMPLETED, handler)
            assert await bus.subscriber_count(JarvisEvent.TASK_COMPLETED) == 1

            await bus.publish(JarvisEvent.TASK_COMPLETED, {"task_id": "a1"})
            await bus.unsubscribe(JarvisEvent.TASK_COMPLETED, handler)
            await bus.publish(JarvisEvent.TASK_COMPLETED, {"task_id": "a2"})

            snapshot = await bus.snapshot()
            assert snapshot[str(JarvisEvent.TASK_COMPLETED)] == 0

        asyncio.run(scenario())
        assert received == [{"task_id": "a1"}]
