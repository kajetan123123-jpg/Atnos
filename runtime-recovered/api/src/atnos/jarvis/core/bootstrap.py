from __future__ import annotations

from typing import Any

from atnos.core.config import Settings
from atnos.jarvis.core.config import JarvisConfigStore
from atnos.jarvis.core.event_bus import EventBus
from atnos.jarvis.core.goal_planner import GoalPlanner
from atnos.jarvis.core.ollama import JarvisOllamaClient
from atnos.jarvis.core.proactive import ProactiveModule
from atnos.jarvis.core.self_improvement import SelfImprovementModule
from atnos.jarvis.perception.clipboard import ClipboardMonitorModule
from atnos.jarvis.perception.file_watcher import FileWatcherModule
from atnos.jarvis.perception.screen import ScreenPerceptionModule
from atnos.jarvis.perception.system_monitor import SystemMonitorModule
from atnos.jarvis.perception.voice_input import VoiceInputModule
from atnos.jarvis.perception.voice_output import VoiceOutputModule
from atnos.jarvis.workers.browser.session_manager import BrowserSessionManager
from atnos.jarvis.workers.browser.session_monitor import BrowserSessionMonitor
from atnos.jarvis.workers.registry import WorkerRegistry
from atnos.jarvis.workers.worker_pool import WorkerPool
from atnos.jarvis.types import JarvisEvent
from atnos.plugins.registry import PluginRegistry
from atnos.providers.registry import ProviderRegistry


class _SessionPreloadModule:
    name = "browser_session_preload"

    def __init__(self, session_manager: BrowserSessionManager, event_bus: EventBus) -> None:
        self.session_manager = session_manager
        self.event_bus = event_bus

    async def start(self) -> None:
        await self.session_manager.preload_all()
        await self.event_bus.subscribe(JarvisEvent.MANUAL_LOGIN_CONFIRMED, self.session_manager.handle_manual_login_confirmed)

    async def stop(self) -> None:
        await self.event_bus.unsubscribe(JarvisEvent.MANUAL_LOGIN_CONFIRMED, self.session_manager.handle_manual_login_confirmed)
        await self.session_manager.shutdown()

    def status(self) -> dict[str, Any]:
        return self.session_manager.status()


class JarvisBootstrap:
    def __init__(
        self,
        *,
        settings: Settings,
        provider_registry: ProviderRegistry,
        plugin_registry: PluginRegistry,
        event_bus: EventBus,
        config_store: JarvisConfigStore,
    ) -> None:
        self.settings = settings
        self.provider_registry = provider_registry
        self.plugin_registry = plugin_registry
        self.event_bus = event_bus
        self.config_store = config_store
        self.ollama_client = JarvisOllamaClient(settings, config_store)
        self.session_manager = BrowserSessionManager(settings, config_store, event_bus)
        self.session_preload = _SessionPreloadModule(self.session_manager, event_bus)
        self.session_monitor = BrowserSessionMonitor(
            event_bus=event_bus,
            config_store=config_store,
            session_manager=self.session_manager,
        )
        self.worker_registry = WorkerRegistry(settings, config_store, self.session_manager)
        self.goal_planner = GoalPlanner(
            event_bus=event_bus,
            config_store=config_store,
            ollama_client=self.ollama_client,
        )
        self.modules = [
            SystemMonitorModule(event_bus=event_bus, config_store=config_store, settings=settings),
            self.session_preload,
            FileWatcherModule(event_bus=event_bus, config_store=config_store),
            ClipboardMonitorModule(event_bus=event_bus, config_store=config_store),
            VoiceInputModule(event_bus=event_bus, config_store=config_store, settings=settings),
            VoiceOutputModule(event_bus=event_bus, config_store=config_store, settings=settings),
            ScreenPerceptionModule(event_bus=event_bus, config_store=config_store, ollama_client=self.ollama_client),
            self.goal_planner,
            WorkerPool(event_bus=event_bus, config_store=config_store, registry=self.worker_registry),
            SelfImprovementModule(event_bus=event_bus, config_store=config_store, ollama_client=self.ollama_client),
            self.session_monitor,
            ProactiveModule(event_bus=event_bus, config_store=config_store),
        ]
