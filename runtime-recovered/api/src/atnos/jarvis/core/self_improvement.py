from __future__ import annotations

import asyncio
import json
from statistics import mean
from typing import Any

from atnos.jarvis.core.module import JarvisModule
from atnos.jarvis.core.ollama import JarvisOllamaClient
from atnos.jarvis.types import JarvisEvent, WorkerResult


class SelfImprovementModule(JarvisModule):
    def __init__(self, *, event_bus, config_store, ollama_client: JarvisOllamaClient) -> None:
        super().__init__(name="self_improvement", event_bus=event_bus, config_store=config_store)
        self.ollama_client = ollama_client
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._started = False
        self._winner_path = self.config_store.paths.prompt_versions_dir / "winner.json"

    async def start(self) -> None:
        if self._started:
            return
        await self.event_bus.subscribe(JarvisEvent.TASK_COMPLETED, self.handle_worker_result)
        await self.event_bus.subscribe(JarvisEvent.TASK_FAILED, self.handle_worker_result)
        self._stop_event.clear()
        self._task = asyncio.create_task(self._review_loop(), name="jarvis-self-improvement")
        self._started = True

    async def stop(self) -> None:
        self._stop_event.set()
        await self.event_bus.unsubscribe(JarvisEvent.TASK_COMPLETED, self.handle_worker_result)
        await self.event_bus.unsubscribe(JarvisEvent.TASK_FAILED, self.handle_worker_result)
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._started = False

    def status(self) -> dict[str, Any]:
        return {
            "running": self._started,
            "performance_log_path": self.config_store.paths.performance_log_path.as_posix(),
            "winner_path": self._winner_path.as_posix(),
        }

    async def handle_worker_result(self, payload: WorkerResult | dict[str, Any]) -> None:
        if isinstance(payload, WorkerResult):
            entry = payload.to_dict()
        else:
            entry = payload
        await asyncio.to_thread(self._append_log, entry)

    def _append_log(self, entry: dict[str, Any]) -> None:
        path = self.config_store.paths.performance_log_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    async def _review_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.run_review_once()
                await asyncio.wait_for(self._stop_event.wait(), timeout=86400)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(60.0)

    async def run_review_once(self) -> None:
        entries = await asyncio.to_thread(self._read_entries)
        if len(entries) < 2:
            return
        score = mean(int(item.get("score", 0)) for item in entries[-20:])
        prompt = await self._suggest_prompt(entries, score)
        version_path = self.config_store.paths.prompt_versions_dir / f"prompt-{len(entries):04d}.txt"
        await asyncio.to_thread(version_path.write_text, prompt, "utf-8")
        winner = {"version": version_path.name, "score": score}
        await asyncio.to_thread(self._winner_path.write_text, json.dumps(winner, indent=2), "utf-8")

    def _read_entries(self) -> list[dict[str, Any]]:
        path = self.config_store.paths.performance_log_path
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    async def _suggest_prompt(self, entries: list[dict[str, Any]], score: float) -> str:
        prompt = (
            "Given these recent task outcomes, write an improved short planner prompt. "
            f"Average score: {score:.2f}. Recent entries: {json.dumps(entries[-10:])}"
        )
        try:
            return await self.ollama_client.generate_text(prompt=prompt)
        except Exception:
            return "Be concise, prefer local tools first, degrade gracefully to browser fallback, and keep tasks observable."
