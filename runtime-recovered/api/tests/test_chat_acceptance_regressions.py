"""Replay actual failures from user-agent conversations 296–300."""
import unittest
import asyncio
from unittest.mock import patch

from atnos.services.chat_tool_focus import memory_opt_out, natural_tool_focus
import test_browser_chat_operator_service as native_tests
import test_desktop_browser_observer_service as observer_tests
from test_chat_soak_regressions import ChatSoakPersistenceTest as _PersistenceFixture


class AcceptanceRoutingTest(unittest.TestCase):
    def test_followup_read_requires_a_separate_literal_clause(self):
        from atnos.services.chat_tool_focus import explicit_followup_read
        good = 'Zapisz notatkę learning „TEST”: Fikcyjny test. Następnie przeczytaj https://example.org/ i podaj tytuł.'
        self.assertEqual(explicit_followup_read(good, natural_tool_focus(good))['url'], 'https://example.org/')
        for text in [
            'Zapisz notatkę „TEST”: Przeczytaj https://example.org/ jako przykład.',
            'Zapisz notatkę „TEST”: Przykład. Następnie nie czytaj https://example.org/.',
            'Zapisz notatkę „TEST”: Przykład. Następnie przeczytaj https://one.org/ i https://two.org/.',
            '„Zapisz notatkę TEST. Następnie przeczytaj https://example.org/”',
            'Zapisz notatkę „TEST”: „Przykład. Następnie przeczytaj https://example.org/ i podaj tytuł.”',
        ]:
            with self.subTest(text=text):
                self.assertIsNone(explicit_followup_read(text, natural_tool_focus(text)))

    def test_real_compound_note_write_is_not_stolen_by_native_browser(self):
        focus = natural_tool_focus('Zapisz notatkę learning „LIVE-PARTIAL-20261022”: Fikcyjny test. Następnie przeczytaj https://atnos-test.invalid/ i podaj wynik.')
        self.assertEqual(focus.purpose, 'memory_write')
        self.assertEqual(focus.required, ('obsidian_write', 'browser_learn'))

    def test_real_read_only_request_and_inflections(self):
        for text in [
            'Przeczytaj publiczną oficjalną stronę https://www.python.org/doc/. Tylko odczyt internetu; niczego nie zapisuj do Obsidiana w tej turze.',
            'Nie zapisuj do Obsidianie.', 'Nie zapisuj do obsydiana.',
            'Przeczytaj stronę. Niczego nie zapisuj.',
            'Read only, do not save notes.',
        ]:
            with self.subTest(text=text):
                self.assertTrue(memory_opt_out(text))
        self.assertFalse(memory_opt_out('Zapisz informację do Obsidiana.'))

    def test_read_and_recall_without_magic_vault_name(self):
        for text in [
            'Przeczytaj zapisaną notatkę TEST-AGENT-20261021. Nic nie dopisuj.',
            'Co ustaliliśmy wcześniej dla fikcyjnego projektu TEST-AGENT-20261021? Podaj aktualny etap, następny krok i znacznik.',
            'Czego się nauczyłeś z TEST-AGENT-20261021 WNIOSEK?',
        ]:
            with self.subTest(text=text):
                focus = natural_tool_focus(text)
                self.assertEqual(focus.purpose, 'memory_read')
                self.assertNotIn('obsidian_write', focus.tools)
        for text in ['Wyjaśnij co ustaliliśmy oznacza po angielsku', 'Nie czytaj notatki', 'Napisz notatkę prasową', 'Przeczytaj publiczną stronę https://example.com/']:
            self.assertIsNone(natural_tool_focus(text))

    def test_named_source_is_not_an_incidental_mention_or_ambiguous_title(self):
        from atnos.services.chat_tool_focus import named_note_source
        hits = [{'title': 'TEST-AGENT-20261021', 'relative_path': 'source.md'},
                {'title': 'TEST-AGENT-20261021 WNIOSEK', 'relative_path': 'learning.md'}]
        self.assertEqual(named_note_source('Co ustaliliśmy dla TEST-AGENT-20261021?', {'hits': hits}), 'source.md')
        self.assertEqual(named_note_source('Przeczytaj TEST-AGENT-20261021 WNIOSEK', {'hits': hits}), 'learning.md')
        self.assertIsNone(named_note_source('nieznany projekt', {'hits': hits}))
        self.assertIsNone(named_note_source('TEST-AGENT-20261021', {'hits': hits + [{'title': 'TEST-AGENT-20261021', 'relative_path': 'conflict.md'}]}))


