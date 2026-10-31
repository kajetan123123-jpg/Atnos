from __future__ import annotations

from uuid import uuid4

from atnos.jarvis.types import ToolResult
from atnos.jarvis.workers.tools.base_browser_tool import BaseBrowserTool


class LlmBrowserTool(BaseBrowserTool):
    kind = "llm"
    provider_key = "claude_browser"
    service_key = "claude"

    async def run(self, **kwargs) -> ToolResult:
        if await self.local_primary.is_available(**kwargs):
            return ToolResult(success=False, reason="primary_available", provider_key=self.provider_key)
        task_id = kwargs.get("task_id") or uuid4().hex
        async with self.page_session(self.service_key, task_id) as lease_or_result:
            if isinstance(lease_or_result, ToolResult):
                return lease_or_result
            page = lease_or_result.page
            try:
                await page.goto("https://claude.ai/new", wait_until="domcontentloaded")
                await self._delay()
                system_prompt = str(kwargs.get("system_prompt") or "").strip()
                user_prompt = str(kwargs.get("prompt") or "").strip()
                composed = user_prompt if not system_prompt else f"System instructions:\n{system_prompt}\n\nUser request:\n{user_prompt}"
                await self._fill_first(page, ["textarea", "[contenteditable='true']"], composed)
                await self._delay()
                await self._click_first(page, ["button[type='submit']", "button[aria-label*='Send']", "text=Send"])
                await page.wait_for_load_state("networkidle", timeout=120000)
                await self._delay()
                locator = page.locator("[data-testid*='assistant'], .assistant, article")
                text = await locator.last.inner_text()
                await self._log_tool_event("success", {"task_id": task_id, "response_chars": len(text.strip())})
                return ToolResult(success=True, provider_key=self.provider_key, plain_text=text.strip())
            except Exception as exc:
                screenshot = await self._screenshot_failure(page, prefix="claude")
                await self._log_tool_event("failure", {"task_id": task_id, "reason": str(exc), "screenshot_path": screenshot})
                return ToolResult(success=False, reason=str(exc), provider_key=self.provider_key, screenshot_path=screenshot)
