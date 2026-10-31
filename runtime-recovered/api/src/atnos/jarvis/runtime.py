from __future__ import annotations

import argparse
import asyncio
import signal
from logging import getLogger
from pathlib import Path
from typing import Any

from atnos.core.config import Settings
from atnos.plugins.registry import PluginRegistry
from atnos.providers.registry import ProviderRegistry

from atnos.jarvis.core.bootstrap import JarvisBootstrap
from atnos.jarvis.core.config import JarvisConfigStore
from atnos.jarvis.core.event_bus import EventBus
from atnos.jarvis.types import JarvisEvent

logger = getLogger(__name__)


class JarvisRuntime:
    def __init__(
        self,
        settings: Settings,
        *,
        provider_registry: ProviderRegistry,
        plugin_registry: PluginRegistry,
    ) -> None:
        self.settings = settings
        self.provider_registry = provider_registry
        self.plugin_registry = plugin_registry
        self.event_bus = EventBus()
        self.config_store = JarvisConfigStore(settings)
        self.bootstrap = JarvisBootstrap(
            settings=settings,
            provider_registry=provider_registry,
            plugin_registry=plugin_registry,
            event_bus=self.event_bus,
            config_store=self.config_store,
        )
        self.modules = self.bootstrap.modules
        self._running = False
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._running:
            return
        await self.config_store.start_hot_reload()
        for module in self.modules:
            await module.start()
        self._running = True
        await self.event_bus.publish(JarvisEvent.SPEAK_REQUESTED, {"text": "Atnos online"})

    async def stop(self) -> None:
        if not self._running and not self.modules:
            return
        for module in reversed(self.modules):
            try:
                await module.stop()
            except Exception:
                logger.exception("Jarvis module stop failed | module=%s", module.name)
        await self.config_store.stop_hot_reload()
        self._running = False
        self._stop_event.set()

    async def run_forever(self) -> None:
        await self.start()
        loop = asyncio.get_running_loop()
        stop_requested = asyncio.Event()

        def _request_stop() -> None:
            stop_requested.set()

        for signame in ("SIGINT", "SIGTERM"):
            if hasattr(signal, signame):
                try:
                    loop.add_signal_handler(getattr(signal, signame), _request_stop)
                except NotImplementedError:
                    pass

        await stop_requested.wait()
        await self.stop()

    def status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "config": self.config_store.status(),
            "modules": {module.name: module.status() for module in self.modules},
        }


async def _run_main(root: str | None) -> None:
    settings = Settings(app_root=Path(root).resolve()) if root else Settings()
    from atnos.plugins.registry import build_default_plugin_registry
    from atnos.providers.registry import build_default_provider_registry

    runtime = JarvisRuntime(
        settings,
        provider_registry=build_default_provider_registry(app_root=settings.app_root, settings=settings),
        plugin_registry=build_default_plugin_registry(),
    )
    await runtime.run_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Jarvis runtime for Atnos.")
    parser.add_argument("--root", help="Optional APP_ROOT override.", default=None)
    args = parser.parse_args()
    asyncio.run(_run_main(args.root))


if __name__ == "__main__":
    main()
