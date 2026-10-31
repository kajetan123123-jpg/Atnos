from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import create_test_app, make_temp_dir

from atnos.db.models.enums import DecisionLogKind, DecisionLogStatus, MessageRole, MessageStatus
from atnos.repositories.conversation_repository import ConversationRepository
from atnos.repositories.message_repository import MessageRepository
from atnos.repositories.project_repository import ProjectRepository
from atnos.services.briefing_service import BriefingService
from atnos.services.linear_connector_service import LinearSprintSummary, LinearTaskItem


class BriefingServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def _create_project_memory_context(self, session):
        project = ProjectRepository(session).create("Projekt briefing")
        project.summary = "Dzis trzeba domknac review i roadmape."
        conversation = ConversationRepository(session).create(
            title="Sprint follow-up",
            project_id=project.id,
        )
        MessageRepository(session).create(
            conversation_id=conversation.id,
            role=MessageRole.USER,
            status=MessageStatus.COMPLETED,
            content="Zrobie review tego PR do piatku.",
        )
        MessageRepository(session).create(
            conversation_id=conversation.id,
            role=MessageRole.USER,
            status=MessageStatus.COMPLETED,
            content="Trzeba jeszcze zamknac raport sprintu.",
        )
        MessageRepository(session).create(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            status=MessageStatus.COMPLETED,
            content="Zapisuje commitment i follow-up.",
        )
        ConversationRepository(session).update_summary(
            conversation,
            "Zostalo review PR i raport sprintu.",
        )
        return project, conversation

    def test_generate_without_connectors_returns_decisions_and_project_memory(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            with app.state.database.session_scope() as session:
                project, conversation = self._create_project_memory_context(session)
                service = BriefingService(session, settings=app.state.settings)
                service.decisions.extract_from_conversation(conversation.id)

                with (
                    patch(
                        "atnos.services.github_connector_service.GitHubConnectorService.is_connected",
                        return_value=False,
                    ),
                    patch(
                        "atnos.services.linear_connector_service.LinearConnectorService.is_connected",
                        return_value=False,
                    ),
                ):
                    payload = service.generate(project_id=project.id)

                section_kinds = [section.kind for section in payload.sections]
                self.assertEqual(section_kinds, ["decisions", "project_memory"])

    def test_generate_with_github_connection_populates_requested_reviews(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            with app.state.database.session_scope() as session:
                project, conversation = self._create_project_memory_context(session)
                service = BriefingService(session, settings=app.state.settings)
                service.decisions.extract_from_conversation(conversation.id)

                with (
                    patch(
                        "atnos.services.github_connector_service.GitHubConnectorService.is_connected",
                        return_value=True,
                    ),
                    patch(
                        "atnos.services.github_connector_service.GitHubConnectorService.list_open_prs",
                        return_value={
                            "status": "ready",
                            "items": [
                                {
                                    "repository_full_name": "atnos/core",
                                    "title": "Domknac fallback briefingu",
                                    "html_url": "https://github.com/atnos/core/pull/12",
                                }
                            ],
                        },
                    ),
                    patch(
                        "atnos.services.github_connector_service.GitHubConnectorService.list_mentions",
                        return_value={"status": "ready", "items": []},
                    ),
                    patch(
                        "atnos.services.linear_connector_service.LinearConnectorService.is_connected",
                        return_value=False,
                    ),
                ):
                    payload = service.generate(project_id=project.id, sections=["github", "decisions"])

                github_section = next(section for section in payload.sections if section.kind == "github")
                self.assertEqual(github_section.items[0].urgency, "high")
                self.assertEqual(github_section.items[0].action_url, "https://github.com/atnos/core/pull/12")
                self.assertIn("review", github_section.summary)

    def test_generate_with_linear_connection_prioritizes_overdue_tasks(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            with app.state.database.session_scope() as session:
                project, _ = self._create_project_memory_context(session)
                service = BriefingService(session, settings=app.state.settings)
                overdue_task = LinearTaskItem(
                    task_id="lin-1",
                    identifier="ENG-12",
                    title="Naprawic blocker review",
                    state="In Progress",
                    state_type="started",
                    due_date=date.today() - timedelta(days=1),
                    priority=1,
                    url="https://linear.app/atnos/issue/ENG-12",
                    team_name="Core",
                    cycle_id="cycle-1",
                    cycle_name="Sprint 12",
                )
                today_task = LinearTaskItem(
                    task_id="lin-2",
                    identifier="ENG-13",
                    title="Domknac opis briefingu",
                    state="Todo",
                    state_type="unstarted",
                    due_date=date.today(),
                    priority=2,
                    url="https://linear.app/atnos/issue/ENG-13",
                    team_name="Core",
                    cycle_id="cycle-1",
                    cycle_name="Sprint 12",
                )

                with (
                    patch(
                        "atnos.services.github_connector_service.GitHubConnectorService.is_connected",
                        return_value=False,
                    ),
                    patch(
                        "atnos.services.linear_connector_service.LinearConnectorService.is_connected",
                        return_value=True,
                    ),
                    patch(
                        "atnos.services.linear_connector_service.LinearConnectorService.list_overdue_tasks",
                        return_value=[overdue_task],
                    ),
                    patch(
                        "atnos.services.linear_connector_service.LinearConnectorService.list_today_due",
                        return_value=[today_task],
                    ),
                    patch(
                        "atnos.services.linear_connector_service.LinearConnectorService.get_active_sprint_summary",
                        return_value=LinearSprintSummary(
                            cycle_id="cycle-1",
                            cycle_name="Sprint 12",
                            total_tasks=6,
                            completed_tasks=3,
                            open_tasks=3,
                        ),
                    ),
                ):
                    payload = service.generate(project_id=project.id, sections="linear")

                linear_section = payload.sections[0]
                self.assertEqual(linear_section.kind, "linear")
                self.assertEqual(linear_section.items[0].urgency, "high")
                self.assertIn("zaleglych taskow", linear_section.summary)
