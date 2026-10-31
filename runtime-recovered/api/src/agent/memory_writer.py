from __future__ import annotations

from atnos.core.config import Settings

from agent.models import AgentRunResult
from memory.agent_memory import AgentMemory


class AgentMemoryWriter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def write(self, result: AgentRunResult) -> bool:
        memory = AgentMemory(self.settings)
        payload = memory.write_agent_task_log(
            task=result.task,
            plan=[
                {
                    "name": step.name,
                    "tool_name": step.tool_name,
                    "args": step.args,
                    "expected_result": step.expected_result,
                }
                for step in result.plan
            ],
            tools_used=[
                {
                    "tool_name": item.tool_name,
                    "success": item.success,
                    "status": item.status,
                    "result": item.result,
                    "error": item.error,
                }
                for item in result.tool_results
            ],
            result=result.to_dict(),
            risks=[result.risk_level.value],
            next_steps=[],
        )
        return bool(payload.get("local_path"))
