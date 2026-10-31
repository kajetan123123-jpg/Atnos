from __future__ import annotations

from dataclasses import replace

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.plugins.registry import PluginRegistry
from atnos.providers.registry import ProviderRegistry
from atnos.skills.ubuntu_skill import (
    build_ubuntu_plan,
    detect_ubuntu_intent,
    format_ubuntu_chat_response,
)

from agent.agent_logging import AgentJsonlLogger
from agent.models import AgentRunResult, AgentRunStatus, AgentTaskType
from agent.risk_policy import RiskPolicy
from agent.tool_executor import ToolExecutor, ToolExecutorContext
from agent.verification import AgentVerifier


class UbuntuAgentAdapter:
    """Chat-facing adapter for the Ubuntu SSH sandbox skill."""

    def __init__(
        self,
        *,
        settings: Settings,
        session: Session | None = None,
        provider_registry: ProviderRegistry | None = None,
        plugin_registry: PluginRegistry | None = None,
    ) -> None:
        self.settings = settings
        self.logger = AgentJsonlLogger(settings)
        self.executor = ToolExecutor(
            ToolExecutorContext(
                settings=settings,
                session=session,
                provider_registry=provider_registry,
                plugin_registry=plugin_registry,
                logger=self.logger,
            )
        )
        self.verifier = AgentVerifier()
        self.risk_policy = RiskPolicy()

    def maybe_run(self, task: str) -> AgentRunResult | None:
        intent = detect_ubuntu_intent(task)
        if not intent.activated:
            return None

        plan = build_ubuntu_plan(intent, task)
        risk_level = self.risk_policy.classify(task, AgentTaskType.UBUNTU_CONTROL, plan)
        self.logger.log_decision(
            "ubuntu_skill_route",
            {
                "task": task,
                "intent": intent.action,
                "intent_reason": intent.reason,
                "risk_level": risk_level.value,
                "plan": [
                    {
                        "name": step.name,
                        "tool_name": step.tool_name,
                        "args": step.args,
                        "expected_result": step.expected_result,
                    }
                    for step in plan
                ],
            },
        )

        tool_results = tuple(self.executor.execute(step) for step in plan if step.tool_name is not None)
        verification = self.verifier.verify(tool_results)
        status = AgentRunStatus.COMPLETED if verification.ok else AgentRunStatus.FAILED
        result = AgentRunResult(
            task=task,
            task_type=AgentTaskType.UBUNTU_CONTROL,
            skill="atnos:ubuntu-full-control",
            risk_level=risk_level,
            status=status,
            response=format_ubuntu_chat_response(tool_results),
            approval_required=False,
            plan=plan,
            tool_results=tool_results,
            verification=verification,
            metadata={
                "intent": intent.action,
                "intent_reason": intent.reason,
                "target": {
                    "host": self.settings.atnos_ubuntu_host,
                    "user": self.settings.atnos_ubuntu_user,
                    "workdir": self.settings.atnos_ubuntu_workdir,
                },
            },
        )
        self.logger.log_decision("ubuntu_skill_result", result.to_dict())
        return replace(result, memory_written=False)
