from __future__ import annotations

from agent.models import AgentTaskType, SkillRoute, TaskClassification


CANONICAL_TOOL_MAP: dict[str, str | None] = {
    "atnos:ubuntu-full-control": "ubuntu_check",
    "atnos:content-pipeline": "content_short",
    "atnos:channel-ops": "content_short",
    "atnos:youtube-automation": None,
    "atnos:browser-operator": "browser_open",
    "atnos:obsidian-memory": "memory_write",
}


class SkillRouter:
    def route(self, classification: TaskClassification) -> SkillRoute:
        canonical = self._route_from_canonical_reason(classification)
        if canonical is not None:
            return canonical

        mapping = {
            AgentTaskType.CHAT_GENERAL: SkillRoute("atnos:chat", None, "General chat stays in Chat."),
            AgentTaskType.CODING: SkillRoute("atnos:code", None, "Coding is handled by the existing coding workflow."),
            AgentTaskType.UBUNTU_CONTROL: SkillRoute("atnos:ubuntu-full-control", "ubuntu_check", "Use Ubuntu SSH sandbox."),
            AgentTaskType.BROWSER_CONTROL: SkillRoute("atnos:browser-operator", "browser_open", "Use browser operator foundation."),
            AgentTaskType.CONTENT_SHORT: SkillRoute("atnos:local-short-pipeline", "content_short", "Use local short pipeline."),
            AgentTaskType.FILE_ANALYSIS: SkillRoute("atnos:file-analysis", None, "File analysis stays in Chat capability layer."),
            AgentTaskType.MEMORY: SkillRoute("atnos:memory", "memory_write", "Write a memory note."),
            AgentTaskType.BRIEFING: SkillRoute("atnos:briefing", None, "Briefing is handled by existing briefing flow."),
            AgentTaskType.YOUTUBE: SkillRoute("atnos:youtube", None, "YouTube remains gated by publication approval."),
            AgentTaskType.RESEARCH: SkillRoute("atnos:research", None, "Research should use existing browser/research flow."),
            AgentTaskType.IMAGE_GENERATION: SkillRoute("atnos:image-generation", None, "Image generation stays in creative asset operator."),
            AgentTaskType.DOCUMENT_GENERATION: SkillRoute("atnos:document-generation", None, "Document generation stays in capability layer."),
            AgentTaskType.SETTINGS: SkillRoute("atnos:settings", None, "Settings changes stay in settings routes."),
            AgentTaskType.UNKNOWN: SkillRoute("atnos:triage", None, "Unknown task needs clarification or normal Chat."),
        }
        return mapping[classification.task_type]

    def _route_from_canonical_reason(self, classification: TaskClassification) -> SkillRoute | None:
        marker = "Canonical skill router selected "
        if marker not in classification.reason:
            return None
        skill_id = classification.reason.split(marker, maxsplit=1)[1].split(":", maxsplit=2)
        if len(skill_id) < 2:
            return None
        canonical_id = f"{skill_id[0]}:{skill_id[1]}"
        if canonical_id == "atnos:local-llm":
            return None
        tool_name = CANONICAL_TOOL_MAP.get(canonical_id)
        return SkillRoute(canonical_id, tool_name, "Routed by canonical Atnos skill registry.")
