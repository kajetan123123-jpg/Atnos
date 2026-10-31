from __future__ import annotations

from atnos.core.config import Settings

from agent.models import AgentTaskType, RiskLevel
from agent.sandbox_policy import ActionTarget, is_action_allowed


class ApprovalGate:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def requires_approval(self, *, risk_level: RiskLevel, task_type: AgentTaskType) -> bool:
        target = _target_for_task(task_type)
        decision = is_action_allowed(
            action=f"agent_task:{task_type.value}",
            target=target,
            risk_level=risk_level,
            settings=self.settings,
        )
        return decision.requires_approval


def _target_for_task(task_type: AgentTaskType) -> str:
    if task_type == AgentTaskType.UBUNTU_CONTROL:
        return ActionTarget.UBUNTU_SANDBOX
    if task_type == AgentTaskType.YOUTUBE:
        return ActionTarget.PUBLICATION
    if task_type in {AgentTaskType.BROWSER_CONTROL, AgentTaskType.RESEARCH}:
        return ActionTarget.CLOUD
    return ActionTarget.MAIN_PC
