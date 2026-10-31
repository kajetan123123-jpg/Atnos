from __future__ import annotations

import importlib
import pkgutil
from typing import Iterable

from atnos.tools.base import BaseTool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register_tool(self, tool: BaseTool) -> BaseTool:
        if not tool.name.strip():
            raise ValueError("Tool name cannot be empty.")
        self._tools[tool.name] = tool
        return tool

    def register(self, tool_cls):
        tool = tool_cls()
        self.register_tool(tool)
        return tool_cls

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def list_all(self) -> tuple[BaseTool, ...]:
        return tuple(self._tools.values())

    def get_ollama_tools(self, *, names: Iterable[str] | None = None) -> list[dict]:
        return [tool.to_ollama_tool() for tool in self._select(names)]

    def get_openai_tools(self, *, names: Iterable[str] | None = None) -> list[dict]:
        return [tool.to_openai_function() for tool in self._select(names)]

    def _select(self, names: Iterable[str] | None) -> tuple[BaseTool, ...]:
        if names is None:
            return self.list_all()
        selected: list[BaseTool] = []
        for name in names:
            tool = self.get(name)
            if tool is not None:
                selected.append(tool)
        return tuple(selected)


default_tool_registry = ToolRegistry()


def discover_builtin_tools(registry: ToolRegistry = default_tool_registry) -> ToolRegistry:
    import atnos.tools.builtin as builtin_package

    for module_info in pkgutil.iter_modules(builtin_package.__path__):
        importlib.import_module(f"{builtin_package.__name__}.{module_info.name}")
    if registry is not default_tool_registry:
        for tool in default_tool_registry.list_all():
            registry.register_tool(tool)
    return registry
