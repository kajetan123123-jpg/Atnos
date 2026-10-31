from __future__ import annotations

from agent.models import AgentTaskType
from agent.task_classifier import TaskClassifier


def test_agent_orchestrator_classifier_routes_core_tasks() -> None:
    classifier = TaskClassifier()

    assert classifier.classify("Sprawdz stan laptopa Ubuntu i zrob raport").task_type == AgentTaskType.UBUNTU_CONTROL
    assert classifier.classify("Zrob mi shorta po polsku o lokalnym AI").task_type == AgentTaskType.CONTENT_SHORT
    assert classifier.classify("Otworz strone i zrob screenshot").task_type == AgentTaskType.BROWSER_CONTROL
    assert classifier.classify("Zapamietaj ten fakt w Obsidianie").task_type == AgentTaskType.MEMORY
