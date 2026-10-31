from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from logging import getLogger
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from atnos.core.config import Settings

logger = getLogger(__name__)


class LlmConfig(BaseModel):
    vision_model: str = "qwen3-vl:8b"
    chat_model: str = "qwen3:8b"
    orchestration_model: str = "qwen3:8b"
    ollama_base_url: str | None = None
    timeout_s: float = 60.0


class ScreenConfig(BaseModel):
    enabled: bool = True
    interval_s: float = 2.0
    min_emit_confidence: float = 0.35


class VoiceInputConfig(BaseModel):
    enabled: bool = True
    wake_word: str = "Atnos"
    silence_duration_s: float = 1.2
    sample_rate: int = 16000
    wake_word_model_path: str | None = None
    stt_model: str | None = None
    model_path: str | None = None


class VoiceOutputConfig(BaseModel):
    enabled: bool = True
    backend: str = "kokoro_local"
    voice_preset: str = "af_heart"


class SystemMonitorConfig(BaseModel):
    enabled: bool = True
    interval_s: float = 5.0
    vram_low_threshold_mb: int = 1024


class FileWatcherConfig(BaseModel):
    enabled: bool = True
    watch_folders_file: str = "watch_folders.json"


class ClipboardConfig(BaseModel):
    enabled: bool = True
    interval_s: float = 1.0


class WorkerPoolConfig(BaseModel):
    enabled: bool = True
    worker_count: int = 3


class SessionConfig(BaseModel):
    enabled: bool = True
    validation_interval_s: float = 1800.0
    page_limit_per_service: int = 5


class ProactiveConfig(BaseModel):
    enabled: bool = True
    interval_s: float = 7200.0
    trend_score_threshold: float = 0.65


class LoggingConfig(BaseModel):
    browser_sessions_log: str = "browser_sessions.log"
    browser_tools_log: str = "browser_tools.log"
    runtime_log: str = "runtime.log"


