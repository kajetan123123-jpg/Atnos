from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from atnos.jarvis.types import ToolResult
from atnos.jarvis.workers.tools.base_browser_tool import BaseBrowserTool


class TranscribeBrowserTool(BaseBrowserTool):
    kind = "transcription"
    provider_key = "veed_browser"
    service_key = "veed"

    async def run(self, **kwargs) -> ToolResult:
        if await self.local_primary.is_available(**kwargs):
            return ToolResult(success=False, reason="primary_available", provider_key=self.provider_key)
        task_id = kwargs.get("task_id") or uuid4().hex
        video_path = Path(kwargs["video_path"]).resolve()
        async with self.page_session(self.service_key, task_id) as lease_or_result:
            if isinstance(lease_or_result, ToolResult):
                return lease_or_result
            page = lease_or_result.page
            try:
                await page.goto("https://www.veed.io/dashboard", wait_until="domcontentloaded")
                await self._delay()
                file_input = page.locator("input[type='file']")
                await file_input.first.set_input_files(video_path.as_posix())
                await page.wait_for_load_state("networkidle", timeout=180000)
                await self._delay()
                await self._click_first(page, ["text=Subtitles", "text=Captions"])
                await self._click_first(page, ["text=Auto Subtitle", "text=Auto-subtitle", "text=Auto Subtitles"])
                await page.wait_for_load_state("networkidle", timeout=180000)
                await self._delay()
                async with page.expect_download(timeout=60000) as download_info:
                    await self._click_first(page, ["text=.srt", "button:has-text('SRT')", "text=Download subtitles"])
                download = await download_info.value
                srt_path = (self.config_store.paths.root_dir / "tmp" / "atnos_outputs" / "subtitles" / f"{uuid4().hex}.srt").resolve()
                saved_path = await self._download_to_path(download, srt_path)
                plain_text = Path(saved_path).read_text(encoding="utf-8", errors="ignore")
                await self._log_tool_event("success", {"task_id": task_id, "srt_path": saved_path})
                return ToolResult(
                    success=True,
                    provider_key=self.provider_key,
                    artifacts={"srt_path": saved_path},
                    plain_text=plain_text,
                )
            except Exception as exc:
                screenshot = await self._screenshot_failure(page, prefix="veed")
                await self._log_tool_event("failure", {"task_id": task_id, "reason": str(exc), "screenshot_path": screenshot})
                return ToolResult(success=False, reason=str(exc), provider_key=self.provider_key, screenshot_path=screenshot)
