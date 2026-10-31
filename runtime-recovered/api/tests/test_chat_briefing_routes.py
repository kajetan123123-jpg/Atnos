from __future__ import annotations

from datetime import UTC, datetime, timedelta
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


class ChatBriefingRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_chat_decisions_list_and_resolve_flow(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app) as client:
            with app.state.database.session_scope() as session:
                project = ProjectRepository(session).create("Projekt briefingu")
                conversation = ConversationRepository(session).create(
                    title="Decision flow",
                    project_id=project.id,
                )
                messages = MessageRepository(session)
                messages.create(
                    conversation_id=conversation.id,
                    role=MessageRole.USER,
                    status=MessageStatus.COMPLETED,
                    content="Najpierw domknijmy scope.",
                )
                messages.create(
                    conversation_id=conversation.id,
                    role=MessageRole.USER,
                    status=MessageStatus.COMPLETED,
                    content="Zrobie review tego PR do piatku.",
                )
                messages.create(
                    conversation_id=conversation.id,
                    role=MessageRole.ASSISTANT,
                    status=MessageStatus.COMPLETED,
                    content="Zapisane jako commitment.",
                )

            with app.state.database.session_scope() as session:
                from atnos.services.decision_log_service import DecisionLogService

                created = DecisionLogService(session).extract_from_conversation(conversation.id)
                self.assertEqual(len(created), 1)
                decision_id = created[0].id

            listed = client.get(f"/chat/decisions?project_id={project.id}")
            self.assertEqual(listed.status_code, 200)
            list_payload = listed.json()
            self.assertEqual(len(list_payload["entries"]), 1)
            self.assertEqual(list_payload["entries"][0]["summary"], "Zrobie review tego PR do piatku.")

            resolved = client.post(
                f"/chat/decisions/{decision_id}/resolve",
                json={"resolution_summary": "Review dowiezione."},
            )
            self.assertEqual(resolved.status_code, 200)
            resolved_payload = resolved.json()
            self.assertEqual(resolved_payload["status"], "resolved")
            self.assertEqual(resolved_payload["resolution_summary"], "Review dowiezione.")

    def test_chat_briefing_route_returns_structured_payload(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app) as client:
            with app.state.database.session_scope() as session:
                project = ProjectRepository(session).create("Projekt briefingu")
                conversation = ConversationRepository(session).create(
                    title="Briefing context",
                    project_id=project.id,
                )
                ConversationRepository(session).update_summary(
                    conversation,
                    "Do dowiezienia zostalo review i raport sprintu.",
                )
                project.summary = "Dzis trzeba domknac review i raport sprintu."
                from atnos.services.decision_log_service import DecisionLogService

                DecisionLogService(session).decisions.create(
                    conversation_id=conversation.id,
                    project_id=project.id,
                    kind=DecisionLogKind.COMMITMENT,
                    summary="Dowiezc raport sprintu.",
                    made_at=datetime.now(UTC),
                    status=DecisionLogStatus.ACTIVE,
                    due_at=datetime.now(UTC) + timedelta(hours=2),
                    resolved_at=None,
                    resolution_summary=None,
                    evidence_message_ids=[501],
                )

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
                response = client.post(f"/chat/briefing?project_id={project.id}")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["metadata_json"]["kind"], "briefing")
            self.assertIn("sections", payload["metadata_json"]["payload"])
