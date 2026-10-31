from __future__ import annotations

from datetime import datetime
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
from atnos.services.decision_log_service import DecisionLogService


class DecisionLogServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_extracts_commitment_and_due_hint_from_polish_conversation(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            with app.state.database.session_scope() as session:
                conversation = ConversationRepository(session).create(title="Projekt weekly")
                messages = MessageRepository(session)
                messages.create(
                    conversation_id=conversation.id,
                    role=MessageRole.USER,
                    status=MessageStatus.COMPLETED,
                    content="Potrzebujemy domknac review PR.",
                )
                messages.create(
                    conversation_id=conversation.id,
                    role=MessageRole.USER,
                    status=MessageStatus.COMPLETED,
                    content="Tak, zrobie raport do piatku.",
                )
                messages.create(
                    conversation_id=conversation.id,
                    role=MessageRole.ASSISTANT,
                    status=MessageStatus.COMPLETED,
                    content="Zapisuje to jako commitment.",
                )

                entries = DecisionLogService(session).extract_from_conversation(conversation.id)

                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0].kind.value, "commitment")
                self.assertIn("zrobie raport", entries[0].summary.lower())
                self.assertIsNotNone(entries[0].due_at)

    def test_extracts_english_due_hint(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            with app.state.database.session_scope() as session:
                conversation = ConversationRepository(session).create(title="Weekly sync")
                messages = MessageRepository(session)
                messages.create(
                    conversation_id=conversation.id,
                    role=MessageRole.USER,
                    status=MessageStatus.COMPLETED,
                    content="We need to close the release checklist.",
                )
                messages.create(
                    conversation_id=conversation.id,
                    role=MessageRole.USER,
                    status=MessageStatus.COMPLETED,
                    content="I will send the final notes by Friday.",
                )
                messages.create(
                    conversation_id=conversation.id,
                    role=MessageRole.ASSISTANT,
                    status=MessageStatus.COMPLETED,
                    content="I will keep that as a commitment.",
                )

                entries = DecisionLogService(session).extract_from_conversation(conversation.id)

                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0].kind.value, "commitment")
                self.assertIsNotNone(entries[0].due_at)

    def test_skips_extraction_for_conversations_shorter_than_three_messages(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            with app.state.database.session_scope() as session:
                conversation = ConversationRepository(session).create(title="Too short")
                messages = MessageRepository(session)
                messages.create(
                    conversation_id=conversation.id,
                    role=MessageRole.USER,
                    status=MessageStatus.COMPLETED,
                    content="Zrobie to jutro.",
                )
                messages.create(
                    conversation_id=conversation.id,
                    role=MessageRole.ASSISTANT,
                    status=MessageStatus.COMPLETED,
                    content="OK.",
                )

                entries = DecisionLogService(session).extract_from_conversation(conversation.id)

                self.assertEqual(entries, [])

    def test_detects_contradiction_and_resolves_entry(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            with app.state.database.session_scope() as session:
                conversation = ConversationRepository(session).create(title="Produkt")
                message_repo = MessageRepository(session)
                message_repo.create(
                    conversation_id=conversation.id,
                    role=MessageRole.USER,
                    status=MessageStatus.COMPLETED,
                    content="Zacznijmy od prostego modelu produktu.",
                )
                message = message_repo.create(
                    conversation_id=conversation.id,
                    role=MessageRole.USER,
                    status=MessageStatus.COMPLETED,
                    content="Ustalamy: produkt zostaje w modelu Chat i Ustawienia.",
                )
                message_repo.create(
                    conversation_id=conversation.id,
                    role=MessageRole.ASSISTANT,
                    status=MessageStatus.COMPLETED,
                    content="Zapisane jako decyzja architektoniczna.",
                )
                service = DecisionLogService(session)
                created = service.extract_from_conversation(conversation.id)
                self.assertEqual(len(created), 1)

                contradiction = service.detect_contradiction(
                    project_id=None,
                    user_message="Zrobmy z tego arena modeli.",
                )
                self.assertIsNotNone(contradiction)
                assert contradiction is not None
                self.assertIn("chat i ustawienia", contradiction.summary.lower())
                self.assertIn(str(message.id), contradiction.build_prefix())

                resolved = service.resolve(created[0].id, resolution_summary="Zamkniete.")
                self.assertEqual(resolved.status, DecisionLogStatus.RESOLVED)
                self.assertEqual(resolved.resolution_summary, "Zamkniete.")
                self.assertIsInstance(resolved.resolved_at, datetime)

    def test_extract_from_conversation_skips_llm_on_request_path_by_default(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            with app.state.database.session_scope() as session:
                conversation = ConversationRepository(session).create(title="Projekt weekly")
                messages = MessageRepository(session)
                messages.create(
                    conversation_id=conversation.id,
                    role=MessageRole.USER,
                    status=MessageStatus.COMPLETED,
                    content="Opisujemy scope raportu sprintu.",
                )
                messages.create(
                    conversation_id=conversation.id,
                    role=MessageRole.USER,
                    status=MessageStatus.COMPLETED,
                    content="Tak, zrobie raport w piatek.",
                )
                messages.create(
                    conversation_id=conversation.id,
                    role=MessageRole.ASSISTANT,
                    status=MessageStatus.COMPLETED,
                    content="Zapisuje to jako commitment.",
                )

                service = DecisionLogService(session)
                with patch.object(
                    DecisionLogService,
                    "_extract_with_llm",
                    side_effect=AssertionError("LLM extraction should stay out of the request path."),
                ):
                    entries = service.extract_from_conversation(conversation.id)

                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0].kind.value, "commitment")

    def test_detect_contradiction_skips_llm_on_request_path_by_default(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            with app.state.database.session_scope() as session:
                conversation = ConversationRepository(session).create(title="Produkt")
                message_repo = MessageRepository(session)
                message_repo.create(
                    conversation_id=conversation.id,
                    role=MessageRole.USER,
                    status=MessageStatus.COMPLETED,
                    content="Najpierw opiszmy scope produktu.",
                )
                message_repo.create(
                    conversation_id=conversation.id,
                    role=MessageRole.USER,
                    status=MessageStatus.COMPLETED,
                    content="Ustalamy: produkt zostaje w modelu Chat i Ustawienia.",
                )
                message_repo.create(
                    conversation_id=conversation.id,
                    role=MessageRole.ASSISTANT,
                    status=MessageStatus.COMPLETED,
                    content="Zapisane jako decyzja.",
                )
                service = DecisionLogService(session)
                created = service.extract_from_conversation(conversation.id)
                self.assertEqual(len(created), 1)

                with patch.object(
                    DecisionLogService,
                    "_detect_with_llm",
                    side_effect=AssertionError("LLM contradiction detection should stay out of the request path."),
                ):
                    contradiction = service.detect_contradiction(
                        project_id=None,
                        user_message="Zrobmy z tego arena modeli.",
                    )

                self.assertIsNotNone(contradiction)

    def test_recall_active_prioritizes_overdue_entries_over_null_due_dates(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            with app.state.database.session_scope() as session:
                project = ProjectRepository(session).create("Projekt recall")
                conversation = ConversationRepository(session).create(
                    title="Recall ordering",
                    project_id=project.id,
                )
                service = DecisionLogService(session)
                now = datetime.now().astimezone()
                for index in range(11):
                    service.decisions.create(
                        conversation_id=conversation.id,
                        project_id=project.id,
                        kind=DecisionLogKind.DECISION,
                        summary=f"Decyzja bez terminu {index}",
                        made_at=now,
                        status=DecisionLogStatus.ACTIVE,
                        due_at=None,
                        resolved_at=None,
                        resolution_summary=None,
                        evidence_message_ids=[],
                    )
                overdue = service.decisions.create(
                    conversation_id=conversation.id,
                    project_id=project.id,
                    kind=DecisionLogKind.COMMITMENT,
                    summary="Dowiezc review PR dzisiaj.",
                    made_at=now,
                    status=DecisionLogStatus.ACTIVE,
                    due_at=now,
                    resolved_at=None,
                    resolution_summary=None,
                    evidence_message_ids=[],
                )

                recall = service.recall_active(project.id)

                self.assertEqual(len(recall.entries), 10)
                self.assertEqual(recall.entries[0].id, overdue.id)
                self.assertEqual(len(recall.overdue_entries), 1)
                self.assertEqual(recall.overdue_entries[0].id, overdue.id)
