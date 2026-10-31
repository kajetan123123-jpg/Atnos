from __future__ import annotations

from uuid import uuid4

from atnos.jarvis.types import ToolResult
from atnos.jarvis.workers.tools.base_browser_tool import BaseBrowserTool


class ImageGenerationBrowserTool(BaseBrowserTool):
    kind = "image_gen"
    provider_key = "ideogram_browser"
    service_key = "ideogram"

    async def run(self, **kwargs) -> ToolResult:
        if await self.local_primary.is_available(**kwargs):
            return ToolResult(success=False, reason="primary_available", provider_key=self.provider_key)
        task_id = kwargs.get("task_id") or uuid4().hex
        async with self.page_session(self.service_key, task_id) as lease_or_result:
            if isinstance(lease_or_result, ToolResult):
                return lease_or_result
            page = lease_or_result.page
            try:
                await page.goto("https://ideogram.ai/t/explore", wait_until="domcontentloaded")
                await self._delay()
                prompt = str(kwargs.get("prompt") or "").strip()
                negative = str(kwargs.get("negative_prompt") or "").strip()
                if negative:
                    prompt = f"{prompt}\n\nNegative prompt: {negative}"
                await self._fill_first(page, ["textarea", "[contenteditable='true']", "input[placeholder*='Prompt']"], prompt)
                await self._delay()
                await self._click_first(page, ["button:has-text('Generate')", "button[type='submit']"])
                await page.wait_for_load_state("networkidle", timeout=180000)
                await self._delay()
                download_path = self.config_store.paths.images_output_dir / f"{uuid4().hex}.png"
                async with page.expect_download(timeout=30000) as download_info:
                    await self._click_first(page, ["button[aria-label*='Download']", "button:has-text('Download')"])
                download = await download_info.value
                saved_path = await self._download_to_path(download, download_path)
                await self._log_tool_event("success", {"task_id": task_id, "image_path": saved_path})
                return ToolResult(success=True, provider_key=self.provider_key, artifacts={"image_path": saved_path})
            except Exception as exc:
                screenshot = await self._screenshot_failure(page, prefix="ideogram")
                await self._log_tool_event("failure", {"task_id": task_id, "reason": str(exc), "screenshot_path": screenshot})
                return ToolResult(success=False, reason=str(exc), provider_key=self.provider_key, screenshot_path=screenshot)