class NativeBrowserOptOutTest(unittest.TestCase):
    setUp = native_tests.BrowserChatOperatorServiceTest.setUp
    _with_service = native_tests.BrowserChatOperatorServiceTest._with_service
    def test_explicit_denial_reaches_persistence_boundary_even_with_default_enabled(self):
        # Existing fixture asserts both paths absent and persist_memory=False.
        from atnos.services.browser_chat_operator_service import BrowserChatOperatorService
        original = BrowserChatOperatorService.maybe_handle
        def read_only(service, *, content, memory_enabled=True):
            return original(service, content=content + ' Tylko odczyt internetu; niczego nie zapisuj do Obsidiana w tej turze.', memory_enabled=True)
        with patch.object(BrowserChatOperatorService, 'maybe_handle', read_only):
            native_tests.BrowserChatOperatorServiceTest.test_web_research_respects_memory_disabled(self)


class BrowserPersistenceBoundaryTest(unittest.TestCase):
    setUp = observer_tests.DesktopBrowserObserverServiceTest.setUp
    def test_real_browser_dns_error_page_is_not_summarized_or_persisted(self):
        from atnos.services.desktop_browser_observer_service import DesktopBrowserObserverService, _CdpPageSnapshot
        service = DesktopBrowserObserverService.__new__(DesktopBrowserObserverService)
        service.settings = self.settings
        snapshot = _CdpPageSnapshot(target_url='https://atnos-test.invalid/', final_url='chrome-error://chromewebdata/',
            title='atnos-test.invalid', target_id='fixture', extracted_text='DNS_PROBE_FINISHED_NXDOMAIN ' * 5,
            extraction_mode='inner_text', runtime_notes=(), artifacts={})
        with patch.object(service, '_extract_webpage_via_cdp', return_value=snapshot), \
             patch.object(service, '_fetch_webpage_text_http', side_effect=OSError('DNS failure')), \
             patch.object(service, '_summarize_webpage_text_resilient', side_effect=AssertionError('must not summarize error UI')), \
             patch.object(service, '_save_page_learning_capture', side_effect=AssertionError('must not save error UI')):
            result = service.learn_from_webpage(target_url=snapshot.target_url, persist_memory=True)
        self.assertFalse(result.ok)
        self.assertIsNone(result.vault_note_path)
        self.assertIsNone(result.curated_path)
        self.assertIn('browser_error_page_rejected', result.runtime_fallback)

    def test_default_persist_true_cannot_override_explicit_read_only_focus(self):
        from atnos.services.desktop_browser_observer_service import DesktopBrowserObserverService
        original = DesktopBrowserObserverService.learn_from_webpage
        def read_only(service, **kwargs):
            kwargs['persist_memory'] = True
            kwargs['research_focus'] = 'Przeczytaj stronę. Tylko odczyt; niczego nie zapisuj do Obsidiana.'
            return original(service, **kwargs)
        with patch.object(DesktopBrowserObserverService, 'learn_from_webpage', read_only):
            observer_tests.DesktopBrowserObserverServiceTest.test_learn_from_webpage_does_not_persist_when_memory_is_disabled(self)


