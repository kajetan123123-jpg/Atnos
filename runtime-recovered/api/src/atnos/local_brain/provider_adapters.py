"""Bridge notes: reuse existing ProviderRegistry adapters for Local Brain.

Do not invent a second LLM stack. When wiring real benchmarks:

- `ollama-local` → wrap generate/health into LocalModelProvider
- `llamacpp-local` → same; prefer HIP/ROCm backend metadata when known
- OpenAI-compatible local servers → same Protocol

This module intentionally contains no download or production chat wiring.
"""

from __future__ import annotations

SUPPORTED_RUNTIME_KEYS = ("ollama-local", "llamacpp-local")


def supported_runtime_keys() -> tuple[str, ...]:
    return SUPPORTED_RUNTIME_KEYS
