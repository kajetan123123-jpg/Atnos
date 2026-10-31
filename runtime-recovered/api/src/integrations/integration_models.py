from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class IntegrationType(str, Enum):
    API = "api"
    BROWSER = "browser"
    LOCAL = "local"
    MCP = "mcp"
    FUTURE = "future"


class IntegrationPriority(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class IntegrationStatus(str, Enum):
    READY = "ready"
    CONFIG_REQUIRED = "config_required"
    DISABLED = "disabled"
    SCAFFOLD = "scaffold"
    FUTURE = "future"


@dataclass(frozen=True)
class IntegrationDefinition:
    id: str
    name: str
    type: IntegrationType
    priority: IntegrationPriority
    enabled: bool
    requires_api_key: bool
    requires_browser_login: bool
    risk_level: str
    use_cases: list[str] = field(default_factory=list)
    not_for: list[str] = field(default_factory=list)
    env_vars: list[str] = field(default_factory=list)
    status: IntegrationStatus = IntegrationStatus.SCAFFOLD

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "IntegrationDefinition":
        return cls(
            id=str(payload["id"]),
            name=str(payload["name"]),
            type=IntegrationType(str(payload["type"])),
            priority=IntegrationPriority(str(payload["priority"])),
            enabled=bool(payload.get("enabled", False)),
            requires_api_key=bool(payload.get("requires_api_key", False)),
            requires_browser_login=bool(payload.get("requires_browser_login", False)),
            risk_level=str(payload.get("risk_level", "medium")),
            use_cases=[str(item) for item in payload.get("use_cases", [])],
            not_for=[str(item) for item in payload.get("not_for", [])],
            env_vars=[str(item) for item in payload.get("env_vars", [])],
            status=IntegrationStatus(str(payload.get("status", "scaffold"))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type.value,
            "priority": self.priority.value,
            "enabled": self.enabled,
            "requires_api_key": self.requires_api_key,
            "requires_browser_login": self.requires_browser_login,
            "risk_level": self.risk_level,
            "use_cases": list(self.use_cases),
            "not_for": list(self.not_for),
            "env_vars": list(self.env_vars),
            "status": self.status.value,
        }


@dataclass(frozen=True)
class ProviderDecision:
    provider_id: str
    reason: str
    confidence: float
    task_category: str
    requires_approval: bool = False
    fallback_provider_ids: list[str] = field(default_factory=list)
    safety_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "reason": self.reason,
            "confidence": self.confidence,
            "task_category": self.task_category,
            "requires_approval": self.requires_approval,
            "fallback_provider_ids": list(self.fallback_provider_ids),
            "safety_notes": list(self.safety_notes),
        }
