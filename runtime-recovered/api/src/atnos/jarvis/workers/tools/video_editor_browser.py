from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from atnos.jarvis.types import ToolResult
from atnos.jarvis.workers.tools.base_browser_tool import BaseBrowserTool


class VideoEditorBrowserTool(BaseBrowserTool):
    kind = "video_edit"
    provider_key = "capcut_browser"
    service_key = "capcut"

    async def run(self, **kwargs) -> ToolResult:
        if await self.local_primary.is_available(**kwargs):
            return ToolResult(success=False, reason="primary_available", provider_key=self.provider_key)
        task_id = kwargs.get("task_id") or uuid4().hex
        video_path = Path(kwargs["video_path"]).resolve()
        srt_path = Path(kwargs["srt_path"]).resolve()
        cut_start = float(kwargs.get("cut_start", 0.0))
        cut_end = float(kwargs.get("cut_end", 0.0))
        async with self.page_session(self.service_key, task_id) as lease_or_result:
            if isinstance(lease_or_result, ToolResult):
                return lease_or_result
            page = lease_or_result.page
            try:
                await page.goto("https://www.capcut.com/editor", wait_until="domcontentloaded")
                await self._delay()
                await page.locator("input[type='file']").first.set_input_files(video_path.as_posix())
                await page.wait_for_load_state("networkidle", timeout=180000)
                await self._delay()
                if srt_path.exists():
                    await self._maybe_import_subtitles(page, srt_path)
                else:
                    await self._click_first(page, ["text=Captions", "text=Subtitles"])
                    await self._click_first(page, ["text=Auto captions", "text=Recognise", "text=Generate"])
                if cut_end > cut_start:
                    await self._maybe_apply_trim(page, cut_start, cut_end)
                await page.wait_for_load_state("networkidle", timeout=180000)
                async with page.expect_download(timeout=120000) as download_info:
                    await self._click_first(page, ["text=Export", "button:has-text('Export')"])
                download = await download_info.value
                output_path = (self.config_store.paths.root_dir / "tmp" / "atnos_outputs" / "videos" / f"{uuid4().hex}.mp4").resolve()
                saved_path = await self._download_to_path(download, output_path)
                await self._log_tool_event(
                    "success",
                    {"task_id": task_id, "video_path": saved_path, "cut_start": cut_start, "cut_end": cut_end},
                )
                return ToolResult(success=True, provider_key=self.provider_key, artifacts={"video_path": saved_path})
            except Exception as exc:
                screenshot = await self._screenshot_failure(page, prefix="capcut")
                await self._log_tool_event("failure", {"task_id": task_id, "reason": str(exc), "screenshot_path": screenshot})
                return ToolResult(success=False, reason=str(exc), provider_key=self.provider_key, screenshot_path=screenshot)

    async def _maybe_import_subtitles(self, page, srt_path: Path) -> None:
        await self._click_first(page, ["text=Captions", "text=Subtitles"])
        for selector in ["input[type='file'][accept*='.srt']", "input[type='file']"]:
            locator = page.locator(selector)
            if await locator.count():
                await locator.first.set_input_files(srt_path.as_posix())
                return
        await self._click_first(page, ["text=Import subtitles", "text=Upload SRT", "text=Import"])

    async def _maybe_apply_trim(self, page, cut_start: float, cut_end: float) -> None:
        try:
            await self._fill_first(page, ["input[aria-label*='Start']", "input[placeholder*='Start']"], self._seconds_to_timestamp(cut_start))
            await self._fill_first(page, ["input[aria-label*='End']", "input[placeholder*='End']"], self._seconds_to_timestamp(cut_end))
        except Exception:
            return

    @staticmethod
    def _seconds_to_timestamp(seconds: float) -> str:
        whole = max(0, int(seconds))
        hours, remainder = divmod(whole, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
