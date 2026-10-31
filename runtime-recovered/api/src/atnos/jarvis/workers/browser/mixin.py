from __future__ import annotations

from contextlib import asynccontextmanager

from atnos.jarvis.types import ToolResult


class BrowserSessionMixin:
    session_manager: object

    @asynccontextmanager
    async def page_session(self, service: str, task_id: str):
        lease_or_result = await self.session_manager.get_page(service, task_id)
        if isinstance(lease_or_result, ToolResult):
            yield lease_or_result
            return
        try:
            yield lease_or_result
        finally:
            await self.session_manager.release_page(lease_or_result)
