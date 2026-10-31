from __future__ import annotations

from dataclasses import replace

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.plugins.registry import PluginRegistry
from atnos.providers.registry import ProviderRegistry
from atnos.skills.ubuntu_skill import build_ubuntu_plan, detect_ubuntu_intent, format_ubuntu_chat_response

from agent.agent_logging import AgentJsonlLogger
from agent.approval_gate import ApprovalGate
from agent.execution_director import AgentExecutionDirector
from agent.failure_recovery import FailureRecoveryPlanner
from agent.memory_writer import AgentMemoryWriter
from agent.models import AgentExecutionDecision, AgentPlanStep, AgentRunResult, AgentRunStatus, AgentTaskType
from agent.risk_policy import RiskPolicy
from agent.skill_router import SkillRouter
from agent.task_classifier import TaskClassifier
from agent.tool_executor import ToolExecutor, ToolExecutorContext, extract_ubuntu_command
from agent.verification import AgentVerifier
from memory import AgentMemory


class AgentOrchestrator:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        session: Session | None = None,
        provider_registry: ProviderRegistry | None = None,
        plugin_registry: PluginRegistry | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.classifier = TaskClassifier()
        self.router = SkillRouter()
        self.risk_policy = RiskPolicy()
        self.approval_gate = ApprovalGate(self.settings)
        self.execution_director = AgentExecutionDirector()
        self.failure_recovery = FailureRecoveryPlanner()
        self.verifier = AgentVerifier()
        self.memory_writer = AgentMemoryWriter(self.settings)
        self.agent_memory = AgentMemory(self.settings)
        self.logger = AgentJsonlLogger(self.settings)
        self.executor = ToolExecutor(
            ToolExecutorContext(
                settings=self.settings,
                session=session,
                provider_registry=provider_registry,
                plugin_registry=plugin_registry,
                logger=self.logger,
            )
        )

    def run(self, task: str) -> AgentRunResult:
        classification = self.classifier.classify(task)
        route = self.router.route(classification)
        memory_context = self.retrieve_memory(task)
        plan = self.plan_steps(task=task, task_type=classification.task_type, tool_name=route.tool_name)
        risk_level = self.risk_policy.classify(task, classification.task_type, plan)
        approval_required = self.approval_gate.requires_approval(
            risk_level=risk_level,
            task_type=classification.task_type,
        )
        execution_decision = self.execution_director.decide(
            task_type=classification.task_type,
            risk_level=risk_level,
            approval_required=approval_required,
            plan=plan,
        )
        self.logger.log_decision(
            "agent_route",
            {
                "task": task,
                "task_type": classification.task_type.value,
                "classification_reason": classification.reason,
                "skill": route.skill,
                "tool_name": route.tool_name,
                "plan": [
                    {
                        "name": step.name,
                        "tool_name": step.tool_name,
                        "args": step.args,
                        "expected_result": step.expected_result,
                    }
                    for step in plan
                ],
                "risk_level": risk_level.value,
                "approval_required": approval_required,
                "execution_decision": execution_decision.to_dict(),
                "memory_context": memory_context,
            },
        )
        if approval_required:
            result = AgentRunResult(
                task=task,
                task_type=classification.task_type,
                skill=route.skill,
                risk_level=risk_level,
                status=AgentRunStatus.AWAITING_APPROVAL,
                response=self._approval_response(plan=plan, execution_decision=execution_decision),
                approval_required=True,
                plan=plan,
                metadata={
                    "classification_reason": classification.reason,
                    "route_reason": route.reason,
                    "execution_decision": execution_decision.to_dict(),
                    "memory_context": memory_context,
                },
            )
            self.logger.log_decision("agent_result", result.to_dict())
            return result

        if not execution_decision.should_execute:
            result = AgentRunResult(
                task=task,
                task_type=classification.task_type,
                skill=route.skill,
                risk_level=risk_level,
                status=AgentRunStatus.SKIPPED,
                response=self._plan_only_response(plan=plan, execution_decision=execution_decision),
                approval_required=False,
                plan=plan,
                metadata={
                    "classification_reason": classification.reason,
                    "route_reason": route.reason,
                    "execution_decision": execution_decision.to_dict(),
                    "memory_context": memory_context,
                },
            )
            self.logger.log_decision("agent_result", result.to_dict())
            return result

        tool_results = tuple(self.executor.execute(step) for step in plan if step.tool_name is not None)
        verification = self.verifier.verify(tool_results)
        recovery_actions = self.failure_recovery.plan_recovery(tool_results)
        status = AgentRunStatus.COMPLETED if verification.ok else AgentRunStatus.FAILED
        result = AgentRunResult(
            task=task,
            task_type=classification.task_type,
            skill=route.skill,
            risk_level=risk_level,
            status=status,
            response=self.respond_to_user(
                task_type=classification.task_type,
                tool_results=tool_results,
                verification_ok=verification.ok,
                verification_message=verification.message,
                recovery_actions=recovery_actions,
            ),
            approval_required=False,
            plan=plan,
            tool_results=tool_results,
            verification=verification,
            memory_written=False,
            metadata={
                "classification_reason": classification.reason,
                "route_reason": route.reason,
                "execution_decision": execution_decision.to_dict(),
                "memory_context": memory_context,
                "recovery_actions": list(recovery_actions),
                "artifacts": self._extract_artifacts(tool_results),
            },
        )
        memory_written = self.memory_writer.write(result)
        result = replace(result, memory_written=memory_written)
        self.logger.log_decision("agent_result", result.to_dict())
        return result

    def retrieve_memory(self, task: str) -> dict[str, object]:
        records = self.agent_memory.search_operational_memory(task, limit=5)
        if not records:
            return {"used": False, "reason": "No matching operational memory records."}
        return {
            "used": True,
            "source": "obsidian_operational_memory_index",
            "records": [
                {
                    "memory_id": item.get("memory_id"),
                    "domain": item.get("domain"),
                    "source": item.get("source"),
                    "summary": item.get("summary"),
                    "tags": item.get("tags"),
                    "task_id": item.get("task_id"),
                }
                for item in records
            ],
        }

    def plan_steps(self, *, task: str, task_type: AgentTaskType, tool_name: str | None) -> tuple[AgentPlanStep, ...]:
        if task_type == AgentTaskType.UBUNTU_CONTROL:
            intent = detect_ubuntu_intent(task)
            if intent.activated:
                return build_ubuntu_plan(intent, task)
            command = extract_ubuntu_command(task)
            if command is not None:
                return (
                    AgentPlanStep(
                        name="run_ubuntu_command",
                        tool_name="ubuntu_run",
                        args={"command": command, "timeout": 120},
                        expected_result="Command returns exit code and stdout/stderr.",
                    ),
                )
            return (
                AgentPlanStep(
                    name="check_ubuntu_connection",
                    tool_name="ubuntu_check",
                    expected_result="Ubuntu sandbox returns identity, sudo, hostname and runtime checks.",
                ),
            )
        if task_type == AgentTaskType.BROWSER_CONTROL:
            url = self._extract_url(task) or "about:blank"
            return (
                AgentPlanStep(
                    name="open_browser_page",
                    tool_name="browser_open",
                    args={"url": url},
                    expected_result="Browser opens target page and returns observation.",
                ),
            )
        if task_type == AgentTaskType.CONTENT_SHORT:
            return (
                AgentPlanStep(
                    name="run_local_short_pipeline",
                    tool_name="content_short",
                    args={"task": task},
                    expected_result="Local short workflow is queued or completed without publication.",
                ),
            )
        if task_type == AgentTaskType.MEMORY:
            return (
                AgentPlanStep(
                    name="write_memory_note",
                    tool_name="memory_write",
                    args={"note": task},
                    expected_result="Memory note is recorded.",
                ),
            )
        return (
            AgentPlanStep(
                name="respond_directly",
                tool_name=tool_name,
                expected_result="No external tool required in orchestrator v1.",
            ),
        )

    @staticmethod
    def respond_to_user(
        *,
        task_type: AgentTaskType,
        tool_results: tuple,
        verification_ok: bool,
        verification_message: str,
        recovery_actions: tuple[str, ...] = (),
    ) -> str:
        if not verification_ok:
            lines = [
                "Nie udało się domknąć zadania.",
                f"Weryfikacja: {verification_message}",
            ]
            if recovery_actions:
                lines.append("Najbliższe poprawki:")
                lines.extend(f"- {action}" for action in recovery_actions[:4])
            lines.append("Szczegóły są w logach agentowych i `tool_calls.jsonl`.")
            return "\n".join(lines)
        if not tool_results:
            return "Zadanie zostalo sklasyfikowane i nie wymaga osobnego narzedzia w agent_orchestrator v1."
        first = tool_results[0]
        if task_type == AgentTaskType.UBUNTU_CONTROL:
            return format_ubuntu_chat_response(tool_results)
        if task_type == AgentTaskType.CONTENT_SHORT:
            return "Uruchomiłem lokalny workflow shorta albo zwróciłem czytelny stan gotowości pipeline'u."
        if task_type == AgentTaskType.BROWSER_CONTROL:
            return f"Wykonałem krok browser operatora. Weryfikacja: {verification_message}"
        if first.status == "completed":
            return f"Zadanie wykonane przez agent_orchestrator. Weryfikacja: {verification_message}"
        return "Zadanie zakończone z niejednoznacznym statusem. Sprawdź logi."

    @staticmethod
    def _approval_response(
        *,
        plan: tuple[AgentPlanStep, ...],
        execution_decision: AgentExecutionDecision,
    ) -> str:
        lines = [
            execution_decision.user_visible_summary,
            "Plan do zatwierdzenia:",
        ]
        lines.extend(
            f"- {step.name}: {step.expected_result or 'brak opisu wyniku'}"
            for step in plan
        )
        lines.append("Po zatwierdzeniu wykonam kroki i zapiszę logi.")
        return "\n".join(lines)

    @staticmethod
    def _plan_only_response(
        *,
        plan: tuple[AgentPlanStep, ...],
        execution_decision: AgentExecutionDecision,
    ) -> str:
        lines = [
            execution_decision.user_visible_summary,
            f"Powód: {execution_decision.reason}",
        ]
        if plan:
            lines.append("Proponowany plan:")
            lines.extend(
                f"- {step.name}: {step.expected_result or 'brak opisu wyniku'}"
                for step in plan
            )
        return "\n".join(lines)

    @staticmethod
    def _extract_artifacts(tool_results: tuple) -> list[str]:
        artifacts: list[str] = []
        for result in tool_results:
            for key in ("report_path", "final_mp4", "output_dir", "screenshot_path", "response_path", "prompt_path"):
                value = result.result.get(key)
                if value:
                    artifacts.append(str(value))
        return artifacts

    @staticmethod
    def _extract_url(task: str) -> str | None:
        for token in task.split():
            if token.startswith("http://") or token.startswith("https://"):
                return token.strip(".,)")
        return None
