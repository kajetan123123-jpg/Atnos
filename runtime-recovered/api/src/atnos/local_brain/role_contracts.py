"""Provider-agnostic Local Brain role contracts.

Roles are capabilities of Atnos, not brand names (Qwen/DeepSeek/Ollama/llama.cpp).
Bindings to concrete models happen elsewhere and stay unbound until benchmark wins.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class BrainModelRole(str, Enum):
    CHAT = "CHAT"
    OPERATOR = "OPERATOR"
    CODING = "CODING"
    CLASSIFIER = "CLASSIFIER"


class ReasoningLevel(str, Enum):
    MINIMAL = "minimal"
    STANDARD = "standard"
    DEEP = "deep"


class LatencyPriority(str, Enum):
    HIGH = "high"
    BALANCED = "balanced"
    QUALITY = "quality"


class ApprovalPolicy(str, Enum):
    """How the role interacts with Atnos permission/approval layers."""

    READ_ONLY_DEFAULT = "read_only_default"
    TOOLS_WITH_HARD_STOP = "tools_with_hard_stop"
    REPO_TOOLS_WITH_HARD_STOP = "repo_tools_with_hard_stop"
    NO_HIGH_IMPACT_ACTIONS = "no_high_impact_actions"


class VerificationPolicy(str, Enum):
    """How operational SUCCESS / DONE / PUBLISHED / CONNECTED may be claimed."""

    CONVERSATIONAL_ONLY = "conversational_only"
    RUNTIME_AND_VERIFIER_REQUIRED = "runtime_and_verifier_required"
    TOOL_RESULT_REQUIRED = "tool_result_required"
    STRUCTURED_OUTPUT_ONLY = "structured_output_only"


@dataclass(frozen=True, slots=True)
class ModelRoleContract:
    role: BrainModelRole
    purpose: str
    required_capabilities: frozenset[str]
    structured_output_required: bool
    tool_calling_required: bool
    reasoning_level: ReasoningLevel
    context_budget_tokens: int
    latency_priority: LatencyPriority
    approval_policy: ApprovalPolicy
    verification_policy: VerificationPolicy
    may_execute_high_impact_tools: bool
    notes: str


ROLE_CONTRACTS: Mapping[BrainModelRole, ModelRoleContract] = {
    BrainModelRole.CHAT: ModelRoleContract(
        role=BrainModelRole.CHAT,
        purpose="Natural conversation with the user; may use retrieved context and Project Brain reads.",
        required_capabilities=frozenset({"chat", "streaming"}),
        structured_output_required=False,
        tool_calling_required=False,
        reasoning_level=ReasoningLevel.STANDARD,
        context_budget_tokens=8_000,
        latency_priority=LatencyPriority.HIGH,
        approval_policy=ApprovalPolicy.READ_ONLY_DEFAULT,
        verification_policy=VerificationPolicy.CONVERSATIONAL_ONLY,
        may_execute_high_impact_tools=False,
        notes="Must not auto-run high-impact tools (publish, delete, connect, spend).",
    ),
    BrainModelRole.OPERATOR: ModelRoleContract(
        role=BrainModelRole.OPERATOR,
        purpose=(
            "Operate Atnos: context → plan → capability selection → permission → "
            "tool execution → verification → state update."
        ),
        required_capabilities=frozenset({"chat", "tool_calls", "structured_output"}),
        structured_output_required=True,
        tool_calling_required=True,
        reasoning_level=ReasoningLevel.DEEP,
        context_budget_tokens=16_000,
        latency_priority=LatencyPriority.BALANCED,
        approval_policy=ApprovalPolicy.TOOLS_WITH_HARD_STOP,
        verification_policy=VerificationPolicy.RUNTIME_AND_VERIFIER_REQUIRED,
        may_execute_high_impact_tools=True,
        notes=(
            "May never self-declare SUCCESS/DONE/PUBLISHED/CONNECTED without "
            "runtime/verifier evidence. Permission denials are binding."
        ),
    ),
    BrainModelRole.CODING: ModelRoleContract(
        role=BrainModelRole.CODING,
        purpose="Repo/code/debugging work through existing Git/files/tests/runtime tools.",
        required_capabilities=frozenset({"chat", "tool_calls", "structured_output"}),
        structured_output_required=True,
        tool_calling_required=True,
        reasoning_level=ReasoningLevel.DEEP,
        context_budget_tokens=16_000,
        latency_priority=LatencyPriority.QUALITY,
        approval_policy=ApprovalPolicy.REPO_TOOLS_WITH_HARD_STOP,
        verification_policy=VerificationPolicy.TOOL_RESULT_REQUIRED,
        may_execute_high_impact_tools=True,
        notes="Not the default lane for ordinary user Chat; route explicitly.",
    ),
    BrainModelRole.CLASSIFIER: ModelRoleContract(
        role=BrainModelRole.CLASSIFIER,
        purpose="Fast routing, classification, extraction, and schema-only output.",
        required_capabilities=frozenset({"chat", "structured_output"}),
        structured_output_required=True,
        tool_calling_required=False,
        reasoning_level=ReasoningLevel.MINIMAL,
        context_budget_tokens=2_000,
        latency_priority=LatencyPriority.HIGH,
        approval_policy=ApprovalPolicy.NO_HIGH_IMPACT_ACTIONS,
        verification_policy=VerificationPolicy.STRUCTURED_OUTPUT_ONLY,
        may_execute_high_impact_tools=False,
        notes="Prefer deterministic settings and minimal context; no high-impact actions.",
    ),
}


def get_role_contract(role: BrainModelRole | str) -> ModelRoleContract:
    if isinstance(role, str):
        role = BrainModelRole(role)
    try:
        return ROLE_CONTRACTS[role]
    except KeyError as exc:
        raise KeyError(f"Unknown Local Brain role: {role!r}") from exc


def unbound_role_bindings() -> dict[str, None]:
    """Initial bindings: roles exist, no model winner yet."""
    return {role.value: None for role in BrainModelRole}
