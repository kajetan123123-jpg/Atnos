from __future__ import annotations

import asyncio
import json
import random
from logging import getLogger
from pathlib import Path
from typing import Any
from uuid import uuid4

from atnos.core.config import Settings
from atnos.jarvis.core.config import JarvisConfigStore
from atnos.jarvis.workers.browser.mixin import BrowserSessionMixin

logger = getLogger(__name__)


class BaseBrowserTool(BrowserSessionMixin):
    kind: str
    provider_key: str
    service_key: str

    def __init__(self, settings: Settings, config_store: JarvisConfigStore, session_manager, local_primary) -> None:
        self.settings = settings
        self.config_store = config_store
        self.session_manager = session_manager
        self.local_primary = local_primary
        self._log_path = self.config_store.paths.logs_dir / self.config_store.current().logging.browser_tools_log

    async def is_available(self, **kwargs) -> bool:
        local_available = await self.local_primary.is_available(**kwargs)
        return not local_available

    def describe(self) -> dict[str, Any]:
        return {"kind": self.kind, "provider_key": self.provider_key, "service_key": self.service_key}

    async def _delay(self) -> None:
        await asyncio.sleep(random.uniform(2.0, 5.0))

    async def _screenshot_failure(self, page, *, prefix: str) -> str | None:
        try:
            path = (self.config_store.paths.screenshots_dir / f"{prefix}-{uuid4().hex}.png").resolve()
            await page.screenshot(path=path.as_posix(), full_page=True)
            return path.as_posix()
        except Exception:
            logger.exception("Failed to capture browser fallback screenshot.")
            return None

    async def _download_to_path(self, download, target_path: Path) -> str:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        await download.save_as(target_path.as_posix())
        return target_path.as_posix()

    async def _log_tool_event(self, action: str, payload: dict[str, Any]) -> None:
        line = json.dumps(
            {
                "tool": self.provider_key,
                "service": self.service_key,
                "action": action,
                "payload": payload,
            },
            ensure_ascii=False,
        )
        await asyncio.to_thread(self._append_log_line, line)

    def _append_log_line(self, line: str) -> None:
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    async def _fill_first(self, page, selectors: list[str], value: str) -> None:
        for selector in selectors:
            locator = page.locator(selector)
            if await locator.count():
                await locator.first.fill(value)
                return
        raise RuntimeError(f"No matching selector to fill: {selectors!r}")

    async def _click_first(self, page, selectors: list[str]) -> None:
        for selector in selectors:
            locator = page.locator(selector)
            if await locator.count():
                await locator.first.click()
                return
        raise RuntimeError(f"No matching selector to click: {selectors!r}")
