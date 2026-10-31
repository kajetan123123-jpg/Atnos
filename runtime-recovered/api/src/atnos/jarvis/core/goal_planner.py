from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

from atnos.jarvis.core.module import JarvisModule
from atnos.jarvis.core.ollama import JarvisOllamaClient
from atnos.jarvis.types import JarvisEvent, ResourceSnapshot, ScreenContext, VoiceCommand, WorkerPriority, WorkerTask


class GoalPlanner(JarvisModule):
    def __init__(self, *, event_bus, config_store, ollama_client: JarvisOllamaClient) -> None:
        super().__init__(name="goal_planner", event_bus=event_bus, config_store=config_store)
        self.ollama_client = ollama_client
        self._goals: list[dict[str, Any]] = []
        self._latest_screen: ScreenContext | None = None
        self._latest_resources: ResourceSnapshot | None = None
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._goals = await self._load_goals()
        await self.event_bus.subscribe(JarvisEvent.VOICE_COMMAND, self.handle_voice_command)
        await self.event_bus.subscribe(JarvisEvent.SCREEN_CONTEXT_UPDATED, self.handle_screen_context)
        await self.event_bus.subscribe(JarvisEvent.RESOURCE_SNAPSHOT_UPDATED, self.handle_resource_snapshot)
        self._started = True

    async def stop(self) -> None:
        await self.event_bus.unsubscribe(JarvisEvent.VOICE_COMMAND, self.handle_voice_command)
        await self.event_bus.unsubscribe(JarvisEvent.SCREEN_CONTEXT_UPDATED, self.handle_screen_context)
        await self.event_bus.unsubscribe(JarvisEvent.RESOURCE_SNAPSHOT_UPDATED, self.handle_resource_snapshot)
        self._started = False

    def status(self) -> dict[str, Any]:
        return {
            "running": self._started,
            "goal_count": len(self._goals),
            "latest_goal": self._goals[-1] if self._goals else None,
        }

    async def set_goal(self, goal: str) -> dict[str, Any]:
        goal_id = uuid4().hex
        plan = await self._decompose_goal(goal)
        record = {"id": goal_id, "goal": goal, "plan": plan}
        self._goals.append(record)
        await self._save_goals()
        await self.event_bus.publish(JarvisEvent.GOAL_PLAN_UPDATED, record)
        for index, item in enumerate(plan.get("tasks", []), start=1):
            task = WorkerTask(
                id=uuid4().hex,
                priority=WorkerPriority.NORMAL if index > 1 else WorkerPriority.URGENT,
                kind=item.get("kind", "llm"),
                payload=item,
                goal_id=goal_id,
            )
            await self.event_bus.publish(JarvisEvent.TASK_ENQUEUED, task)
        return record

    async def handle_voice_command(self, command: VoiceCommand) -> None:
        payload = {
            "prompt": command.text,
            "source": "voice_command",
            "screen_context": self._latest_screen.to_dict() if self._latest_screen else None,
            "resource_snapshot": self._latest_resources.to_dict() if self._latest_resources else None,
        }
        task = WorkerTask(
            id=uuid4().hex,
            priority=WorkerPriority.URGENT,
            kind="llm",
            payload=payload,
        )
        await self.event_bus.publish(JarvisEvent.TASK_ENQUEUED, task)

    async def handle_screen_context(self, payload: ScreenContext) -> None:
        self._latest_screen = payload

    async def handle_resource_snapshot(self, payload: ResourceSnapshot) -> None:
        self._latest_resources = payload

    async def _load_goals(self) -> list[dict[str, Any]]:
        path = self.config_store.paths.goals_path
        if not path.exists():
            return []
        raw = await asyncio.to_thread(path.read_text, "utf-8")
        return json.loads(raw)

    async def _save_goals(self) -> None:
        await asyncio.to_thread(
            self.config_store.paths.goals_path.write_text,
            json.dumps(self._goals, indent=2),
            "utf-8",
        )

    async def _decompose_goal(self, goal: str) -> dict[str, Any]:
        prompt = (
            "Return compact JSON with keys summary and tasks. Each task must have kind, title and prompt. "
            f"Goal: {goal}"
        )
        try:
            response = await self.ollama_client.generate_text(prompt=prompt)
            parsed = json.loads(response[response.index("{") : response.rindex("}") + 1])
            if isinstance(parsed.get("tasks"), list):
                return parsed
        except Exception:
            pass
        return {
            "summary": goal,
            "tasks": [
                {"kind": "llm", "title": "Goal breakdown", "prompt": f"Break down this goal into next actions: {goal}"},
                {"kind": "trend_research", "title": "Relevant research", "prompt": f"Research trends relevant to: {goal}"},
            ],
        }
