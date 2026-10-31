from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from typing import Any
from uuid import uuid4

import httpx

from atnos.jarvis.core.module import JarvisModule
from atnos.jarvis.types import JarvisEvent, WorkerPriority, WorkerTask


class ProactiveModule(JarvisModule):
    def __init__(self, *, event_bus, config_store) -> None:
        super().__init__(name="proactive", event_bus=event_bus, config_store=config_store)
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._last_summary: dict[str, Any] | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop(), name="jarvis-proactive")

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
            "last_summary": self._last_summary,
        }

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                if self.config_store.current().proactive.enabled:
                    summary = await self._collect_relevant_signals()
                    self._last_summary = summary
                    score = float(summary.get("trend_score") or 0.0)
                    if score >= self.config_store.current().proactive.trend_score_threshold:
                        await self.event_bus.publish(JarvisEvent.TREND_DETECTED, summary)
                        task = WorkerTask(
                            id=uuid4().hex,
                            priority=WorkerPriority.BACKGROUND,
                            kind="trend_research",
                            payload=summary,
                        )
                        await self.event_bus.publish(JarvisEvent.TASK_ENQUEUED, task)
                await asyncio.wait_for(self._stop_event.wait(), timeout=max(60.0, self.config_store.current().proactive.interval_s))
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(60.0)

    async def _collect_relevant_signals(self) -> dict[str, Any]:
        google = await self._fetch_google_trends()
        hugging_face = await self._fetch_hugging_face()
        items = google[:5] + hugging_face[:5]
        score = min(1.0, len(items) / 10)
        return {
            "trend_score": score,
            "google_trends": google,
            "hugging_face": hugging_face,
            "focus": "short-form content, ai tooling, creator workflows",
        }

    async def _fetch_google_trends(self) -> list[str]:
        url = "https://trends.google.com/trends/trendingsearches/daily/rss?geo=US"
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url)
            response.raise_for_status()
        root = ET.fromstring(response.text)
        ns = {"item": "http://search.yahoo.com/mrss/"}
        titles: list[str] = []
        for item in root.findall(".//item"):
            title = item.findtext("title")
            if title:
                titles.append(title.strip())
        return [item for item in titles if self._is_relevant(item)]

    async def _fetch_hugging_face(self) -> list[str]:
        url = "https://huggingface.co/api/models?sort=trendingScore&limit=10"
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
        labels = [str(item.get("id") or "").strip() for item in payload if str(item.get("id") or "").strip()]
        return [item for item in labels if self._is_relevant(item)]

    @staticmethod
    def _is_relevant(value: str) -> bool:
        normalized = value.lower()
        markers = ("video", "image", "caption", "subtitle", "short", "reel", "creator", "model", "flux", "llm")
        return any(marker in normalized for marker in markers)
