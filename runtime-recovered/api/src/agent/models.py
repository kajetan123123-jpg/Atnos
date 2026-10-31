from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AgentTaskType(str, Enum):
    CHAT_GENERAL = "chat_general"
    CODING = "coding"
    UBUNTU_CONTROL = "ubuntu_control"
    BROWSER_CONTROL = "browser_control"
    CONTENT_SHORT = "content_short"
    FILE_ANALYSIS = "file_analysis"
    MEMORY = "memory"
    BRIEFING = "briefing"
    YOUTUBE = "youtube"
    RESEARCH = "research"
    IMAGE_GENERATION = "image_generation"
    DOCUMENT_GENERATION = "document_generation"
    SETTINGS = "settings"
    UNKNOWN = "unknown"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AgentRunStatus(str, Enum):
    COMPLETED = "completed"
    AWAITING_APPROVAL = "awaiting_approval"
    FAILED = "failed"
    SKIPPED = "skipped"


class AgentExecutionMode(str, Enum):
    ANSWER_ONLY = "answer_only"
    PROPOSE_PLAN = "propose_plan"
    EXECUTE = "execute"
    REQUIRE_APPROVAL = "require_approval"
    CREATE_ARTIFACT = "create_artifact"


@dataclass(frozen=True, slots=True)
class TaskClassification:
    task_type: AgentTaskType
    confidence: float
    reason: str


@dataclass(frozen=True, slots=True)
class SkillRoute:
    skill: str
    tool_name: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class AgentPlanStep:
    name: str
    tool_name: str | None
    args: dict[str, Any] = field(default_factory=dict)
    expected_result: str = ""


@dataclass(frozen=True, slots=True)
class AgentExecutionDecision:
    mode: AgentExecutionMode
    reason: str
    user_visible_summary: str
    should_execute: bool
    creates_artifact: bool = False
    requires_approval: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "reason": self.reason,
            "user_visible_summary": self.user_visible_summary,
            "should_execute": self.should_execute,
            "creates_artifact": self.creates_artifact,
            "requires_approval": self.requires_approval,
        }


@dataclass(frozen=True, slots=True)
class ToolCallResult:
    tool_name: str
    success: bool
    status: str
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True, slots=True)
class VerificationResult:
    ok: bool
    status: str
    message: str


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    task: str
    task_type: AgentTaskType
    skill: str
    risk_level: RiskLevel
    status: AgentRunStatus
    response: str
    approval_required: bool
    plan: tuple[AgentPlanStep, ...] = ()
    tool_results: tuple[ToolCallResult, ...] = ()
    verification: VerificationResult | None = None
    memory_written: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "task_type": self.task_type.value,
            "skill": self.skill,
            "risk_level": self.risk_level.value,
            "status": self.status.value,
            "response": self.response,
            "approval_required": self.approval_required,
            "plan": [
                {
                    "name": step.name,
                    "tool_name": step.tool_name,
                    "args": step.args,
                    "expected_result": step.expected_result,
                }
                for step in self.plan
            ],
            "tool_results": [
                {
                    "tool_name": item.tool_name,
                    "success": item.success,
                    "status": item.status,
                    "result": item.result,
                    "error": item.error,
                }
                for item in self.tool_results
            ],
            "verification": (
                {
                    "ok": self.verification.ok,
                    "status": self.verification.status,
                    "message": self.verification.message,
                }
                if self.verification is not None
                else None
            ),
            "memory_written": self.memory_written,
            "metadata": self.metadata,
        }
