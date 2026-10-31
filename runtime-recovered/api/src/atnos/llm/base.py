from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class LLMRunCancelled(RuntimeError):
    """Cooperative cancellation; callers must not turn it into success or retry."""


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    id: str | None = None
    raw_arguments: str | dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ChatResponse:
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    input_tokens: int | None = None
    output_tokens: int | None = None
    finish_reason: str | None = None
    raw_payload: dict[str, Any] | None = None


class LLMBackend(Protocol):
    async def chat_complete(
        self,
        *,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResponse:
        ...
