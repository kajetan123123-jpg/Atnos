from __future__ import annotations

from atnos.plugins.base import PluginDescriptor, PluginPermission


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, PluginDescriptor] = {}

    def register(self, plugin: PluginDescriptor) -> None:
        if plugin.key in self._plugins:
            raise ValueError(f"Plugin '{plugin.key}' is already registered.")
        self._plugins[plugin.key] = plugin

    @property
    def count(self) -> int:
        return len(self._plugins)

    def get(self, key: str) -> PluginDescriptor | None:
        return self._plugins.get(key)

    def list(self) -> list[PluginDescriptor]:
        return list(self._plugins.values())


def build_default_plugin_registry() -> PluginRegistry:
    registry = PluginRegistry()
    registry.register(
        PluginDescriptor(
            key="local-files",
            name="Local Files",
            summary="Read and write local project files with explicit policy checks.",
            permissions=(PluginPermission.READ_FILES, PluginPermission.WRITE_FILES),
            requires_confirmation=False,
        )
    )
    registry.register(
        PluginDescriptor(
            key="document-tools",
            name="Document Tools",
            summary="Prepare structured document workflows for DOCX, PPTX, and Markdown.",
            permissions=(PluginPermission.READ_FILES, PluginPermission.WRITE_FILES),
            requires_confirmation=False,
        )
    )
    return registry
