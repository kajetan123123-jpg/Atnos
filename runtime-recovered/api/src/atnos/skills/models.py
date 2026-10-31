from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SkillExecutionType(str, Enum):
    LOCAL = "local"
    CONNECTOR = "connector"
    EXTERNAL_API = "external_api"
    MANUAL = "manual"
    FUTURE = "future"


class SkillRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SkillStatus(str, Enum):
    AVAILABLE = "available"
    PLANNED = "planned"
    EXTERNAL = "external"
    UNAVAILABLE = "unavailable"


class Skill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=3)
    namespace: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    category: str = Field(min_length=1)
    triggers: list[str] = Field(default_factory=list)
    good_for: list[str] = Field(default_factory=list)
    not_for: list[str] = Field(default_factory=list)
    input_requirements: list[str] = Field(default_factory=list)
    output_type: str = Field(min_length=1)
    execution_type: SkillExecutionType
    risk_level: SkillRiskLevel
    requires_human_approval: bool = False
    requires_network: bool = False
    requires_files: bool = False
    requires_credentials: bool = False
    priority: int = 0
    status: SkillStatus
    notes: str = ""

    @model_validator(mode="after")
    def _validate_identity(self) -> "Skill":
        expected_id = f"{self.namespace}:{self.name}"
        if self.id != expected_id:
            raise ValueError(f"Skill id '{self.id}' must match namespace:name '{expected_id}'.")
        return self


class SkillRouteDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: str
    selected_skills: list[str] = Field(default_factory=list)
    rejected_skills: list[str] = Field(default_factory=list)
    reasoning_summary: str
    required_inputs: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    next_action: str
    confidence: float = Field(ge=0.0, le=1.0)
