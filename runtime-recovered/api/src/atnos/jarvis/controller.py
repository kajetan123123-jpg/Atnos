from __future__ import annotations

from typing import Any

from atnos.core.config import Settings
from atnos.plugins.registry import PluginRegistry
from atnos.providers.registry import ProviderRegistry

from atnos.jarvis.runtime import JarvisRuntime


class JarvisRuntimeController:
    def __init__(
        self,
        settings: Settings,
        *,
        provider_registry: ProviderRegistry,
        plugin_registry: PluginRegistry,
    ) -> None:
        self._runtime = JarvisRuntime(
            settings,
            provider_registry=provider_registry,
            plugin_registry=plugin_registry,
        )

    async def start(self) -> dict[str, Any]:
        await self._runtime.start()
        return self.status()

    async def stop(self) -> dict[str, Any]:
        await self._runtime.stop()
        return self.status()

    def status(self) -> dict[str, Any]:
        return self._runtime.status()

    async def inject_goal(self, goal: str) -> None:
        planner = self._runtime.bootstrap.goal_planner
        await planner.set_goal(goal)

    async def list_browser_sessions(self) -> list[dict[str, Any]]:
        await self._runtime.config_store.load()
        return self._runtime.bootstrap.session_manager.describe_sessions()

    async def start_manual_login(self, service: str) -> tuple[bool, dict[str, Any]]:
        await self._runtime.config_store.load()
        return await self._runtime.bootstrap.session_manager.start_manual_login(service)

    async def start_manual_login_for_url(
        self,
        *,
        service: str,
        display_name: str,
        login_url: str,
        validation_url: str | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        await self._runtime.config_store.load()
        return await self._runtime.bootstrap.session_manager.start_manual_login_for_url(
            service=service,
            display_name=display_name,
            login_url=login_url,
            validation_url=validation_url,
        )

    async def confirm_manual_login(self, service: str) -> tuple[bool, dict[str, Any]]:
        await self._runtime.config_store.load()
        return await self._runtime.bootstrap.session_manager.confirm_manual_login(service)

    @property
    def runtime(self) -> JarvisRuntime:
        return self._runtime
