from __future__ import annotations

from agent.models import (
    AgentExecutionDecision,
    AgentExecutionMode,
    AgentPlanStep,
    AgentTaskType,
    RiskLevel,
)


class AgentExecutionDirector:
    """Decides how agentic a turn should be.

    This layer keeps Chat simple: the orchestrator decides whether a task needs
    a direct answer, a visible plan, execution, artifact creation, or approval.
    """

    ANSWER_ONLY_TYPES = {
        AgentTaskType.CHAT_GENERAL,
        AgentTaskType.CODING,
        AgentTaskType.FILE_ANALYSIS,
        AgentTaskType.BRIEFING,
        AgentTaskType.SETTINGS,
        AgentTaskType.UNKNOWN,
    }
    ARTIFACT_TYPES = {
        AgentTaskType.CONTENT_SHORT,
        AgentTaskType.IMAGE_GENERATION,
        AgentTaskType.DOCUMENT_GENERATION,
    }
    TOOL_EXECUTION_TYPES = {
        AgentTaskType.UBUNTU_CONTROL,
        AgentTaskType.BROWSER_CONTROL,
        AgentTaskType.CONTENT_SHORT,
        AgentTaskType.MEMORY,
    }

    def decide(
        self,
        *,
        task_type: AgentTaskType,
        risk_level: RiskLevel,
        approval_required: bool,
        plan: tuple[AgentPlanStep, ...],
    ) -> AgentExecutionDecision:
        if approval_required:
            return AgentExecutionDecision(
                mode=AgentExecutionMode.REQUIRE_APPROVAL,
                reason="Risk policy requires approval before execution.",
                user_visible_summary="Zadanie jest ryzykowne albo dotyka zewnętrznego działania, więc wymaga zgody.",
                should_execute=False,
                creates_artifact=task_type in self.ARTIFACT_TYPES,
                requires_approval=True,
            )

        if task_type in self.ARTIFACT_TYPES:
            executable = self._has_tool(plan)
            return AgentExecutionDecision(
                mode=AgentExecutionMode.CREATE_ARTIFACT,
                reason="Task is expected to produce a file or media artifact.",
                user_visible_summary="To zadanie powinno zakończyć się artefaktem, nie tylko opisem.",
                should_execute=executable,
                creates_artifact=True,
            )

        if task_type in self.TOOL_EXECUTION_TYPES and self._has_tool(plan):
            return AgentExecutionDecision(
                mode=AgentExecutionMode.EXECUTE,
                reason="Task has a safe executable tool plan.",
                user_visible_summary="To zadanie wymaga narzędzia, więc uruchamiam kontrolowany workflow.",
                should_execute=True,
            )

        if task_type in self.ANSWER_ONLY_TYPES:
            return AgentExecutionDecision(
                mode=AgentExecutionMode.ANSWER_ONLY,
                reason="Task is better handled by normal Chat/provider flow.",
                user_visible_summary="To zadanie nie wymaga osobnego tool calla w agent_orchestrator.",
                should_execute=False,
            )

        return AgentExecutionDecision(
            mode=AgentExecutionMode.PROPOSE_PLAN,
            reason="Task is not safely executable by the current orchestrator.",
            user_visible_summary="Mogę przygotować plan, ale nie mam jeszcze bezpiecznego wykonawcy dla tego typu zadania.",
            should_execute=False,
        )

    @staticmethod
    def _has_tool(plan: tuple[AgentPlanStep, ...]) -> bool:
        return any(step.tool_name is not None for step in plan)
