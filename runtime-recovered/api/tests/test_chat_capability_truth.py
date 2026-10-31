import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import pytest
from atnos.services.chat_tool_focus import natural_tool_focus, direct_read_request
from atnos.tools.chat_agent_tools import SelfStateTool, build_chat_agent_registry
from test_support import build_test_settings
from test_chat_soak_regressions import ChatSoakPersistenceTest as _PersistenceFixture

@pytest.mark.parametrize('text', ['Co potrafisz?', 'Jakie masz możliwości?', 'Jakie masz narzędzia?',
    'Pokaż swoje możliwości.', 'Czy masz dostęp do internetu?', 'Czy masz dostęp do plików?'])
def test_capabilities_are_a_real_read_without_starting_work(text):
    focus = natural_tool_focus(text)
    assert focus and focus.purpose == 'capability_status'
    assert direct_read_request(text, focus) == ('self_state', {})

@pytest.mark.parametrize('text', ['„Co potrafisz?”', 'Wyjaśnij jak działają narzędzia.',
    'Co potrafisz? Następnie uruchom program.', 'Przeczytaj plik E:/Atnos/readme.md.',
    'Jakie masz narzędzia? Napisz mi plan.', 'Co potrafisz i przeczytaj E:/Atnos/test.md.'])
def test_other_requests_do_not_become_a_capabilities_only_answer(text):
    focus = natural_tool_focus(text)
    assert not focus or focus.purpose != 'capability_status'

def test_self_state_respects_actual_conversation_flags_and_does_not_upgrade_registered_accounts(tmp_path):
    settings = build_test_settings(tmp_path).model_copy(update={'cloud_llm_allowed': True})
    snapshot = {'browser_operator_available': True, 'account_backed_external_llms': ['Claude']}
    with patch('atnos.tools.chat_agent_tools.ObsidianReadService') as obsidian:
        obsidian.return_value.get_vault_status.return_value = SimpleNamespace(
            vault_name='Controlled vault', path_configured=True, path_exists=True, readable=True,
            note_count=1, warnings=())
        registry = build_chat_agent_registry(settings=settings, session=Mock(), provider_registry=Mock(), conversation_id=71,
            capability_snapshot=snapshot, include_inventory=False, files_enabled=False,
            memory_enabled=False, internet_enabled=False, local_file_write_enabled=False, memory_write_enabled=False)
        state = asyncio.run(registry.get('self_state').execute())
        obsidian.assert_not_called()
    assert state['can_do']['local_files_without_chat_attachment'] is False
    assert state['can_do']['realtime_internet'] is False
    assert state['can_do']['external_llms_claude_openai_gemini'] is False
    assert state['can_do']['memory_read'] is False
    assert state['can_do']['memory_write'] is False
    assert state['can_do']['external_llm_labels'] == []
    assert state['registered_external_llms'] == ['Claude']
    assert 'self_state' in state['registered_tools']
    assert 'fs_read' not in state['registered_tools']
    assert 'obsidian_read' not in state['registered_tools']

def test_registered_browser_account_is_unknown_not_confirmed_or_denied(tmp_path):
    settings = build_test_settings(tmp_path).model_copy(update={'cloud_llm_allowed': True})
    tool = SelfStateTool(settings=settings, session=Mock(), conversation_id=72,
        capability_snapshot={'account_backed_external_llms': ['Claude']},
        session_access={'internet_enabled': True, 'memory_enabled': False, 'files_enabled': False})
    state = asyncio.run(tool.execute())
    assert state['can_do']['external_llms_claude_openai_gemini'] is None
    assert state['can_do']['external_llm_labels'] == []
    assert state['registered_external_llms'] == ['Claude']


class CapabilityQuestionApiTest(unittest.TestCase):
    setUp = _PersistenceFixture.setUp

    def test_natural_capability_question_uses_one_real_tool_and_no_model(self):
        from fastapi.testclient import TestClient
        with patch('atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend',
                   side_effect=AssertionError('Capability discovery must not generate a model answer')), TestClient(self.app) as client:
            response = client.post('/chat', json={'content': 'Jakie masz możliwości?',
                'execution_preferences': {'file_enabled': False, 'memory_enabled': False,
                    'internet_enabled': False, 'active_work_mode': 'quick'}})
        assert response.status_code == 200
        data = response.json()
        trace = data['assistant_message_metadata_json']['agentic_tool_loop']
        assert trace['turns_used'] == 0
        assert trace['tools_used'] == ['self_state']
        assert trace['status'] == 'completed'
        assert 'pliki lokalne: wyłączone w rozmowie' in data['assistant_message']
        assert 'Obsidian: wyłączony w tej rozmowie' in data['assistant_message']
