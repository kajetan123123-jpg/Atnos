"""Natural note requests and real long-file reads, without model inference."""
import asyncio
from pathlib import Path
import unittest
from unittest.mock import patch

import pytest

from atnos.services.chat_tool_focus import natural_tool_focus, requests_note_comparison
from atnos.services.obsidian_read_service import ObsidianReadService
from atnos.tools.chat_agent_tools import ObsidianReadTool, MAX_READ_CHARS
from test_support import make_temp_dir
from test_chat_soak_regressions import ChatSoakPersistenceTest as _PersistenceFixture


@pytest.mark.parametrize('content', [
    'Co jest w notatce CHANNEL STATUS?',
    'Co zawiera moja notatka CHANNEL STATUS?',
    'Jaka jest treść zapisanej notatki CHANNEL STATUS?',
    'Czy możesz przeczytać notatkę CHANNEL STATUS?',
    'Odczytaj mi notatkę CHANNEL STATUS.',
    'Znajdź notatkę CHANNEL STATUS i pokaż jej treść.',
    'Przeczytaj notatkę „Zapisz pomysł”.',
])
def test_natural_note_request_uses_read_tools_without_product_name(content):
    focus = natural_tool_focus(content)
    assert focus is not None and focus.purpose == 'memory_read'
    assert focus.required == ('obsidian_search', 'obsidian_read')
    assert 'obsidian_write' not in focus.tools


@pytest.mark.parametrize('content', [
    'Co zawiera notatka prasowa?', 'Jak napisać notatkę?',
    'Wyjaśnij pojęcie notatki.', 'Nie czytaj notatki CHANNEL STATUS.',
    '„Co jest w notatce CHANNEL STATUS?”',
    'Przeczytaj notatkę CHANNEL STATUS, potem zaktualizuj jej treść.',
])
def test_general_or_quoted_question_does_not_dispatch_a_note_read(content):
    assert natural_tool_focus(content) is None


@pytest.mark.parametrize('text, expected', [
    ('Przeczytaj notatkę „Konflikt”.', False),
    ('Przeczytaj notatkę „Porównanie”.', False),
    ('Przeczytaj notatkę TEST. Nie porównuj źródeł.', False),
    ('Przeczytaj notatkę TEST. Pokaż różnice.', True),
    ('Przeczytaj notatkę TEST. Pokaż źródła i ich sprzeczności.', True),
])
def test_comparison_is_requested_outside_the_literal_title(text, expected):
    assert requests_note_comparison(text) is expected


def read_real_note(body):
    with make_temp_dir() as temp:
        vault = Path(temp)
        (vault/'note.md').write_text('# Note\n\n' + body, encoding='utf-8')
        service = ObsidianReadService.__new__(ObsidianReadService)
        service._resolve_vault_root = lambda **kwargs: vault
        with patch('atnos.tools.chat_agent_tools.ObsidianReadService', return_value=service):
            return asyncio.run(ObsidianReadTool(settings=None, session=None).execute(relative_path='note.md'))


def test_chat_note_read_does_not_silently_use_the_2400_character_ui_preview():
    body = 'A complete sentence.\n' * 220 + 'END-OF-REAL-NOTE-729'
    result = read_real_note(body)
    assert 'END-OF-REAL-NOTE-729' in result['markdown']
    assert result['truncated'] is False and result['status'] == 'ok'


def test_note_above_context_budget_is_explicitly_incomplete():
    result = read_real_note('A complete sentence.\n' * 1000 + 'NOT-IN-FIRST-PAGE')
    assert result['truncated'] is True
    assert result['status'] == 'incomplete'
    assert result['total_chars'] > MAX_READ_CHARS
    assert result['returned_chars'] == len(result['markdown'])
    assert 'NOT-IN-FIRST-PAGE' not in result['markdown']


