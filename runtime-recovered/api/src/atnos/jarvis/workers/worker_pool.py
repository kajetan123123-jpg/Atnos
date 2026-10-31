from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from atnos.jarvis.core.module import JarvisModule
from atnos.jarvis.types import JarvisEvent, WorkerResult, WorkerTask


@dataclass(slots=True)
class WorkerStatus:
    worker_id: int
    state: str = "idle"
    task_id: str | None = None
    last_error: str | None = None


class WorkerPool(JarvisModule):
    def __init__(self, *, event_bus, config_store, registry) -> None:
        super().__init__(name="worker_pool", event_bus=event_bus, config_store=config_store)
        self.registry = registry
        self._queue: asyncio.PriorityQueue[tuple[int, int, WorkerTask]] = asyncio.PriorityQueue()
        self._workers: list[asyncio.Task[None]] = []
        self._worker_status: dict[int, WorkerStatus] = {}
        self._counter = 0
        self._memory: dict[str, Any] = {}
        self._memory_lock = asyncio.Lock()
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        await self.event_bus.subscribe(JarvisEvent.TASK_ENQUEUED, self.enqueue)
        worker_count = self.config_store.current().worker_pool.worker_count
        for worker_id in range(worker_count):
            self._worker_status[worker_id] = WorkerStatus(worker_id=worker_id)
            self._workers.append(asyncio.create_task(self._worker_loop(worker_id), name=f"jarvis-worker-{worker_id}"))
        self._started = True

    async def stop(self) -> None:
        await self.event_bus.unsubscribe(JarvisEvent.TASK_ENQUEUED, self.enqueue)
        for task in self._workers:
            task.cancel()
        for task in self._workers:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._workers.clear()
        self._started = False

    def status(self) -> dict[str, Any]:
        return {
            "running": self._started,
            "queued": self._queue.qsize(),
            "workers": {worker_id: status.__dict__ for worker_id, status in self._worker_status.items()},
        }

    async def enqueue(self, task: WorkerTask | dict[str, Any]) -> None:
        if isinstance(task, dict):
            task = WorkerTask(
                id=str(task["id"]),
                priority=task["priority"],
                kind=str(task["kind"]),
                payload=dict(task.get("payload") or {}),
                goal_id=task.get("goal_id"),
            )
        self._counter += 1
        await self._queue.put((int(task.priority), self._counter, task))

    async def _worker_loop(self, worker_id: int) -> None:
        while True:
            _priority, _seq, task = await self._queue.get()
            status = self._worker_status[worker_id]
            status.state = "running"
            status.task_id = task.id
            await self.event_bus.publish(JarvisEvent.TASK_STARTED, task)
            try:
                tool = await self.registry.resolve(task.kind, **task.payload)
                if tool is None:
                    raise RuntimeError(f"No tool registered for task kind '{task.kind}'.")
                result = await tool.run(task_id=task.id, **task.payload)
                worker_result = WorkerResult(
                    task_id=task.id,
                    status="done" if result.success else "failed",
                    score=10 if result.success else 0,
                    artifacts=result.to_dict(),
                )
                event = JarvisEvent.TASK_COMPLETED if result.success else JarvisEvent.TASK_FAILED
                await self.event_bus.publish(event, worker_result)
            except Exception as exc:
                status.last_error = str(exc)
                worker_result = WorkerResult(task_id=task.id, status="failed", score=0, artifacts={"error": str(exc)})
                await self.event_bus.publish(JarvisEvent.TASK_FAILED, worker_result)
            finally:
                status.state = "idle"
                status.task_id = None
