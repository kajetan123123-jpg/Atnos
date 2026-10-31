from __future__ import annotations

import asyncio

from atnos.jarvis.workers.registry import ToolChain, WorkerRegistry


class _DummyTool:
    def __init__(self, available: bool, name: str) -> None:
        self.available = available
        self.name = name

    async def is_available(self, **kwargs) -> bool:
        return self.available

    def describe(self) -> dict[str, str]:
        return {"name": self.name}


class TestJarvisRegistry:
    def test_registry_prefers_primary_then_fallback(self) -> None:
        registry = WorkerRegistry.__new__(WorkerRegistry)
        primary = _DummyTool(True, "primary")
        fallback = _DummyTool(True, "fallback")
        registry._chains = {"image_gen": ToolChain(primary=primary, fallback=fallback)}  # noqa: SLF001

        selected = asyncio.run(registry.resolve("image_gen"))
        assert selected is primary

        primary.available = False
        selected = asyncio.run(registry.resolve("image_gen"))
        assert selected is fallback