class ReceiptCompletionTest(unittest.TestCase):
    def run_tools(self, names, required):
        from atnos.agentic.orchestrator import OrchestratorAgent, AgentContext
        from atnos.llm.base import ChatResponse, ToolCall
        from atnos.tools.base import BaseTool
        from atnos.tools.registry import ToolRegistry
        class Backend:
            calls = 0
            async def chat_complete(self, **kwargs):
                if self.calls >= len(names):
                    raise AssertionError('must finish from real receipts, not another model generation')
                name = names[self.calls]
                self.calls += 1
                return ChatResponse(content='', tool_calls=(ToolCall(name=name, arguments={}),))
        class Tool(BaseTool):
            description = 'test'
            parameters = {'type': 'object', 'properties': {}}
            async def execute(self, **kwargs):
                if self.name == 'obsidian_write':
                    return {'status': 'written', 'title': 'TEST', 'path': 'E:/test.md'}
                if self.name == 'obsidian_read':
                    return {'relative_path': 'atnos-chat/learning/test.md', 'markdown': 'Etap: kontrola kompozycji. Źródło: rozmowa296/1240.'}
                return {'count': 1, 'hits': [{'relative_path': 'atnos-chat/learning/test.md'}]}
        registry = ToolRegistry()
        for name in set(names):
            tool = Tool()
            tool.name = name
            registry.register_tool(tool)
        backend = Backend()
        result = asyncio.run(OrchestratorAgent().run('test', AgentContext(
            system_prompt='', backend=backend, registry=registry, required_tool_names=required,
            max_turns=len(names))))
        self.assertEqual(result.stopped_reason, 'completed')
        self.assertEqual(backend.calls, len(names))
        return result

    def test_edit_write_followed_by_required_search_is_not_false_failure(self):
        self.run_tools(['obsidian_write', 'obsidian_search'], ('obsidian_search', 'obsidian_write'))

    def test_read_returns_source_content_without_dangling_model_introduction(self):
        result = self.run_tools(['obsidian_search', 'obsidian_read'], ('obsidian_search', 'obsidian_read'))
        self.assertIn('kontrola kompozycji', result.final_response)
        self.assertIn('rozmowa296/1240', result.final_response)
        self.assertIn('atnos-chat/learning/test.md', result.final_response)


class AliasOptOutTest(unittest.TestCase):
    def test_browser_open_cannot_write_alias_when_persistence_is_disabled(self):
        from types import SimpleNamespace
        from atnos.tools.chat_agent_tools import BrowserOpenTool
        with patch('atnos.services.web_site_resolve_service.WebSiteResolveService') as resolver, \
             patch('atnos.tools.chat_agent_tools.LocalHostControlService') as host, \
             patch('atnos.services.chat_session_memory.persist_site_alias_memory') as write:
            resolver.return_value.resolve.return_value = SimpleNamespace(url='https://example.com/', candidates=[], method='alias', summary='resolved')
            host.return_value.open_browser_url.return_value = SimpleNamespace(exit_code=0, stdout='', stderr='')
            result = asyncio.run(BrowserOpenTool(settings=SimpleNamespace(), session=None, persist_memory=False).execute(query='Example site'))
        self.assertEqual(result['status'], 'opened')
        write.assert_not_called()

    def test_desktop_open_cannot_write_alias_when_persistence_is_disabled(self):
        from types import SimpleNamespace
        from atnos.tools.chat_agent_tools import DesktopAppOpenTool
        with patch('atnos.tools.chat_agent_tools.LocalHostControlService') as host, \
             patch('atnos.services.chat_session_memory.persist_desktop_alias_memory') as write:
            host.return_value.open_or_resolve_desktop_app.return_value = {'status': 'opened', 'app_key': 'comfy', 'label': 'ComfyUI'}
            result = asyncio.run(DesktopAppOpenTool(settings=SimpleNamespace(), session=None, persist_memory=False).execute(query='Comfort'))
        self.assertEqual(result['status'], 'opened')
        write.assert_not_called()


