from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from atnos.jarvis.core.module import JarvisModule
from atnos.jarvis.types import DetectedFile, JarvisEvent


class FileWatcherModule(JarvisModule):
    def __init__(self, *, event_bus, config_store) -> None:
        super().__init__(name="file_watcher", event_bus=event_bus, config_store=config_store)
        self._observer = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._available = True
        self._last_error: str | None = None

    async def start(self) -> None:
        if self._observer is not None:
            return
        self._loop = asyncio.get_running_loop()
        try:
            from watchdog.events import FileSystemEventHandler  # type: ignore
            from watchdog.observers import Observer  # type: ignore
        except Exception as exc:
            self._available = False
            self._last_error = str(exc)
            return

        watch_folders = self._load_watch_folders()
        module = self

        class Handler(FileSystemEventHandler):
            def on_created(self, event) -> None:
                if event.is_directory or module._loop is None:
                    return
                asyncio.run_coroutine_threadsafe(module._handle_file(Path(event.src_path)), module._loop)

        observer = Observer()
        handler = Handler()
        for folder in watch_folders:
            if folder.exists():
                observer.schedule(handler, folder.as_posix(), recursive=False)
        observer.start()
        self._observer = observer

    async def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
        self._loop = None

    def status(self) -> dict[str, Any]:
        return {
            "running": self._observer is not None,
            "available": self._available,
            "last_error": self._last_error,
            "watch_folders_path": self.config_store.paths.watch_folders_path.as_posix(),
        }

    def _load_watch_folders(self) -> list[Path]:
        payload = json.loads(self.config_store.paths.watch_folders_path.read_text(encoding="utf-8"))
        return [Path(item).expanduser().resolve() for item in payload.get("watch_folders", []) if str(item).strip()]

    async def _handle_file(self, path: Path) -> None:
        kind = self._classify(path)
        payload = DetectedFile(path=path.as_posix(), kind=kind, source_folder=path.parent.as_posix())
        await self.event_bus.publish(JarvisEvent.FILE_DETECTED, payload)

    @staticmethod
    def _classify(path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in {".mp4", ".mov", ".mkv", ".webm"}:
            return "video"
        if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
            return "image"
        if suffix in {".txt", ".md", ".doc", ".docx", ".pdf"}:
            return "text"
        if suffix in {".url"}:
            return "url"
        return "file"
