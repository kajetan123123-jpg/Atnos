from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class SkillCategory(str, Enum):
    CREATION = "creation"
    SYSTEM = "system"
    RESEARCH = "research"
    DEV = "dev"


class SkillReadiness(str, Enum):
    READY = "ready"
    PARTIAL = "partial"
    BLUEPRINT = "blueprint"
    FUTURE = "future"


@dataclass(frozen=True, slots=True)
class SkillToolContract:
    tool_name: str
    description: str
    schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class NativeSkillBlueprint:
    key: str
    label: str
    category: SkillCategory
    readiness: SkillReadiness
    summary: str
    repo_name: str
    repo_url: str
    why_it_matters: str
    amd_profile: str
    next_integration_step: str
    related_modules: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    tool_contracts: tuple[SkillToolContract, ...] = ()
    learning_note_path: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "category": self.category.value,
            "readiness": self.readiness.value,
            "summary": self.summary,
            "repo_name": self.repo_name,
            "repo_url": self.repo_url,
            "why_it_matters": self.why_it_matters,
            "amd_profile": self.amd_profile,
            "next_integration_step": self.next_integration_step,
            "related_modules": list(self.related_modules),
            "blockers": list(self.blockers),
            "tool_contracts": [
                {
                    "tool_name": item.tool_name,
                    "description": item.description,
                    "schema": dict(item.schema),
                }
                for item in self.tool_contracts
            ],
            "learning_note_path": self.learning_note_path,
        }
