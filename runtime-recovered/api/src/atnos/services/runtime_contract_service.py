from __future__ import annotations

from typing import TYPE_CHECKING

from atnos.providers.base import required_capability_for_task

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from atnos.providers.registry import ProviderRegistry


class RuntimeContractError(RuntimeError):
    pass


class RuntimeContractService:
    def __init__(self, session: "Session", *, provider_registry: "ProviderRegistry") -> None:
        self.session = session
        self.provider_registry = provider_registry

    def validate_runtime_provider_contract(self) -> None:
        from sqlalchemy import text

        missing_adapters: list[str] = []
        capability_mismatches: list[str] = []
        rows = self.session.execute(
            text(
                """
                SELECT providers.key AS provider_key, model_configs.key AS model_key, model_configs.task AS task
                FROM model_configs
                JOIN providers ON providers.id = model_configs.provider_id
                WHERE model_configs.is_enabled = 1
                  AND model_configs.is_default = 1
                  AND providers.is_enabled = 1
                """
            )
        )
        for provider_key, model_key, task in rows:
            provider_key = str(provider_key)
            model_key = str(model_key)
            task = str(task)
            adapter = self.provider_registry.get(provider_key)
            if adapter is None:
                missing_adapters.append(f"{provider_key}:{model_key}")
                continue
            if not adapter.supports_task(task):
                required_capability = required_capability_for_task(task)
                capability_mismatches.append(
                    f"{provider_key}:{model_key}:{required_capability.value}"
                )

        if missing_adapters:
            joined = ", ".join(sorted(missing_adapters))
            raise RuntimeContractError(
                f"Missing runtime provider adapters for enabled default model routes: {joined}"
            )
        if capability_mismatches:
            joined = ", ".join(sorted(capability_mismatches))
            raise RuntimeContractError(
                f"Runtime provider adapters are missing required task capabilities: {joined}"
            )
