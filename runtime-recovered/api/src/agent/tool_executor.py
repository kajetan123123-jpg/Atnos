from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from sqlalchemy.orm import Session

from atnos.browser_operator import BrowserController
from atnos.core.config import Settings
from atnos.plugins.registry import PluginRegistry
from atnos.providers.registry import ProviderRegistry
from atnos.services.creative_video_operator_service import CreativeVideoOperatorService
from tools.ubuntu_control import (
    UbuntuControlError,
    bootstrap_workspace,
    check_ubuntu_connection,
    create_system_report,
    install_package,
    run_project_command,
    run_ubuntu,
)

from agent.agent_logging import AgentJsonlLogger
from agent.models import AgentPlanStep, ToolCallResult


@dataclass(slots=True)
class ToolExecutorContext:
    settings: Settings
    session: Session | None = None
    provider_registry: ProviderRegistry | None = None
    plugin_registry: PluginRegistry | None = None
    logger: AgentJsonlLogger | None = None


class ToolExecutor:
    def __init__(self, context: ToolExecutorContext) -> None:
        self.context = context

    def execute(self, step: AgentPlanStep) -> ToolCallResult:
        outcome: ToolCallResult | None = None
        if step.tool_name is None:
            outcome = ToolCallResult(
                tool_name="none",
                success=True,
                status="no_tool_needed",
                result={"message": "No tool was required for this step."},
            )
            return outcome
        try:
            if step.tool_name == "ubuntu_check":
                result = check_ubuntu_connection(settings=self.context.settings)
                outcome = self._success(step.tool_name, result)
                return outcome
            if step.tool_name == "ubuntu_system_report":
                result = create_system_report(settings=self.context.settings)
                outcome = self._success(step.tool_name, result, success=result.get("exit_code") == 0)
                return outcome
            if step.tool_name == "ubuntu_bootstrap_workspace":
                result = bootstrap_workspace(settings=self.context.settings)
                outcome = self._success(step.tool_name, result, success=result.get("exit_code") == 0)
                return outcome
            if step.tool_name == "ubuntu_install_package":
                result = install_package(str(step.args.get("package_name") or ""), settings=self.context.settings)
                outcome = self._success(step.tool_name, result, success=result.get("exit_code") == 0)
                return outcome
            if step.tool_name == "ubuntu_project_command":
                command = str(step.args.get("command") or "").strip()
                result = run_project_command(
                    command,
                    int(step.args.get("timeout") or 600),
                    settings=self.context.settings,
                )
                outcome = self._success(step.tool_name, result, success=result.get("exit_code") == 0)
                return outcome
            if step.tool_name == "ubuntu_run":
                command = str(step.args.get("command") or "").strip()
                result = run_ubuntu(command, int(step.args.get("timeout") or 600), settings=self.context.settings)
                outcome = self._success(step.tool_name, result, success=result.get("exit_code") == 0)
                return outcome
            if step.tool_name == "browser_open":
                result = self._browser_open(str(step.args.get("url") or "about:blank"))
                outcome = self._success(step.tool_name, result)
                return outcome
            if step.tool_name == "content_short":
                result = self._content_short(str(step.args.get("task") or ""))
                outcome = self._success(step.tool_name, result, success=bool(result.get("activated")))
                return outcome
            if step.tool_name == "memory_write":
                outcome = self._success(step.tool_name, {"stored": True, "note": step.args.get("note")})
                return outcome
            outcome = ToolCallResult(
                tool_name=step.tool_name,
                success=False,
                status="unknown_tool",
                error=f"Unknown agent tool: {step.tool_name}",
            )
            return outcome
        except UbuntuControlError as exc:
            outcome = ToolCallResult(tool_name=step.tool_name, success=False, status="ubuntu_error", error=str(exc))
            return outcome
        except Exception as exc:
            outcome = ToolCallResult(tool_name=step.tool_name, success=False, status="tool_error", error=f"{type(exc).__name__}: {exc}")
            return outcome
        finally:
            if self.context.logger is not None:
                self.context.logger.log_tool_call(self._build_tool_log_payload(step, outcome))

    @staticmethod
    def _success(tool_name: str, result: dict[str, Any], *, success: bool = True) -> ToolCallResult:
        return ToolCallResult(
            tool_name=tool_name,
            success=success,
            status="completed" if success else "failed",
            result=result,
        )

    def _browser_open(self, url: str) -> dict[str, Any]:
        if not self.context.settings.browser_enabled:
            return {"activated": False, "status": "browser_disabled"}
        controller = BrowserController(settings=self.context.settings)
        controller.start()
        try:
            controller.goto(url)
            observation = controller.observe()
            return {
                "activated": True,
                "status": "opened",
                "url": observation.url,
                "title": observation.title,
            }
        finally:
            controller.close()

    def _content_short(self, task: str) -> dict[str, Any]:
        if self.context.session is None or self.context.provider_registry is None or self.context.plugin_registry is None:
            return {
                "activated": False,
                "status": "runtime_context_required",
                "message": "Content short execution requires database session and provider/plugin registries.",
            }
        service = CreativeVideoOperatorService(
            self.context.session,
            settings=self.context.settings,
            provider_registry=self.context.provider_registry,
            plugin_registry=self.context.plugin_registry,
        )
        operation = service.maybe_handle(
            content=task,
            conversation_id=None,
            project_id=None,
            allow_local_file_write=True,
            quality_profile="standard",
            runtime_controls=None,
        )
        return {
            "activated": operation.activated,
            "assistant_content": operation.assistant_content,
            "metadata": operation.metadata,
            "selected_provider_key": operation.selected_provider_key,
            "selected_model_key": operation.selected_model_key,
            "routing_reason": operation.routing_reason,
        }

    def _build_tool_log_payload(self, step: AgentPlanStep, outcome: ToolCallResult | None) -> dict[str, Any]:
        result = outcome.result if outcome is not None else {}
        command = result.get("command") or step.args.get("command")
        payload: dict[str, Any] = {
            "tool_name": step.tool_name,
            "step": step.name,
            "args": step.args,
            "success": outcome.success if outcome is not None else False,
            "status": outcome.status if outcome is not None else "not_completed",
            "error": outcome.error if outcome is not None else None,
            "result": result,
        }
        if str(step.tool_name or "").startswith("ubuntu_"):
            payload.update(
                {
                    "command": command,
                    "risk_level": _ubuntu_risk_level(str(command or "")),
                    "target": {
                        "host": self.context.settings.atnos_ubuntu_host,
                        "user": self.context.settings.atnos_ubuntu_user,
                        "workdir": self.context.settings.atnos_ubuntu_workdir,
                    },
                    "exit_code": result.get("exit_code"),
                    "stdout_excerpt": _excerpt(str(result.get("stdout") or "")),
                    "stderr_excerpt": _excerpt(
                        str(result.get("stderr") or (outcome.error if outcome is not None else ""))
                    ),
                }
            )
        return payload


def extract_ubuntu_command(task: str) -> str | None:
    match = re.search(r"`([^`]+)`", task)
    if match:
        return match.group(1).strip()
    lowered = task.casefold()
    if "python" in lowered and "version" in lowered:
        return "python3 --version"
    if "raport" in lowered or "report" in lowered:
        return "python3 --version && hostname && df -h | head && free -h"
    return None


def _ubuntu_risk_level(command: str) -> str:
    lowered = command.casefold()
    if "sudo" in lowered:
        return "high"
    return "low"


def _excerpt(value: str, limit: int = 1000) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."
