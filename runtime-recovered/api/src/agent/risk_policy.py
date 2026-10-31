from __future__ import annotations

from agent.models import AgentPlanStep, AgentTaskType, RiskLevel


class RiskPolicy:
    HIGH_TOKENS = (
        "sudo",
        "rm ",
        "del ",
        "delete",
        "kasuj",
        "usun",
        "publish",
        "opublikuj",
        "upload",
        "billing",
        "api key",
        "haslo",
        "password",
        "konto",
        "systemctl",
    )
    MEDIUM_TYPES = {
        AgentTaskType.BROWSER_CONTROL,
        AgentTaskType.CONTENT_SHORT,
        AgentTaskType.IMAGE_GENERATION,
        AgentTaskType.DOCUMENT_GENERATION,
        AgentTaskType.MEMORY,
    }

    def classify(self, task: str, task_type: AgentTaskType, plan: tuple[AgentPlanStep, ...]) -> RiskLevel:
        haystack = " ".join([task, *[str(step.args) for step in plan]]).casefold()
        if any(token in haystack for token in self.HIGH_TOKENS):
            return RiskLevel.HIGH
        if task_type in self.MEDIUM_TYPES:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW
