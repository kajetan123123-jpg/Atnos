from __future__ import annotations

from atnos.providers.base import ProviderCapability, ProviderDescriptor


def build_mock_provider() -> ProviderDescriptor:
    return ProviderDescriptor(
        key="mock-provider",
        label="Mock Provider",
        capabilities=(ProviderCapability.CHAT,),
        local_first=True,
        response_prefix="Mock provider stub response:",
    )
