from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.db.models.enums import MessageRole, MessageStatus
from atnos.providers.base import ProviderExecutionResult
from atnos.repositories.conversation_repository import ConversationRepository
from atnos.repositories.message_repository import MessageRepository

from test_support import bootstrap_test_app, create_test_app, make_temp_dir


class AnswerQuestionEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def _build_question_fixture(
        self,
        app,
        *,
        multi_select: bool = False,
        required: bool = True,
        resolved_selected_ids: list[str] | None = None,
    ) -> tuple[int, int]:
        with app.state.database.session_scope() as session:
            conversations = ConversationRepository(session)
            messages = MessageRepository(session)
            conversation = conversations.create(title="Question endpoint flow")
            seed_user = messages.create(
                conversation_id=conversation.id,
                role=MessageRole.USER,
                status=MessageStatus.COMPLETED,
                content="Potrzebuje pomocy z wyborem.",
            )
            assistant = messages.create(
                conversation_id=conversation.id,
                role=MessageRole.ASSISTANT,
                status=MessageStatus.COMPLETED,
                content="",
                parent_message_id=seed_user.id,
                metadata_json={
                    "kind": "question_choice",
                    "question_id": "next-step",
                    "prompt": "Co robimy dalej?",
                    "options": [
                        {"id": "a", "label": "Backend"},
                        {"id": "b", "label": "Frontend"},
                        {"id": "c", "label": "Design"},
                    ],
                    "multi_select": multi_select,
                    "required": required,
                    **(
                        {"resolved_selected_ids": resolved_selected_ids}
                        if resolved_selected_ids is not None
                        else {}
                    ),
                },
            )
            session.flush()
            return conversation.id, assistant.id

    def test_happy_path_creates_user_choice_and_next_assistant_message(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        bootstrap_test_app(app)
        conversation_id, assistant_message_id = self._build_question_fixture(app)
        provider = app.state.providers.get("ollama-local")
        assert provider is not None

        def _fake_execute(*, task, context, provider, model_config):
            return ProviderExecutionResult(content="Kontynuacja po wyborze Backend.")

        with TestClient(app) as client, patch.object(provider, "execute", side_effect=_fake_execute):
            response = client.post(
                "/agent/answer-question",
                json={
                    "conversation_id": conversation_id,
                    "assistant_message_id": assistant_message_id,
                    "question_id": "next-step",
                    "selected_ids": ["a"],
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertIsNotNone(payload["next_assistant_message_id"])

        with app.state.database.session_scope() as session:
            messages = MessageRepository(session).list_for_conversation(conversation_id)
        self.assertEqual(messages[-2].metadata_json["kind"], "choice_response")
        self.assertEqual(messages[-2].content, "Wybralem: Backend")
        self.assertEqual(messages[-1].role, MessageRole.ASSISTANT)
        self.assertEqual(messages[-1].parent_message_id, messages[-2].id)
        self.assertEqual(messages[1].metadata_json["resolved_selected_ids"], ["a"])

    def test_wrong_question_id_returns_404(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        bootstrap_test_app(app)
        conversation_id, assistant_message_id = self._build_question_fixture(app)

        with TestClient(app) as client:
            response = client.post(
                "/agent/answer-question",
                json={
                    "conversation_id": conversation_id,
                    "assistant_message_id": assistant_message_id,
                    "question_id": "wrong-id",
                    "selected_ids": ["a"],
                },
            )

        self.assertEqual(response.status_code, 404)

    def test_selected_id_not_in_options_returns_422(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        bootstrap_test_app(app)
        conversation_id, assistant_message_id = self._build_question_fixture(app)

        with TestClient(app) as client:
            response = client.post(
                "/agent/answer-question",
                json={
                    "conversation_id": conversation_id,
                    "assistant_message_id": assistant_message_id,
                    "question_id": "next-step",
                    "selected_ids": ["z"],
                },
            )

        self.assertEqual(response.status_code, 422)

    def test_single_select_rejects_multiple_answers(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        bootstrap_test_app(app)
        conversation_id, assistant_message_id = self._build_question_fixture(app, multi_select=False)

        with TestClient(app) as client:
            response = client.post(
                "/agent/answer-question",
                json={
                    "conversation_id": conversation_id,
                    "assistant_message_id": assistant_message_id,
                    "question_id": "next-step",
                    "selected_ids": ["a", "b"],
                },
            )

        self.assertEqual(response.status_code, 422)

    def test_already_answered_question_returns_409(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        bootstrap_test_app(app)
        conversation_id, assistant_message_id = self._build_question_fixture(
            app,
            resolved_selected_ids=["b"],
        )

        with TestClient(app) as client:
            response = client.post(
                "/agent/answer-question",
                json={
                    "conversation_id": conversation_id,
                    "assistant_message_id": assistant_message_id,
                    "question_id": "next-step",
                    "selected_ids": ["a"],
                },
            )

        self.assertEqual(response.status_code, 409)

    def test_nonexistent_conversation_returns_404(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        bootstrap_test_app(app)
        _, assistant_message_id = self._build_question_fixture(app)

        with TestClient(app) as client:
            response = client.post(
                "/agent/answer-question",
                json={
                    "conversation_id": 999999,
                    "assistant_message_id": assistant_message_id,
                    "question_id": "next-step",
                    "selected_ids": ["a"],
                },
            )

        self.assertEqual(response.status_code, 404)
