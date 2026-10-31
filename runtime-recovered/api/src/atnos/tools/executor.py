from __future__ import annotations

import asyncio
from time import perf_counter
from jsonschema import Draft202012Validator, SchemaError, ValidationError

from atnos.llm.base import ToolCall
from atnos.tools.base import ToolResult
from atnos.tools.registry import ToolRegistry, default_tool_registry


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry = default_tool_registry,
        *,
        timeout_seconds: float = 120.0,
    ) -> None:
        self.registry = registry
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero.")
        self.timeout_seconds = timeout_seconds

    def validate_tool_call(self, tool_call: ToolCall) -> str | None:
        tool = self.registry.get(tool_call.name)
        if tool is None:
            names = ", ".join(tool.name for tool in self.registry.list_all()) or "(none)"
            return f"Unknown tool: {tool_call.name}. Available tool names: {names}. Use an exact name; no operation was executed."
        try:
            Draft202012Validator.check_schema(tool.parameters)
            Draft202012Validator(tool.parameters).validate(tool_call.arguments)
        except (ValidationError, SchemaError) as exc:
            location = ".".join(str(item) for item in exc.absolute_path) or "arguments"
            return f"Invalid tool arguments at {location}: {exc.validator}"
        return None

    async def execute_tool_call(self, tool_call: ToolCall) -> ToolResult:
        started = perf_counter()
        tool = self.registry.get(tool_call.name)
        if tool is None:
            return ToolResult(
                success=False,
                error=self.validate_tool_call(tool_call),
                latency_seconds=perf_counter() - started,
            )
        if tool.requires_approval:
            return ToolResult(
                success=False,
                error=f"Tool requires approval before execution: {tool_call.name}",
                latency_seconds=perf_counter() - started,
            )
        validation_error = self.validate_tool_call(tool_call)
        if validation_error is not None:
            return ToolResult(
                success=False,
                error=validation_error,
                latency_seconds=perf_counter() - started,
            )
        try:
            data = await asyncio.wait_for(
                tool.execute(**tool_call.arguments),
                timeout=self.timeout_seconds,
            )
        except TimeoutError:
            return ToolResult(
                success=False,
                error=(
                    f"TimeoutError: tool {tool_call.name} exceeded "
                    f"{self.timeout_seconds:g} seconds"
                ),
                latency_seconds=perf_counter() - started,
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"{exc.__class__.__name__}: {exc}",
                latency_seconds=perf_counter() - started,
            )
        return ToolResult(
            success=True,
            data=data,
            latency_seconds=perf_counter() - started,
        )
