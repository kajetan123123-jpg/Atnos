from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.db.models.enums import MessageRole, MessageStatus
from atnos.providers.base import ProviderExecutionResult
from atnos.repositories.conversation_repository import ConversationRepository
from atnos.repositories.message_repository import MessageRepository
from atnos.services.chat_orchestration_service import ChatOrchestrationService

from test_support import bootstrap_test_app, create_test_app, make_temp_dir


class ContinueWithAnswerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_continue_with_answer_includes_selected_choice_in_provider_context(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        bootstrap_test_app(app)

        with app.state.database.session_scope() as session:
            conversations = ConversationRepository(session)
            messages = MessageRepository(session)
            conversation = conversations.create(title="Structured question flow")
            seed_user = messages.create(
                conversation_id=conversation.id,
                role=MessageRole.USER,
                status=MessageStatus.COMPLETED,
                content="Pomoz mi wybrac nastepny krok.",
            )
            question_message = messages.create(
                conversation_id=conversation.id,
                role=MessageRole.ASSISTANT,
                status=MessageStatus.COMPLETED,
                content="",
                parent_message_id=seed_user.id,
                metadata_json={
                    "kind": "question_choice",
                    "question_id": "scope-next-step",
                    "prompt": "Ktory obszar ruszamy teraz?",
                    "options": [
                        {"id": "a", "label": "Backend"},
                        {"id": "b", "label": "Frontend"},
                    ],
                    "multi_select": False,
                    "required": True,
                    "resolved_selected_ids": ["a"],
                },
            )
            answer_message = messages.create(
                conversation_id=conversation.id,
                role=MessageRole.USER,
                status=MessageStatus.COMPLETED,
                content="Wybralem: Backend",
                parent_message_id=question_message.id,
                metadata_json={
                    "kind": "choice_response",
                    "question_id": "scope-next-step",
                    "selected_ids": ["a"],
                },
            )
            session.flush()

            service = ChatOrchestrationService(
                session,
                settings=app.state.settings,
                provider_registry=app.state.providers,
                plugin_registry=app.state.plugins,
                summary_writeback_factory=app.state.summary_writeback_factory,
                ambient_context_service=getattr(app.state, "ambient_context_service", None),
                scheduler_runtime_service=getattr(app.state, "scheduler_runtime", None),
            )
            provider = app.state.providers.get("ollama-local")
            assert provider is not None
            captured_prompt: dict[str, str] = {}

            def _fake_execute(*, task, context, provider, model_config):
                captured_prompt["value"] = context.provider_prompt
                return ProviderExecutionResult(
                    content="Kontynuuje rozmowe po wybraniu Backendu.",
                )

            with patch.object(provider, "execute", side_effect=_fake_execute):
                next_message = service.continue_with_answer(
                    conversation_id=conversation.id,
                    answered_question_id="scope-next-step",
                    selected_ids=["a"],
                )

            self.assertEqual(next_message.role, MessageRole.ASSISTANT)
            self.assertEqual(next_message.parent_message_id, answer_message.id)
            self.assertIn("STRUCTURED_QUESTION_ANSWER:", captured_prompt["value"])
            self.assertIn("user_answer_labels=Backend", captured_prompt["value"])
            self.assertIn("Continue the conversation using this answer", captured_prompt["value"])
            persisted_messages = messages.list_for_conversation(conversation.id)
            self.assertEqual(persisted_messages[-1].id, next_message.id)
