from __future__ import annotations

from pathlib import Path
from typing import Iterable

import yaml

from integrations.integration_models import IntegrationDefinition


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "integrations.yaml"


class IntegrationRegistry:
    def __init__(self, integrations: Iterable[IntegrationDefinition]) -> None:
        self._integrations = {integration.id: integration for integration in integrations}

    @classmethod
    def from_yaml(cls, path: str | Path = DEFAULT_CONFIG_PATH) -> "IntegrationRegistry":
        config_path = Path(path)
        with config_path.open("r", encoding="utf-8") as file:
            payload = yaml.safe_load(file) or {}
        integrations = [
            IntegrationDefinition.from_mapping(item)
            for item in payload.get("integrations", [])
        ]
        return cls(integrations)

    def get(self, integration_id: str) -> IntegrationDefinition:
        try:
            return self._integrations[integration_id]
        except KeyError as exc:
            raise KeyError(f"Unknown integration: {integration_id}") from exc

    def maybe_get(self, integration_id: str) -> IntegrationDefinition | None:
        return self._integrations.get(integration_id)

    def list_all(self) -> list[IntegrationDefinition]:
        return sorted(
            self._integrations.values(),
            key=lambda item: (item.priority.value, item.id),
        )

    def list_enabled(self) -> list[IntegrationDefinition]:
        return [item for item in self.list_all() if item.enabled]

    def ids(self) -> list[str]:
        return [item.id for item in self.list_all()]


def load_default_registry() -> IntegrationRegistry:
    return IntegrationRegistry.from_yaml(DEFAULT_CONFIG_PATH)
