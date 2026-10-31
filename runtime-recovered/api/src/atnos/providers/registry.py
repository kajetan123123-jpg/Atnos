from __future__ import annotations

from importlib import import_module
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from atnos.providers.base import ProviderCapability, ProviderDescriptor, ProviderReadiness

if TYPE_CHECKING:
    from atnos.core.config import Settings


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, ProviderDescriptor] = {}

    def register(self, provider: ProviderDescriptor) -> None:
        if provider.key in self._providers:
            raise ValueError(f"Provider '{provider.key}' is already registered.")
        self._providers[provider.key] = provider

    @property
    def count(self) -> int:
        return len(self._providers)

    def get(self, key: str) -> ProviderDescriptor | None:
        return self._providers.get(key)

    def list(self) -> list[ProviderDescriptor]:
        return list(self._providers.values())


class LazyProviderAdapter(ProviderDescriptor):
    def __init__(
        self,
        *,
        key: str,
        label: str,
        capabilities: tuple[ProviderCapability, ...],
        local_first: bool,
        module_name: str,
        class_name: str,
        kwargs: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            key=key,
            label=label,
            capabilities=capabilities,
            local_first=local_first,
        )
        self._module_name = module_name
        self._class_name = class_name
        self._kwargs = kwargs or {}
        self._adapter: ProviderDescriptor | None = None

    def _load_adapter(self) -> ProviderDescriptor:
        if self._adapter is None:
            module = import_module(self._module_name)
            adapter_class = getattr(module, self._class_name)
            self._adapter = adapter_class(**self._kwargs)
        return self._adapter

    def configuration_status(self, *, provider) -> ProviderReadiness:
        return self._load_adapter().configuration_status(provider=provider)

    def check_health(self, *, provider):
        return self._load_adapter().check_health(provider=provider)

    def execute(self, *, task, context, provider, model_config):
        return self._load_adapter().execute(
            task=task,
            context=context,
            provider=provider,
            model_config=model_config,
        )


class StubAnthropicCloudProviderAdapter(ProviderDescriptor):
    def __init__(self) -> None:
        super().__init__(
            key="anthropic-cloud",
            label="Anthropic Cloud",
            capabilities=(
                ProviderCapability.CHAT,
                ProviderCapability.SYNTHESIS,
                ProviderCapability.VISION,
            ),
            local_first=False,
            response_prefix="External fallback provider stub:",
        )

    def configuration_status(self, *, provider) -> ProviderReadiness:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if api_key:
            return ProviderReadiness(ready=True)
        return ProviderReadiness(
            ready=False,
            reason="Brakuje API key dla providera Anthropic.",
            failure_kind="provider_configuration",
        )


class StubOpenAICompatibleCloudProviderAdapter(ProviderDescriptor):
    def __init__(self, *, key: str, label: str, api_key_env_var: str) -> None:
        super().__init__(
            key=key,
            label=label,
            capabilities=(
                ProviderCapability.CHAT,
                ProviderCapability.SYNTHESIS,
                ProviderCapability.VISION,
            ),
            local_first=False,
            response_prefix=f"External fallback provider stub ({label}):",
        )
        self.api_key_env_var = api_key_env_var

    def configuration_status(self, *, provider) -> ProviderReadiness:
        api_key = os.getenv(self.api_key_env_var)
        if api_key:
            return ProviderReadiness(ready=True)
        return ProviderReadiness(
            ready=False,
            reason=f"Brakuje API key dla providera {self.label}.",
            failure_kind="provider_configuration",
        )


def build_runtime_provider_registry(*, app_root: Path, settings: "Settings") -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(
        LazyProviderAdapter(
            key="ollama-local",
            label="Ollama Local",
            capabilities=(
                ProviderCapability.CHAT,
                ProviderCapability.SYNTHESIS,
                ProviderCapability.VISION,
            ),
            local_first=True,
            module_name="atnos.providers.ollama_local",
            class_name="OllamaLocalProviderAdapter",
            kwargs={"app_root": app_root, "settings": settings},
        )
    )
    registry.register(
        LazyProviderAdapter(
            key="llamacpp-local",
            label="llama.cpp Local",
            capabilities=(ProviderCapability.CHAT, ProviderCapability.SYNTHESIS),
            local_first=True,
            module_name="atnos.providers.llamacpp_local",
            class_name="LlamaCppLocalProviderAdapter",
            kwargs={"app_root": app_root},
        )
    )
    registry.register(
        LazyProviderAdapter(
            key="anthropic-cloud",
            label="Anthropic Cloud",
            capabilities=(
                ProviderCapability.CHAT,
                ProviderCapability.SYNTHESIS,
                ProviderCapability.VISION,
            ),
            local_first=False,
            module_name="atnos.providers.anthropic_cloud",
            class_name="AnthropicCloudProviderAdapter",
            kwargs={"app_root": app_root},
        )
    )
    registry.register(
        LazyProviderAdapter(
            key="openai-cloud",
            label="OpenAI Cloud",
            capabilities=(
                ProviderCapability.CHAT,
                ProviderCapability.SYNTHESIS,
                ProviderCapability.VISION,
            ),
            local_first=False,
            module_name="atnos.providers.openai_compatible_cloud",
            class_name="OpenAICompatibleCloudProviderAdapter",
            kwargs={
                "provider_key": "openai-cloud",
                "label": "OpenAI Cloud",
                "app_root": app_root,
                "api_key_env_var": "OPENAI_API_KEY",
                "default_base_url": "https://api.openai.com",
                "model_env_prefix": "OPENAI",
            },
        )
    )
    registry.register(
        LazyProviderAdapter(
            key="gemini-cloud",
            label="Google Gemini",
            capabilities=(
                ProviderCapability.CHAT,
                ProviderCapability.SYNTHESIS,
                ProviderCapability.VISION,
            ),
            local_first=False,
            module_name="atnos.providers.google_gemini",
            class_name="GoogleGeminiProviderAdapter",
            kwargs={"app_root": app_root, "settings": settings},
        )
    )
    return registry


def build_stub_provider_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(
        ProviderDescriptor(
            key="ollama-local",
            label="Ollama Local",
            capabilities=(
                ProviderCapability.CHAT,
                ProviderCapability.SYNTHESIS,
                ProviderCapability.VISION,
            ),
            local_first=True,
            response_prefix="Local-first provider stub:",
        )
    )
    registry.register(
        ProviderDescriptor(
            key="llamacpp-local",
            label="llama.cpp Local",
            capabilities=(ProviderCapability.CHAT, ProviderCapability.SYNTHESIS),
            local_first=True,
            response_prefix="Local llama.cpp provider stub:",
        )
    )
    registry.register(StubAnthropicCloudProviderAdapter())
    registry.register(
        StubOpenAICompatibleCloudProviderAdapter(
            key="openai-cloud",
            label="OpenAI Cloud",
            api_key_env_var="OPENAI_API_KEY",
        )
    )
    registry.register(
        StubOpenAICompatibleCloudProviderAdapter(
            key="gemini-cloud",
            label="Google Gemini",
            api_key_env_var="GEMINI_API_KEY",
        )
    )
    return registry


def build_default_provider_registry(*, app_root: Path, settings: "Settings") -> ProviderRegistry:
    return build_runtime_provider_registry(app_root=app_root, settings=settings)
