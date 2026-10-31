from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from atnos.db.models.enums import MessageRole, MessageStatus
from atnos.providers.base import ProviderExecutionResult
from atnos.repositories.conversation_repository import ConversationRepository
from atnos.repositories.message_repository import MessageRepository
from tests.test_support import bootstrap_test_app, create_test_app, make_temp_dir


def test_abcd_choice_flow_persists_selection_and_continues() -> None:
    temp_dir = make_temp_dir()
    try:
        app = create_test_app(Path(temp_dir.name))
        bootstrap_test_app(app)
        try:
            conversation_id, assistant_message_id = _create_question_message(app)
            provider = app.state.providers.get("ollama-local")
            assert provider is not None

            with TestClient(app) as client, patch.object(
                provider,
                "execute",
                return_value=ProviderExecutionResult(content="Kontynuuję wariant B: storytelling."),
            ):
                response = client.post(
                    "/agent/answer-question",
                    json={
                        "conversation_id": conversation_id,
                        "assistant_message_id": assistant_message_id,
                        "question_id": "smoke-abcd",
                        "selected_ids": ["b"],
                    },
                )

            assert response.status_code == 200
            payload = response.json()
            assert payload["ok"] is True
            assert payload["user_message_id"] > 0
            assert payload["next_assistant_message_id"] is not None

            with app.state.database.session_scope() as session:
                messages = MessageRepository(session).list_for_conversation(conversation_id)

            assert messages[-2].metadata_json["kind"] == "choice_response"
            assert messages[-2].metadata_json["selected_ids"] == ["b"]
            assert "Storytelling" in messages[-2].content
            assert messages[-1].role == MessageRole.ASSISTANT
            assert "wariant B" in messages[-1].content
            assert messages[1].metadata_json["resolved_selected_ids"] == ["b"]
        finally:
            app.state.database.dispose()
    finally:
        temp_dir.cleanup()


def _create_question_message(app) -> tuple[int, int]:
    with app.state.database.session_scope() as session:
        conversations = ConversationRepository(session)
        messages = MessageRepository(session)
        conversation = conversations.create(title="Smoke ABCD flow")
        user_message = messages.create(
            conversation_id=conversation.id,
            role=MessageRole.USER,
            status=MessageStatus.COMPLETED,
            content="Jaki styl shorta wybrać?",
        )
        assistant = messages.create(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            status=MessageStatus.COMPLETED,
            content="Wybierz styl.",
            parent_message_id=user_message.id,
            metadata_json={
                "kind": "question_choice",
                "question_id": "smoke-abcd",
                "prompt": "Jaki styl wybierasz?",
                "options": [
                    {"id": "a", "label": "Edukacyjny"},
                    {"id": "b", "label": "Storytelling"},
                    {"id": "c", "label": "Newsowy"},
                    {"id": "d", "label": "Kontrowersyjny"},
                ],
                "multi_select": False,
                "required": True,
            },
        )
        session.flush()
        return conversation.id, assistant.id
