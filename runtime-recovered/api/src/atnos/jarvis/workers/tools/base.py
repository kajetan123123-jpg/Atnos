from __future__ import annotations

from typing import Any, Protocol

from atnos.jarvis.types import ToolResult


class WorkerTool(Protocol):
    kind: str
    provider_key: str

    async def is_available(self, **kwargs) -> bool:
        ...

    async def run(self, **kwargs) -> ToolResult:
        ...

    def describe(self) -> dict[str, Any]:
        ...
