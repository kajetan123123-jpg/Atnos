"""Ingest must survive cancellation before the first model token."""
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).parent))
import pytest
from test_support import create_test_app, bootstrap_test_app
from atnos.services.chat_orchestration_service import ChatOrchestrationService
from atnos.services.conversation_service import ConversationTurnRequest
from atnos.repositories.message_repository import MessageRepository


def test_stream_commits_user_turn_and_acknowledges_identity_before_provider(tmp_path):
    app = create_test_app(tmp_path)
    bootstrap_test_app(app)
    events=[]
    def cancelled(*args, **kwargs):
        ack = next((e for e in events if e['type']=='conversation'), None)
        assert ack is not None, 'Client needs conversation identity even when generation is cancelled'
        with app.state.database.session_scope() as independent:
            messages = MessageRepository(independent).list_for_conversation(ack['conversation_id'])
            assert any(m.id==ack['user_message_id'] and m.content=='AUDIT CANCEL INGEST' for m in messages)
        raise RuntimeError('controlled model cancellation')
    try:
        with app.state.database.session_scope() as session:
            service=ChatOrchestrationService(session, settings=app.state.settings, provider_registry=app.state.providers, plugin_registry=app.state.plugins)
            with patch.object(service,'_complete_turn',side_effect=cancelled):
                with pytest.raises(RuntimeError,match='controlled model cancellation'):
                    service.handle_turn(ConversationTurnRequest(content='AUDIT CANCEL INGEST'),stream_event=events.append)
    finally:
        app.state.database.dispose()
