from __future__ import annotations

import re

from agent.models import AgentTaskType, TaskClassification
from atnos.skills.router import route_task


CANONICAL_TASK_MAP: dict[str, AgentTaskType] = {
    "atnos:ubuntu-full-control": AgentTaskType.UBUNTU_CONTROL,
    "atnos:content-pipeline": AgentTaskType.CONTENT_SHORT,
    "atnos:channel-ops": AgentTaskType.CONTENT_SHORT,
    "atnos:youtube-automation": AgentTaskType.YOUTUBE,
    "atnos:browser-operator": AgentTaskType.BROWSER_CONTROL,
    "atnos:desktop-operator": AgentTaskType.BROWSER_CONTROL,
    "atnos:deep-research": AgentTaskType.RESEARCH,
    "atnos:image-generation": AgentTaskType.IMAGE_GENERATION,
    "atnos:codex-repo-work": AgentTaskType.CODING,
    "atnos:obsidian-memory": AgentTaskType.MEMORY,
    "anthropic-skills:consolidate-memory": AgentTaskType.MEMORY,
    "anthropic-skills:pdf": AgentTaskType.FILE_ANALYSIS,
    "anthropic-skills:xlsx": AgentTaskType.FILE_ANALYSIS,
    "anthropic-skills:docx": AgentTaskType.DOCUMENT_GENERATION,
    "anthropic-skills:pptx": AgentTaskType.DOCUMENT_GENERATION,
    "anthropic-skills:algorithmic-art": AgentTaskType.IMAGE_GENERATION,
    "anthropic-skills:canvas-design": AgentTaskType.IMAGE_GENERATION,
}


class TaskClassifier:
    def classify(self, task: str) -> TaskClassification:
        text = self._normalize(task)
        if not text:
            return TaskClassification(AgentTaskType.UNKNOWN, 0.0, "Empty task.")

        canonical = self._classify_from_canonical_route(task)
        if canonical is not None:
            return canonical

        rules: tuple[tuple[AgentTaskType, tuple[str, ...], str], ...] = (
            (AgentTaskType.UBUNTU_CONTROL, ("ubuntu", "laptop", "ssh", "sudo", "198.51.100.216"), "Ubuntu sandbox control."),
            (AgentTaskType.CONTENT_SHORT, ("short", "shorta", "reel", "rolka", "wideo", "video", "mp4"), "Short-form local video task."),
            (AgentTaskType.BROWSER_CONTROL, ("przegladarka", "browser", "otworz strone", "playwright", "chatgpt.com"), "Browser operator task."),
            (AgentTaskType.BRIEFING, ("briefing", "podsumowanie dnia", "commitments", "decyzje"), "Briefing or decision-log task."),
            (AgentTaskType.IMAGE_GENERATION, ("grafika", "obraz", "wygeneruj obraz", "ilustracja", "png"), "Image generation task."),
            (AgentTaskType.DOCUMENT_GENERATION, ("docx", "word", "pptx", "powerpoint", "prezentacja", "dokument"), "Document generation task."),
            (AgentTaskType.CODING, ("kod", "test", "pytest", "refactor", "bug", "repo", "commit"), "Coding task."),
            (AgentTaskType.FILE_ANALYSIS, ("plik", "pdf", "csv", "xlsx", "analizuj dokument", "przeczytaj"), "File analysis task."),
            (AgentTaskType.MEMORY, ("zapisz w pamieci", "zapamietaj", "obsidian", "notatka"), "Memory task."),
            (AgentTaskType.YOUTUBE, ("youtube", "upload", "opublikuj", "miniatura", "analytics"), "YouTube task."),
            (AgentTaskType.RESEARCH, ("research", "sprawdz w internecie", "aktualne", "zrodla", "linki"), "Research task."),
            (AgentTaskType.SETTINGS, ("ustawienia", "settings", "model", "provider", "persona"), "Settings task."),
        )
        for task_type, tokens, reason in rules:
            if any(token in text for token in tokens):
                return TaskClassification(task_type, 0.86, reason)
        if len(re.findall(r"\w+", text)) <= 12:
            return TaskClassification(AgentTaskType.CHAT_GENERAL, 0.7, "Short general chat task.")
        return TaskClassification(AgentTaskType.UNKNOWN, 0.35, "No deterministic rule matched.")

    def _classify_from_canonical_route(self, task: str) -> TaskClassification | None:
        try:
            decision = route_task(task)
        except Exception:
            return None
        for skill_id in decision.selected_skills:
            if skill_id == "atnos:local-llm":
                continue
            task_type = CANONICAL_TASK_MAP.get(skill_id)
            if task_type is not None:
                return TaskClassification(
                    task_type,
                    max(0.7, float(decision.confidence)),
                    f"Canonical skill router selected {skill_id}: {decision.reasoning_summary}",
                )
        return None

    @staticmethod
    def _normalize(value: str) -> str:
        replacements = {
            "ą": "a",
            "ć": "c",
            "ę": "e",
            "ł": "l",
            "ń": "n",
            "ó": "o",
            "ś": "s",
            "ź": "z",
            "ż": "z",
        }
        text = value.casefold()
        for source, target in replacements.items():
            text = text.replace(source, target)
        return " ".join(text.split())
