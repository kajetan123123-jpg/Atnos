from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class LiteLLMModelRoute:
    id: str
    provider: str
    model: str
    use_for: list[str]
    env_vars: list[str]


DEFAULT_LITELLM_ROUTES = [
    LiteLLMModelRoute(
        id="ollama-local",
        provider="ollama",
        model=os.getenv("OLLAMA_MAIN_MODEL", os.getenv("OLLAMA_CHAT_MODEL", "qwen3:14b")),
        use_for=["local_default", "private_context", "agent_planning", "content_quality"],
        env_vars=["OLLAMA_BASE_URL", "OLLAMA_MAIN_MODEL", "OLLAMA_CHAT_MODEL"],
    ),
    LiteLLMModelRoute(
        id="gpt-main",
        provider="openai",
        model=os.getenv("OPENAI_MAIN_MODEL", "gpt-4.1"),
        use_for=["general_reasoning", "tool_planning", "writing"],
        env_vars=["OPENAI_API_KEY", "OPENAI_MAIN_MODEL"],
    ),
    LiteLLMModelRoute(
        id="claude-main",
        provider="anthropic",
        model=os.getenv("ANTHROPIC_MAIN_MODEL", "claude-sonnet-4"),
        use_for=["coding", "long_context", "critical_review"],
        env_vars=["ANTHROPIC_API_KEY", "ANTHROPIC_MAIN_MODEL"],
    ),
    LiteLLMModelRoute(
        id="gemini-main",
        provider="gemini",
        model=os.getenv("GEMINI_MODEL", os.getenv("GEMINI_MAIN_MODEL", "gemini-3.6-flash")),
        use_for=["research", "multimodal", "search_assisted_reasoning"],
        env_vars=["GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_MAIN_MODEL"],
    ),
    LiteLLMModelRoute(
        id="ask-all",
        provider="litellm-router",
        model="gpt-main+claude-main+gemini-main",
        use_for=["multi_model_comparison", "quality_gate", "high_stakes_second_pass"],
        env_vars=["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"],
    ),
]


class LiteLLMGatewayProvider:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = base_url or os.getenv("LITELLM_GATEWAY_URL", "http://127.0.0.1:4000")

    def route_config(self) -> list[dict[str, object]]:
        return [
            {
                "id": route.id,
                "provider": route.provider,
                "model": route.model,
                "use_for": list(route.use_for),
                "env_vars": list(route.env_vars),
            }
            for route in DEFAULT_LITELLM_ROUTES
        ]

    def is_configured(self) -> bool:
        return bool(self.base_url)