class JarvisConfig(BaseModel):
    llm: LlmConfig = Field(default_factory=LlmConfig)
    screen: ScreenConfig = Field(default_factory=ScreenConfig)
    voice_input: VoiceInputConfig = Field(default_factory=VoiceInputConfig)
    voice_output: VoiceOutputConfig = Field(default_factory=VoiceOutputConfig)
    system_monitor: SystemMonitorConfig = Field(default_factory=SystemMonitorConfig)
    file_watcher: FileWatcherConfig = Field(default_factory=FileWatcherConfig)
    clipboard: ClipboardConfig = Field(default_factory=ClipboardConfig)
    worker_pool: WorkerPoolConfig = Field(default_factory=WorkerPoolConfig)
    sessions: SessionConfig = Field(default_factory=SessionConfig)
    proactive: ProactiveConfig = Field(default_factory=ProactiveConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


@dataclass(frozen=True, slots=True)
class JarvisPaths:
    root_dir: Path
    config_path: Path
    goals_path: Path
    performance_log_path: Path
    prompt_versions_dir: Path
    voice_profile_dir: Path
    watch_folders_path: Path
    browser_sessions_dir: Path
    logs_dir: Path
    screenshots_dir: Path
    runtime_logs_dir: Path
    images_output_dir: Path


class JarvisConfigStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.paths = self._build_paths(settings)
        self._config = JarvisConfig()
        self._mtime_ns: int | None = None
        self._reload_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._subscribers: list[asyncio.Queue[JarvisConfig]] = []

    @staticmethod
    def _build_paths(settings: Settings) -> JarvisPaths:
        root_dir = (settings.data_dir / "jarvis").resolve()
        return JarvisPaths(
            root_dir=root_dir,
            config_path=root_dir / "config.json",
            goals_path=root_dir / "goals.json",
            performance_log_path=root_dir / "performance_log.jsonl",
            prompt_versions_dir=root_dir / "prompt_versions",
            voice_profile_dir=root_dir / "voice_profile",
            watch_folders_path=root_dir / "watch_folders.json",
            browser_sessions_dir=root_dir / "browser_sessions",
            logs_dir=root_dir / "logs",
            screenshots_dir=root_dir / "logs" / "screenshots",
            runtime_logs_dir=root_dir / "runtime_logs",
            images_output_dir=root_dir / "tmp" / "atnos_outputs" / "images",
        )

    def ensure_layout(self) -> None:
        for path in (
            self.paths.root_dir,
            self.paths.prompt_versions_dir,
            self.paths.voice_profile_dir,
            self.paths.browser_sessions_dir,
            self.paths.logs_dir,
            self.paths.screenshots_dir,
            self.paths.runtime_logs_dir,
            self.paths.images_output_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
        if not self.paths.watch_folders_path.exists():
            self.paths.watch_folders_path.write_text(
                json.dumps(
                    {
                        "watch_folders": [
                            self.settings.uploads_dir.as_posix(),
                            (Path.home() / "Downloads").as_posix(),
                            (Path.home() / "Desktop").as_posix(),
                        ]
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        else:
            self._migrate_watch_folders()

    def _migrate_watch_folders(self) -> None:
        """Replace obsolete Atnos upload roots after a disk migration."""
        try:
            payload = json.loads(self.paths.watch_folders_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        folders = payload.get("watch_folders")
        if not isinstance(folders, list):
            return

        migrated: list[str] = []
        changed = False
        for item in folders:
            value = str(item or "").strip()
            normalized = value.replace("\\", "/").casefold()
            is_obsolete_atnos_upload = (
                normalized.endswith("/atnos/data/uploads")
                or "/atnossystem/appdata/roaming/atnos/data/uploads" in normalized
            )
            replacement = self.settings.uploads_dir.as_posix() if is_obsolete_atnos_upload else value
            changed = changed or replacement != value
            if replacement and replacement not in migrated:
                migrated.append(replacement)

        if changed:
            payload["watch_folders"] = migrated
            self.paths.watch_folders_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    async def load(self, *, force: bool = False) -> JarvisConfig:
        self.ensure_layout()
        if not self.paths.config_path.exists():
            await self._write_default_config()
        stat = self.paths.config_path.stat()
        if force or self._mtime_ns != stat.st_mtime_ns:
            raw = await asyncio.to_thread(self.paths.config_path.read_text, "utf-8")
            self._config = JarvisConfig.model_validate(json.loads(raw))
            if not self._config.llm.ollama_base_url:
                self._config.llm.ollama_base_url = self.settings.ollama_base_url
            self._mtime_ns = stat.st_mtime_ns
        return self._config

    def current(self) -> JarvisConfig:
        return self._config

    async def _write_default_config(self) -> None:
        default = JarvisConfig(
            llm=LlmConfig(ollama_base_url=self.settings.ollama_base_url),
        )
        payload = default.model_dump(mode="json")
        await asyncio.to_thread(
            self.paths.config_path.write_text,
            json.dumps(payload, indent=2),
            "utf-8",
        )

    async def start_hot_reload(self, interval_s: float = 2.0) -> None:
        await self.load()
        if self._reload_task is not None and not self._reload_task.done():
            return
        self._stop_event.clear()
        self._reload_task = asyncio.create_task(self._reload_loop(interval_s), name="jarvis-config-reload")

    async def stop_hot_reload(self) -> None:
        self._stop_event.set()
        if self._reload_task is not None:
            self._reload_task.cancel()
            try:
                await self._reload_task
            except asyncio.CancelledError:
                pass
            self._reload_task = None

    async def _reload_loop(self, interval_s: float) -> None:
        while not self._stop_event.is_set():
            try:
                previous = self._mtime_ns
                await self.load()
                if previous is not None and self._mtime_ns != previous:
                    await self._notify_subscribers()
            except Exception:
                logger.exception("Jarvis config hot-reload failed.")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval_s)
            except asyncio.TimeoutError:
                continue

    async def subscribe(self) -> asyncio.Queue[JarvisConfig]:
        queue: asyncio.Queue[JarvisConfig] = asyncio.Queue()
        self._subscribers.append(queue)
        await queue.put(self._config)
        return queue

    async def _notify_subscribers(self) -> None:
        for queue in tuple(self._subscribers):
            await queue.put(self._config)

    def status(self) -> dict[str, Any]:
        return {
            "config_path": self.paths.config_path.as_posix(),
            "watch_folders_path": self.paths.watch_folders_path.as_posix(),
            "reload_running": self._reload_task is not None and not self._reload_task.done(),
            "loaded": self._mtime_ns is not None,
        }