class NaturalNoteReadApiTest(unittest.TestCase):
    setUp = _PersistenceFixture.setUp

    def test_failed_comparison_read_keeps_the_successful_source_and_reports_unread_source(self):
        from fastapi.testclient import TestClient
        from atnos.tools.chat_agent_tools import ObsidianWriteTool
        writer = ObsidianWriteTool(settings=self.settings)
        for kind in ('learning', 'decision'):
            asyncio.run(writer.execute(title='DENIED-SOURCE-729', body=f'{kind} source content.', note_type=kind))
        original = ObsidianReadTool.execute
        calls = []
        async def read(tool, **kwargs):
            calls.append(kwargs['relative_path'])
            if '/decision/' in kwargs['relative_path']:
                raise PermissionError('Controlled offline denied read')
            return await original(tool, **kwargs)
        with patch.object(ObsidianReadTool, 'execute', new=read), patch(
                'atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend',
                side_effect=AssertionError('No model needed')), TestClient(self.app) as client:
            response = client.post('/chat', json={
                'content': 'Przeczytaj notatkę „DENIED-SOURCE-729”. Porównaj źródła. Niczego nie zapisuj.',
                'execution_preferences': {'memory_enabled': True, 'file_enabled': True,
                    'internet_enabled': False, 'active_work_mode': 'quick'}})
        assert response.status_code == 200
        data = response.json()
        trace = data['assistant_message_metadata_json']['agentic_tool_loop']
        assert trace['status'] == 'source_conflict'
        assert len(calls) == 2 and len(set(calls)) == 2
        assert 'learning source content.' in data['assistant_message']
        assert 'Porównanie częściowe' in data['assistant_message']
        assert 'Źródła nieodczytane' in data['assistant_message']
        assert any(item['status'] == 'error' for item in trace['tool_calls'])

    def test_comparison_does_not_equate_same_content_or_partial_reads_with_a_conflict_resolution(self):
        from fastapi.testclient import TestClient
        from atnos.tools.chat_agent_tools import ObsidianWriteTool
        writer = ObsidianWriteTool(settings=self.settings)
        cases = [('SAME-SOURCES-729', ['Same body.', 'Same body.'], 'treści są identyczne'),
                 ('LONG-SOURCES-729', ['Line of a long note.\n' * 1000, 'Short note.'], 'Porównanie częściowe'),
                 ('MANY-SOURCES-729', ['Learning body.', 'Decision body.', 'Memory body.'], 'Źródła nieodczytane')]
        for title, bodies, _ in cases:
            for kind, body in zip(('learning', 'decision', 'memory'), bodies):
                asyncio.run(writer.execute(title=title, body=body, note_type=kind))
        with patch('atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend',
                   side_effect=AssertionError('No model needed')), TestClient(self.app) as client:
            for title, _, expected in cases:
                with self.subTest(title=title):
                    response = client.post('/chat', json={
                        'content': f'Przeczytaj notatkę „{title}”. Pokaż różnice. Niczego nie zapisuj.',
                        'execution_preferences': {'memory_enabled': True, 'file_enabled': True,
                            'internet_enabled': False, 'active_work_mode': 'quick'}})
                    assert response.status_code == 200
                    data = response.json()
                    trace = data['assistant_message_metadata_json']['agentic_tool_loop']
                    assert trace['status'] == 'source_conflict'
                    assert trace['tools_used'].count('obsidian_read') == 2
                    assert len(trace['tool_calls']) <= trace['settings']['max_turns']
                    assert expected in data['assistant_message']
                    if title.startswith('SAME'):
                        assert 'Odczytane treści różnią się' not in data['assistant_message']

    def test_explicit_conflict_request_reads_both_sources_without_model_or_writes(self):
        from fastapi.testclient import TestClient
        from atnos.tools.chat_agent_tools import ObsidianWriteTool
        writer = ObsidianWriteTool(settings=self.settings)
        for kind, body in [('learning', 'Fikcyjny test. Etap: kontrola kompozycji.'),
                           ('decision', 'Historyczna decyzja. Etap: storyboard.')]:
            asyncio.run(writer.execute(title='COMPARE-SOURCE-729', body=body, note_type=kind))
        before = {p: p.read_bytes() for p in self.vault.rglob('*.md')}
        with patch('atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend',
                   side_effect=AssertionError('Source comparison needs no model')), TestClient(self.app) as client:
            response = client.post('/chat', json={
                'content': 'Przeczytaj notatkę „COMPARE-SOURCE-729”. Pokaż źródła i ich sprzeczności. Niczego nie zapisuj.',
                'execution_preferences': {'memory_enabled': True, 'file_enabled': True,
                    'internet_enabled': False, 'active_work_mode': 'quick'}})
        assert response.status_code == 200
        data = response.json()
        trace = data['assistant_message_metadata_json']['agentic_tool_loop']
        assert trace['status'] == 'source_conflict'
        assert trace['tools_used'].count('obsidian_read') == 2
        assert 'Etap: kontrola kompozycji.' in data['assistant_message']
        assert 'Etap: storyboard.' in data['assistant_message']
        assert 'Nie ustalam' in data['assistant_message']
        assert before == {p: p.read_bytes() for p in self.vault.rglob('*.md')}

    def test_missing_named_note_reports_no_source_without_calling_a_model(self):
        from fastapi.testclient import TestClient
        with patch('atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend',
                   side_effect=AssertionError('A named lookup needs no model')), TestClient(self.app) as client:
            response = client.post('/chat', json={
                'content': 'Co jest w notatce DOES-NOT-EXIST-729? Niczego nie zapisuj.',
                'execution_preferences': {'memory_enabled': True, 'file_enabled': True,
                    'internet_enabled': False, 'active_work_mode': 'quick'}})
        assert response.status_code == 200
        result = response.json()
        trace = result['assistant_message_metadata_json']['agentic_tool_loop']
        assert trace['status'] == 'source_not_found'
        assert trace['tools_used'] == ['obsidian_search']
        assert 'Nie znalazłem wskazanej notatki' in result['assistant_message']
        assert 'model nie wywołał' not in result['assistant_message']

    def test_real_note_content_and_partial_status_through_chat(self):
        from fastapi.testclient import TestClient
        from atnos.llm.base import ChatResponse, ToolCall
        path = self.vault/'atnos-chat/memory/note-size.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        instances = []
        class Backend:
            model = 'isolated-local'
            calls = 0
            async def chat_complete(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return ChatResponse(content='', tool_calls=(ToolCall(name='obsidian_search',
                        arguments={'query': 'NOTE-SIZE'}),))
                if self.calls == 2:
                    return ChatResponse(content='', tool_calls=(ToolCall(name='obsidian_read',
                        arguments={'relative_path': 'atnos-chat/memory/note-size.md'}),))
                raise AssertionError('A real receipt must finish this read without another generation')
        def backend(*args, **kwargs):
            value = Backend()
            instances.append(value)
            return value
        with patch('atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend',
                   side_effect=backend), TestClient(self.app) as client:
            for repetitions, expected_status in ((220, 'completed'), (1000, 'incomplete')):
                with self.subTest(expected_status=expected_status):
                    path.write_text('# NOTE-SIZE\n\n' + 'A complete sentence.\n' * repetitions + 'LAST-REAL-LINE-729', encoding='utf-8')
                    response = client.post('/chat', json={
                        'content': 'Co jest w notatce NOTE-SIZE? Podaj całą treść. Niczego nie zapisuj.',
                        'execution_preferences': {'memory_enabled': True, 'file_enabled': True,
                            'internet_enabled': False, 'active_work_mode': 'quick'}})
                    assert response.status_code == 200
                    result = response.json()
                    trace = result['assistant_message_metadata_json']['agentic_tool_loop']
                    assert trace['status'] == expected_status
                    assert trace['natural_tool_focus'] == 'memory_read'
                    assert instances == []  # Literal named reads need no model/backend at all.
                    assert trace['tools_used'].count('obsidian_read') == 1
                    assert 'obsidian_write' not in trace['available_tools']
                    if expected_status == 'completed':
                        assert 'LAST-REAL-LINE-729' in result['assistant_message']
                    else:
                        assert 'To nie jest pełna treść notatki' in result['assistant_message']
                        assert 'LAST-REAL-LINE-729' not in result['assistant_message']
