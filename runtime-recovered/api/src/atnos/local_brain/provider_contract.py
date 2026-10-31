"""LocalModelProvider contract for Local Brain benchmark / adapters.

Benchmark must not depend on Ollama specifically. Existing provider adapters
(ollama-local, llamacpp-local, OpenAI-compatible) can satisfy this Protocol later
without spawning a second LLM subsystem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class LocalProviderCapabilities:
    chat: bool = True
    structured_output: bool = False
    tool_calls: bool = False
    streaming: bool = False
    context_length: int | None = None
    vision: bool = False
    cancel: bool = False
    health: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chat": self.chat,
            "structured_output": self.structured_output,
            "tool_calls": self.tool_calls,
            "streaming": self.streaming,
            "context_length": self.context_length,
            "vision": self.vision,
            "cancel": self.cancel,
            "health": self.health,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class LocalProviderHealth:
    ok: bool
    reason: str
    runtime: str | None = None
    backend: str | None = None
    models: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "runtime": self.runtime,
            "backend": self.backend,
            "models": list(self.models),
        }


@dataclass(frozen=True, slots=True)
class LocalChatMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class LocalToolCall:
    name: str
    arguments: Mapping[str, Any]
    call_id: str | None = None


@dataclass(frozen=True, slots=True)
class LocalGenerationRequest:
    messages: tuple[LocalChatMessage, ...]
    tools: tuple[Mapping[str, Any], ...] = ()
    temperature: float | None = None
    max_tokens: int | None = None
    response_format: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LocalGenerationResult:
    content: str
    tool_calls: tuple[LocalToolCall, ...] = ()
    finish_reason: str | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)
    latency_ms: float | None = None
    ttft_ms: float | None = None
    tokens_per_second: float | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)
    crashed: bool = False
    timed_out: bool = False
    error: str | None = None


@runtime_checkable
class LocalModelProvider(Protocol):
    """Minimal interface for Local Brain harness and future role bindings."""

    @property
    def provider_id(self) -> str: ...

    @property
    def runtime_name(self) -> str: ...

    def detect_capabilities(self) -> LocalProviderCapabilities: ...

    def health(self) -> LocalProviderHealth: ...

    def generate(self, request: LocalGenerationRequest) -> LocalGenerationResult: ...

    def cancel(self) -> bool: ...