class CurrentMemoryTest(unittest.TestCase):
    setUp = _PersistenceFixture.setUp

    def test_followup_read_does_not_bypass_disabled_internet_or_exhausted_turn_budget(self):
        from fastapi.testclient import TestClient
        from atnos.llm.base import ChatResponse, ToolCall
        from atnos.tools.chat_agent_tools import BrowserLearnTool

        for internet, exhaust in ((False, False), (True, True)):
            with self.subTest(internet=internet, exhaust=exhaust):
                class Backend:
                    model = 'test-local'
                    count = 0
                    async def chat_complete(self, **kwargs):
                        self.count += 1
                        if self.count == 1:
                            return ChatResponse(content='', tool_calls=(ToolCall(name='obsidian_write', arguments={
                                'title': 'FOLLOWUP-GUARD', 'body': 'Fikcyjny test limitu.', 'note_type': 'learning'}),))
                        if exhaust:
                            return ChatResponse(content='', tool_calls=(ToolCall(name='obsidian_search', arguments={'query': 'FOLLOWUP-GUARD'}),))
                        return ChatResponse(content='Gotowe.')
                with patch('atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend', return_value=Backend()), \
                     patch.object(BrowserLearnTool, 'execute', side_effect=AssertionError('must not dispatch')) as read, TestClient(self.app) as client:
                    data = client.post('/chat', json={'content':
                        'Zapisz notatkę learning „FOLLOWUP-GUARD”: Fikcyjny test limitu. Następnie przeczytaj https://example.org/ i podaj wynik.',
                        'execution_preferences': {'memory_enabled': True, 'file_enabled': True,
                            'internet_enabled': internet, 'active_work_mode': 'quick'}}).json()
                trace = data['assistant_message_metadata_json']['agentic_tool_loop']
                self.assertFalse(trace.get('remaining_read_dispatch', False))
                read.assert_not_called()
                self.assertEqual(trace['status'], 'partially_completed' if internet else 'blocked_by_policy')

    def test_premature_model_completion_finishes_one_explicit_read_without_repeating_write(self):
        from fastapi.testclient import TestClient
        from atnos.llm.base import ChatResponse, ToolCall
        from atnos.tools.chat_agent_tools import BrowserLearnTool

        class Backend:
            model = 'test-local'
            count = 0
            async def chat_complete(self, **kwargs):
                self.count += 1
                if self.count == 1:
                    return ChatResponse(content='', tool_calls=(ToolCall(name='obsidian_write', arguments={
                        'title': 'FOLLOWUP-CHECK', 'body': 'Fikcyjny test.', 'note_type': 'learning'}),))
                return ChatResponse(content='Gotowe.')

        for fails in (False, True):
            with self.subTest(fails=fails):
                async def browser(*args, **kwargs):
                    self.assertEqual(kwargs['url'], 'https://example.org/')
                    self.assertNotIn('Zapisz notatkę', kwargs['research_focus'])
                    if fails:
                        raise OSError('Controlled read failure, no network')
                    return {'status': 'learned', 'summary': 'Controlled source result.',
                            'result': {'analysis': 'Actual controlled title: Example Domain.'}}
                backend = Backend()
                with patch('atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend', return_value=backend), \
                     patch.object(BrowserLearnTool, 'execute', side_effect=browser) as read, TestClient(self.app) as client:
                    data = client.post('/chat', json={'content':
                        'Zapisz notatkę learning „FOLLOWUP-CHECK”: Fikcyjny test. Następnie przeczytaj https://example.org/ i podaj tytuł.',
                        'execution_preferences': {'memory_enabled': True, 'file_enabled': True,
                            'internet_enabled': True, 'active_work_mode': 'quick'}}).json()
                trace = data['assistant_message_metadata_json']['agentic_tool_loop']
                self.assertTrue(trace['remaining_read_dispatch'])
                self.assertEqual(sum(c['name'] == 'obsidian_write' for c in trace['tool_calls']), 1)
                self.assertEqual(sum(c['name'] == 'browser_learn' for c in trace['tool_calls']), 1)
                self.assertEqual(read.call_count, 1)
                self.assertEqual(trace['status'], 'partially_completed' if fails else 'completed')
                self.assertIn('FOLLOWUP-CHECK', data['assistant_message'])
                self.assertIn('Nie ponawiaj' if fails else 'Example Domain', data['assistant_message'])

    def test_compound_real_note_write_then_browser_failure_preserves_receipt(self):
        import hashlib
        from fastapi.testclient import TestClient
        from atnos.llm.base import ChatResponse, ToolCall
        from atnos.tools.chat_agent_tools import BrowserLearnTool

        class Backend:
            model = 'test-local'
            count = 0
            async def chat_complete(self, **kwargs):
                self.count += 1
                if self.count == 1:
                    return ChatResponse(content='', tool_calls=(ToolCall(name='obsidian_write', arguments={
                        'title': 'COMPOUND-PARTIAL', 'body': 'Fikcyjna kontrola. Znacznik: PARTIAL-812.',
                        'note_type': 'learning'}),))
                if self.count == 2:
                    return ChatResponse(content='', tool_calls=(ToolCall(name='browser_learn', arguments={
                        'url': 'https://atnos-test.invalid/'}),))
                return ChatResponse(content='Gotowe.')

        async def failed_browser(*args, **kwargs):
            raise OSError('Controlled DNS failure; no actual network request')

        with patch('atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend', return_value=Backend()), \
             patch.object(BrowserLearnTool, 'execute', failed_browser), TestClient(self.app) as client:
            response = client.post('/chat', json={'content':
                'Zapisz notatkę learning „COMPOUND-PARTIAL”: Fikcyjna kontrola. Znacznik: PARTIAL-812. '
                'Następnie przeczytaj https://atnos-test.invalid/ i podaj wynik.',
                'execution_preferences': {'memory_enabled': True, 'file_enabled': True,
                    'internet_enabled': True, 'active_work_mode': 'quick'}})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        trace = data['assistant_message_metadata_json']['agentic_tool_loop']
        self.assertEqual(trace['status'], 'partially_completed')
        self.assertEqual(len(trace['committed_writes']), 1)
        note = self.vault / 'atnos-chat/learning/compound-partial.md'
        self.assertTrue(note.is_file())
        self.assertIn('PARTIAL-812', note.read_text(encoding='utf-8'))
        self.assertEqual(trace['committed_writes'][0]['sha256'], hashlib.sha256(note.read_bytes()).hexdigest())
        self.assertIn('Nie ponawiaj zapisu w ciemno', data['assistant_message'])
        self.assertIn('browser_learn', str(trace))
        self.assertFalse(list(self.vault.glob('Capture/**/*.md')))

    def test_completed_compound_reply_retains_write_receipt_and_actual_page_title(self):
        import hashlib
        from fastapi.testclient import TestClient
        from atnos.llm.base import ChatResponse, ToolCall
        from atnos.tools.chat_agent_tools import BrowserLearnTool
        class Backend:
            model = 'isolated-local'
            count = 0
            async def chat_complete(self, **kwargs):
                self.count += 1
                if self.count == 1:
                    return ChatResponse(content='', tool_calls=(ToolCall(name='obsidian_write', arguments={
                        'title': 'COMPOUND-COMPLETE', 'body': 'Fikcyjny test.', 'note_type': 'learning'}),))
                if self.count == 2:
                    return ChatResponse(content='', tool_calls=(ToolCall(name='browser_learn', arguments={'url': 'https://example.org/'}),))
                return ChatResponse(content='Strona służy jako przykład w dokumentacji.')
        async def browser(**kwargs):
            return {'status': 'learned', 'summary': 'Source read.', 'result': {
                'page_title': 'Example Domain', 'final_url': 'https://example.org/',
                'analysis': 'Documentation example.'}}
        with patch('atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend', return_value=Backend()), patch.object(BrowserLearnTool, 'execute', side_effect=browser), TestClient(self.app) as client:
            data = client.post('/chat', json={'content':
                'Zapisz notatkę learning „COMPOUND-COMPLETE”: Fikcyjny test. Następnie przeczytaj https://example.org/ i podaj tytuł.',
                'execution_preferences': {'memory_enabled': True, 'file_enabled': True,
                    'internet_enabled': True, 'active_work_mode': 'quick'}}).json()
        self.assertEqual(data['assistant_message_metadata_json']['agentic_tool_loop']['status'], 'completed')
        note = self.vault / 'atnos-chat/learning/compound-complete.md'
        content = data['assistant_message']
        for expected in ['Zapisałem notatkę', 'COMPOUND-COMPLETE', 'Example Domain',
                         'https://example.org/', 'przykład w dokumentacji', hashlib.sha256(note.read_bytes()).hexdigest()]:
            self.assertIn(expected, content)

    def test_provider_failure_after_real_write_preserves_durable_receipt_without_retry(self):
        import hashlib
        from fastapi.testclient import TestClient
        from atnos.llm.base import ChatResponse, ToolCall
        from atnos.providers.base import ProviderUnavailableError
        class Backend:
            model = 'isolated-local'
            count = 0
            async def chat_complete(self, **kwargs):
                self.count += 1
                if self.count == 1:
                    return ChatResponse(content='', tool_calls=(ToolCall(name='obsidian_write', arguments={
                        'title': 'PROVIDER-PARTIAL', 'body': 'Fikcyjny test. Znacznik: COMMITTED-500.',
                        'note_type': 'learning'}),))
                raise ProviderUnavailableError('Controlled Ollama HTTP 500 after committed write')
        backend = Backend()
        with patch('atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend', return_value=backend), TestClient(self.app) as client:
            response = client.post('/chat', json={'content':
                'Zapisz notatkę learning „PROVIDER-PARTIAL”: Fikcyjny test. '
                'Następnie przeczytaj https://example.org/ i podaj wynik.',
                'execution_preferences': {'memory_enabled': True, 'file_enabled': True,
                    'internet_enabled': True, 'active_work_mode': 'quick'}})
        data = response.json()
        trace = data['assistant_message_metadata_json']['agentic_tool_loop']
        self.assertEqual(trace['status'], 'partially_completed')
        self.assertEqual(len(trace['committed_writes']), 1)
        note = self.vault / 'atnos-chat/learning/provider-partial.md'
        self.assertEqual(trace['committed_writes'][0]['sha256'], hashlib.sha256(note.read_bytes()).hexdigest())
        self.assertIn('Nie ponawiaj zapisu w ciemno', data['assistant_message'])
        self.assertEqual(backend.count, 2)
        self.assertFalse(trace.get('remaining_read_dispatch', False))
        self.assertFalse(list(self.vault.glob('Capture/**/*.md')))

    def test_committed_write_is_not_reported_as_nothing_done_after_missing_review_step(self):
        from fastapi.testclient import TestClient
        from atnos.llm.base import ChatResponse, ToolCall
        class Backend:
            model = 'test-local'
            count = 0
            async def chat_complete(self, **kwargs):
                self.count += 1
                if self.count == 1:
                    return ChatResponse(content='', tool_calls=(ToolCall(name='obsidian_write', arguments={
                        'title': 'PARTIAL-FIX', 'body': 'Fikcyjny test. Etap: kontrola kompozycji.', 'note_type': 'learning'}),))
                return ChatResponse(content='Gotowe.')
        with patch('atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend', return_value=Backend()), TestClient(self.app) as client:
            response = client.post('/chat', json={'content': 'Popraw notatkę PARTIAL-FIX w Obsidianie: etap kontrola kompozycji.',
                'execution_preferences': {'memory_enabled': True, 'file_enabled': True, 'internet_enabled': False, 'active_work_mode': 'quick'}})
        data = response.json()
        trace = data['assistant_message_metadata_json']['agentic_tool_loop']
        self.assertEqual(trace['status'], 'partially_completed')
        self.assertIn('obsidian_search', trace['requested_tools_not_called'])
        self.assertIn('PARTIAL-FIX', trace['committed_writes'][0]['title'])
        self.assertIn('Zapis notatki', data['assistant_message'])
        self.assertTrue((self.vault / 'atnos-chat/learning/partial-fix.md').is_file())

    def test_read_only_keeps_retrieval_tools_but_disables_every_memory_writer(self):
        from atnos.tools.chat_agent_tools import build_chat_agent_registry
        with self.app.state.database.session_scope() as session:
            registry = build_chat_agent_registry(settings=self.settings, session=session, conversation_id=1,
                provider_registry=self.app.state.providers, memory_enabled=True, local_file_write_enabled=True, memory_write_enabled=False)
            tools = {tool.name: tool for tool in registry.list_all()}
            self.assertIn('obsidian_read', tools)
            self.assertIn('obsidian_search', tools)
            self.assertNotIn('obsidian_write', tools)
            self.assertIn('ai_delegate_background', tools)
            self.assertFalse(tools['browser_learn'].memory_enabled)
            self.assertFalse(tools['browser_open'].persist_memory)
            self.assertFalse(tools['desktop_app_open'].persist_memory)

    def test_chat_reads_unique_named_source_after_model_omits_read_and_keeps_no_write_policy(self):
        from fastapi.testclient import TestClient
        from atnos.tools.chat_agent_tools import ObsidianWriteTool
        from atnos.llm.base import ChatResponse, ToolCall
        asyncio.run(ObsidianWriteTool(settings=self.settings).execute(title='TEST-AGENT-20261021',
            body='Etap: kontrola kompozycji. Znacznik: SZAFIR-672.', note_type='learning'))
        class Backend:
            model = 'test-local'
            count = 0
            async def chat_complete(self, **kwargs):
                self.count += 1
                if self.count == 1:
                    return ChatResponse(content='', tool_calls=(ToolCall(name='obsidian_search', arguments={'query': 'TEST-AGENT-20261021'}),))
                return ChatResponse(content='Oto pełna treść:')
        with patch('atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend', return_value=Backend()), TestClient(self.app) as client:
            response = client.post('/chat', json={'content': 'Przeczytaj zapisaną notatkę TEST-AGENT-20261021. Niczego nie zapisuj.',
                'execution_preferences': {'memory_enabled': True, 'file_enabled': True, 'internet_enabled': False, 'active_work_mode': 'quick'}})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        trace = data['assistant_message_metadata_json']['agentic_tool_loop']
        self.assertEqual(trace['status'], 'completed')
        self.assertTrue(trace['source_read_dispatch'])
        self.assertIn('obsidian_read', trace['tools_used'])
        self.assertIn('kontrola kompozycji', data['assistant_message'])
        self.assertIn('SZAFIR-672', data['assistant_message'])
        self.assertNotIn('obsidian_write', trace['available_tools'])

    def test_matching_source_title_precedes_a_summary_that_repeats_query_more(self):
        from atnos.tools.chat_agent_tools import ObsidianWriteTool
        from atnos.services.chat_session_memory import load_recent_memory_brief
        writer = ObsidianWriteTool(settings=self.settings)
        asyncio.run(writer.execute(title='TEST-AGENT-20261021', body='Etap: kontrola kompozycji.', note_type='learning'))
        asyncio.run(writer.execute(title='stare streszczenie', body=('TEST AGENT 20261021 ' * 10) + 'Etap: Storyboard.', note_type='decision'))
        brief = load_recent_memory_brief(settings=self.settings, query='TEST-AGENT-20261021', limit=1)
        self.assertIn('kontrola kompozycji', brief)
        self.assertNotIn('Etap: Storyboard', brief)

    def test_real_read_without_search_gets_bounded_ambiguity_check_and_full_content(self):
        from fastapi.testclient import TestClient
        from atnos.llm.base import ChatResponse, ToolCall
        from atnos.tools.chat_agent_tools import ObsidianWriteTool
        asyncio.run(ObsidianWriteTool(settings=self.settings).execute(title='LIVE-LEARNING-20261022',
            body='Fikcyjny test.\nOstatni wiersz: COMPLETE-946.', note_type='learning'))
        class Backend:
            model = 'test-local'
            count = 0
            async def chat_complete(self, **kwargs):
                self.count += 1
                return (ChatResponse(content='', tool_calls=(ToolCall(name='obsidian_read', arguments={'relative_path': 'atnos-chat/learning/live-learning-20261022.md'}),))
                        if self.count == 1 else ChatResponse(content='Oto pełna treść:'))
        with patch('atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend', return_value=Backend()), TestClient(self.app) as client:
            # Natural literal-title requests now search/read without a model.
            # Keep this regression exercising read-before-search via explicit tool use.
            response = client.post('/chat', json={'content': 'Użyj obsidian_read relative_path atnos-chat/learning/live-learning-20261022.md i zacytuj całą treść LIVE-LEARNING-20261022. Niczego nie zapisuj.',
                'execution_preferences': {'memory_enabled': True, 'file_enabled': True, 'internet_enabled': False, 'active_work_mode': 'quick'}})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        trace = data['assistant_message_metadata_json']['agentic_tool_loop']
        self.assertEqual(trace['status'], 'completed')
        self.assertIn('COMPLETE-946', data['assistant_message'])
        self.assertEqual(trace['tool_calls'][-1]['name'], 'obsidian_search')

    def test_auto_memory_brief_reads_selected_source_after_external_edit_or_delete(self):
        from atnos.tools.chat_agent_tools import ObsidianWriteTool
        from atnos.services.chat_session_memory import load_recent_memory_brief
        asyncio.run(ObsidianWriteTool(settings=self.settings).execute(
            title='EXTERNAL-CURRENT', body='Etap: stary szkic.', note_type='learning'))
        self.assertIn('stary szkic', load_recent_memory_brief(settings=self.settings, query='EXTERNAL-CURRENT', limit=1))
        source = self.vault / 'atnos-chat/learning/external-current.md'
        # Obsidian or an editor does not call Atnos cache invalidation.
        source.write_text('Etap: aktualna kontrola kompozycji. Znacznik: CURRENT-729.', encoding='utf-8')
        brief = load_recent_memory_brief(settings=self.settings, query='EXTERNAL-CURRENT', limit=1)
        self.assertIn('CURRENT-729', brief)
        self.assertNotIn('stary szkic', brief)
        source.unlink()
        self.assertNotIn('CURRENT-729', load_recent_memory_brief(settings=self.settings, query='EXTERNAL-CURRENT', limit=1))

    def test_queue_filters_apply_before_limit_and_held_tasks_cannot_execute(self):
        from atnos.ai_workers.task_queue import AiWorkerTaskQueue
        from atnos.ai_workers.delegation_router import AiDelegationRouter
        with self.app.state.database.session_scope() as session:
            queue = AiWorkerTaskQueue(session)
            target = queue.enqueue_manual(tasks=['TEST-AGENT-20261021 pierwsze', 'TEST-AGENT-20261021 drugie'], conversation_id=297)
            queue.enqueue_manual(tasks=['OTHER unrelated'], conversation_id=298)
            router = AiDelegationRouter(session, self.settings)
            rows = router.list_tasks(query='TEST-AGENT-20261021', conversation_id=297, limit=2)
            self.assertEqual({r['id'] for r in rows}, {r['id'] for r in target})
            self.assertEqual(router.list_tasks(query='TEST-AGENT-20261021', conversation_id=298), [])
            with self.assertRaisesRegex(ValueError, 'hold_only'):
                asyncio.run(router.run_task(target[0]['id']))
            self.assertEqual(router.get_task(target[0]['id'])['status'], 'planned')

    def test_natural_status_preserves_query_scope(self):
        from atnos.services.chat_tool_focus import direct_read_request, format_background_status
        text = 'Sprawdź jakie zadania w tle czekają z testu TEST-AGENT-20261021 w rozmowie297.'
        # Ordinary separate-number spelling is also accepted.
        text = text.replace('rozmowie297', 'rozmowie 297')
        self.assertEqual(direct_read_request(text, natural_tool_focus(text)), ('ai_delegate_status', {'query': 'TEST-AGENT-20261021', 'conversation_id': 297}))
        summary = format_background_status({'tasks': [{'id': 122, 'status': 'planned', 'sequence_index': 0, 'task_group': 'group', 'summary': 'test'}]})
        self.assertIn('pozycja 1', summary)
        named = 'Pokaż status zadań o nazwie „przygotuj storyboard” w tej rozmowie.'
        self.assertEqual(direct_read_request(named, natural_tool_focus(named), conversation_id=42),
            ('ai_delegate_status', {'query': 'przygotuj storyboard', 'conversation_id': 42}))

    def test_conflicting_named_notes_cannot_be_accepted_as_current_state(self):
        from fastapi.testclient import TestClient
        from atnos.tools.chat_agent_tools import ObsidianWriteTool
        from atnos.llm.base import ChatResponse, ToolCall
        writer = ObsidianWriteTool(settings=self.settings)
        for kind, body in [('learning', 'Etap: kontrola kompozycji.'), ('decision', 'Etap: stary storyboard.')]:
            asyncio.run(writer.execute(title='CONFLICT-TEST', body=body, note_type=kind))
        class Backend:
            model = 'test-local'
            count = 0
            async def chat_complete(self, **kwargs):
                self.count += 1
                call = ToolCall(name='obsidian_search', arguments={'query': 'CONFLICT-TEST'}) if self.count == 1 else ToolCall(name='obsidian_read', arguments={'relative_path': 'atnos-chat/learning/conflict-test.md'})
                return ChatResponse(content='', tool_calls=(call,))
        with patch('atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend', return_value=Backend()), TestClient(self.app) as client:
            response = client.post('/chat', json={'content': 'Przeczytaj zapisaną notatkę CONFLICT-TEST. Niczego nie zapisuj.',
                'execution_preferences': {'memory_enabled': True, 'file_enabled': True, 'internet_enabled': False, 'active_work_mode': 'quick'}})
        data = response.json()
        self.assertEqual(data['assistant_message_metadata_json']['agentic_tool_loop']['status'], 'source_conflict')
        self.assertIn('atnos-chat/learning/conflict-test.md', data['assistant_message'])
        self.assertIn('atnos-chat/decision/conflict-test.md', data['assistant_message'])
        self.assertNotIn('Etap:', data['assistant_message'])


if __name__ == '__main__':
    unittest.main()
