"""Persistence-boundary regressions: no model, user vault, or network access."""
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from atnos.db.models.enums import MessageRole
from atnos.services.chat_session_memory import persist_curated_chat_memory
from atnos.services.chat_tool_focus import memory_opt_out
from atnos.services.decision_log_service import DecisionLogService
from atnos.services.summary_writeback_service import SummaryWritebackService
from test_support import build_test_settings, make_temp_dir


@pytest.mark.parametrize('denial', ['Bez zapisywania do pamięci.', 'Wyłącznie odczyt.', 'Read-only.'])
def test_curated_boundary_uses_the_same_denial_as_browser(denial):
    with make_temp_dir() as temp:
        root = Path(temp)
        vault = root/'vault'
        vault.mkdir()
        settings = build_test_settings(root)
        object.__setattr__(settings, 'obsidian_vault_path', vault)
        text = 'Pamiętaj, że Atnos ma odpowiadać po polsku i konkretnie. ' + denial
        assert memory_opt_out(text)
        result = persist_curated_chat_memory(settings=settings, user_message=text,
                                            conversation_id=42, message_id=9)
        assert result is None
        assert not list(vault.rglob('*.md'))


@pytest.mark.parametrize('denial', ['Nie zapisuj.', 'Nie zapamiętuj.', "Don't remember this."])
def test_unqualified_explicit_denial_is_also_respected(denial):
    assert memory_opt_out('Przeczytaj publiczną stronę. ' + denial)


def message(number, role, text):
    return SimpleNamespace(id=number, role=role, content=text, created_at=datetime.now(UTC))


def decision_service(messages):
    service = DecisionLogService.__new__(DecisionLogService)
    service.conversations = Mock()
    service.conversations.get_any.return_value = SimpleNamespace(project_id=None)
    service.messages = Mock()
    service.messages.list_for_conversation.return_value = messages
    service.decisions = Mock()
    service.decisions.list_for_conversation.return_value = []
    return service


def test_readonly_turn_does_not_trigger_decision_writeback_of_history():
    service = decision_service([
        message(1, MessageRole.USER, 'Ustalamy, że wybieramy lokalną produkcję.'),
        message(2, MessageRole.ASSISTANT, 'Rozumiem.'),
        message(3, MessageRole.USER, 'Przeczytaj example.org. Niczego nie zapisuj.'),
        message(4, MessageRole.ASSISTANT, 'Odczytana strona jest przykładem.'),
    ])
    assert service.extract_from_conversation(1) == []
    service.decisions.create.assert_not_called()


def test_later_turn_cannot_reimport_previously_denied_decision():
    service = decision_service([
        message(1, MessageRole.USER, 'Ustalamy, że wybieramy lokalną produkcję. Niczego nie zapisuj.'),
        message(2, MessageRole.ASSISTANT, 'To nie jest trwała decyzja.'),
        message(3, MessageRole.USER, 'Dziękuję.'),
        message(4, MessageRole.ASSISTANT, 'Proszę.'),
    ])
    assert service.extract_from_conversation(1) == []
    service.decisions.create.assert_not_called()


def test_denied_turn_is_filtered_before_llm_and_window_truncation():
    denied = [message(1, MessageRole.USER, 'Ustalamy poufny wybór. Niczego nie zapisuj.')]
    denied += [message(i, MessageRole.ASSISTANT, 'Nieutrwalana odpowiedź.') for i in range(2, 26)]
    allowed = [message(26, MessageRole.USER, 'Wyjaśnij pojęcie kosztu.'),
               message(27, MessageRole.ASSISTANT, 'To definicja.'),
               message(28, MessageRole.USER, 'Dziękuję.')]
    service = decision_service(denied + allowed)
    service._extract_with_rules = Mock(return_value=[])
    service._extract_with_llm = Mock(return_value=[{
        'kind': SimpleNamespace(value='decision'), 'summary': 'Nie wolno przywrócić tej treści.',
        'evidence_message_ids': [1],
    }])
    assert service.extract_from_conversation(1, allow_llm=True) == []
    assert [m.id for m in service._extract_with_llm.call_args.args[0]] == [26, 27, 28]
    service.decisions.create.assert_not_called()


def test_real_decision_storage_never_reimports_denied_turn():
    from fastapi.testclient import TestClient
    from atnos.db.models.enums import MessageStatus
    from atnos.repositories.conversation_repository import ConversationRepository
    from atnos.repositories.message_repository import MessageRepository
    from test_support import create_test_app
    with make_temp_dir() as temp:
        app = create_test_app(Path(temp))
        try:
            with TestClient(app), app.state.database.session_scope() as session:
                conversation = ConversationRepository(session).create(title='Isolated write denial')
                messages = MessageRepository(session)
                def add(role, content):
                    messages.create(conversation_id=conversation.id, role=role,
                                    status=MessageStatus.COMPLETED, content=content)
                add(MessageRole.USER, 'Ustalamy poufny projekt FIKCYJNY-ZAKAZ. Niczego nie zapisuj.')
                add(MessageRole.ASSISTANT, 'Odczyt zakończony.')
                add(MessageRole.USER, 'Tylko odczyt historii.')
                service = DecisionLogService(session)
                assert service.extract_from_conversation(conversation.id) == []
                assert service.decisions.list_for_conversation(conversation.id) == []
                add(MessageRole.USER, 'Ustalamy publiczny projekt FIKCYJNY-DOZWOLONY.')
                add(MessageRole.ASSISTANT, 'Rozumiem.')
                add(MessageRole.USER, 'Dziękuję za wyjaśnienie.')
                entries = service.extract_from_conversation(conversation.id)
                assert len(entries) == 1 and 'FIKCYJNY-DOZWOLONY' in entries[0].summary
                session.commit()
                stored = service.decisions.list_for_conversation(conversation.id)
                assert len(stored) == 1 and 'FIKCYJNY-ZAKAZ' not in stored[0].summary
        finally:
            app.state.database.dispose()


def test_writeback_service_itself_stops_before_summary_or_project_writes():
    service = SummaryWritebackService(Mock())
    service.conversations = Mock()
    service.conversations.list_by_project.return_value = []
    service.project_memories = Mock()
    service.projects = Mock()
    service.projects.get.return_value = SimpleNamespace(id=7, summary='WB project')
    service.execution_traces = Mock()
    service.refresh_after_turn(
        conversation=SimpleNamespace(id=42, title='Memory denial', project_id=7, project=None, summary=None),
        user_message=SimpleNamespace(id=8, sequence_number=1,
            content='Pamiętaj, że wybieramy WB. Tylko odczyt; niczego nie zapisuj.'),
        assistant_message=SimpleNamespace(id=9, sequence_number=2, content='Odczyt zakończony.'), attachments=(),
        runtime_preferences=SimpleNamespace(memory_enabled=True, work_mode='auto',
            response_style=None, persona_profile=None, work_mode_source='default',
            response_style_source='default', persona_profile_source='default',
            memory_enabled_source='default'), project_context_applied=True)
    service.conversations.update_summary.assert_not_called()
    service.project_memories.upsert_conversation_summary.assert_not_called()
    service.projects.update_summary.assert_not_called()
    metadata = service.execution_traces.create.call_args.kwargs['metadata_json']
    assert metadata['memory_write_denied'] is True
