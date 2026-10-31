"""Regression cases from actual user audit, not generated model quality scores."""
import asyncio
from pathlib import Path
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

from test_support import create_test_app, bootstrap_test_app, make_temp_dir
from atnos.services.chat_tool_focus import natural_tool_focus, direct_read_request
from atnos.services.chat_session_memory import persist_agentic_session_memory, load_recent_memory_brief
from atnos.tools.chat_agent_tools import ObsidianWriteTool, AiQueueTasksTool
from atnos.ai_workers.task_queue import AiWorkerTaskQueue
from atnos.services.creative_video_operator_service import CreativeVideoOperatorService


class ChatSoakRoutingTest(unittest.TestCase):
    def test_audited_memory_requests_require_execution_and_exclude_native_browser(self):
        cases = [
            ('Zapisz w Obsidianie notatkę „TEST”: fikcyjny projekt.', 'memory_write', 'obsidian_write'),
            ('Użyj obsidian_search query browser-learning-from-chat-obsidian-md. Nie dopisuj nowych wniosków.', 'memory_read', 'obsidian_search'),
            ('Jaki jest etap testu? Sprawdź pamięć. Nie zapisuj nowych informacji.', 'memory_read', 'obsidian_search'),
            ('Zaktualizuj pamięć w Obsidianie: etap montaż.', 'memory_write', 'obsidian_search'),
            ('Dodaj dwa zadania do kolejki: 1. przeczytać notatkę 2. porównać kadry', 'task_enqueue', 'ai_queue_tasks'),
        ]
        for content, purpose, tool in cases:
            with self.subTest(content=content):
                focus = natural_tool_focus(content)
                self.assertEqual(focus.purpose, purpose)
                self.assertIn(tool, focus.required)
                self.assertNotIn('browser_learn', focus.tools)

    def test_memory_and_url_retain_both_operations(self):
        focus = natural_tool_focus('Sprawdź w Obsidianie TEST. Następnie otwórz https://example.com i podaj tytuł.')
        self.assertEqual(set(focus.required), {'obsidian_search', 'obsidian_read', 'browser_learn'})

    def test_exact_memory_write_does_not_infer_edit_or_quoted_permission(self):
        text = 'Zapisz w Obsidianie notatkę testową „AUDYT”: Etap szkic.'
        name, args = direct_read_request(text, natural_tool_focus(text))
        self.assertEqual((name, args['title'], args['body']), ('obsidian_write', 'AUDYT', 'Etap szkic.'))
        edit = 'Zaktualizuj w Obsidianie notatkę „AUDYT”: Etap montaż.'
        self.assertIsNone(direct_read_request(edit, natural_tool_focus(edit)))
        for text in ['Wyjaśnij jak działa pamięć Obsidiana', 'Nie zapisuj w Obsidianie', '„Zapisz w Obsidianie” to przykład']:
            # Quoted smart text must not authorize mutation.
            self.assertIsNone(natural_tool_focus(text))


class ChatSoakPersistenceTest(unittest.TestCase):
    def setUp(self):
        self.temp = make_temp_dir()
        self.addCleanup(self.temp.cleanup)
        self.app = create_test_app(Path(self.temp.name))
        self.addCleanup(self.app.state.database.dispose)
        bootstrap_test_app(self.app)
        self.settings = self.app.state.settings
        self.vault = Path(self.temp.name) / 'vault'
        self.vault.mkdir()
        object.__setattr__(self.settings, 'obsidian_vault_path', self.vault)

    def test_fake_success_does_not_become_memory(self):
        result = persist_agentic_session_memory(settings=self.settings, user_message='Zleć film',
            assistant_message='Zapamiętałem. Uruchomiłem produkcję.', tools_used=[])
        self.assertIsNone(result)
        self.assertEqual(list(self.vault.rglob('*.md')), [])

    def test_exact_write_uses_real_tool_without_model_and_cannot_bypass_disabled_memory(self):
        with patch('atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend', side_effect=AssertionError('exact user fields do not need model')), TestClient(self.app) as client:
            content = 'Zapisz w Obsidianie notatkę learning „SOAKROUTE”: Wniosek testowy.'
            result = client.post('/chat', json={'content': content, 'execution_preferences': {'memory_enabled': True, 'file_enabled': True, 'internet_enabled': False}}).json()
            self.assertEqual(result['selected_model_key'], 'local-tools/executor')
            self.assertEqual(result['assistant_message_metadata_json']['agentic_tool_loop']['tools_used'], ['obsidian_write'])
            self.assertTrue((self.vault / 'atnos-chat/learning/soakroute.md').exists())
            blocked = client.post('/chat', json={'content': content.replace('SOAKROUTE', 'FORBIDDEN'), 'execution_preferences': {'memory_enabled': False, 'file_enabled': True, 'internet_enabled': False}}).json()
            self.assertEqual(blocked['assistant_message_metadata_json']['agentic_tool_loop']['status'], 'blocked_by_policy')
            self.assertFalse((self.vault / 'atnos-chat/learning/forbidden.md').exists())

    def test_real_learning_write_immediately_visible_after_catalog_was_cached(self):
        load_recent_memory_brief(settings=self.settings, query='SOAKREGRESS')
        receipt = asyncio.run(ObsidianWriteTool(settings=self.settings).execute(title='SOAKREGRESS',
            body='Zweryfikowany wniosek testowy.', note_type='learning', tags=['audit-test']))
        self.assertEqual(receipt['status'], 'written')
        self.assertEqual(len(receipt['sha256']), 64)
        self.assertIn('Zweryfikowany wniosek', load_recent_memory_brief(settings=self.settings, query='SOAKREGRESS'))

    def test_manual_queue_survives_new_session_and_repeated_submission_without_execution(self):
        with self.app.state.database.session_scope() as session:
            first = AiWorkerTaskQueue(session).enqueue_manual(tasks=['Przeczytać test', 'Porównać kadry'], conversation_id=42)
        with self.app.state.database.session_scope() as session:
            second = AiWorkerTaskQueue(session).enqueue_manual(tasks=['Przeczytać test', 'Porównać kadry'], conversation_id=42)
            self.assertEqual([r['id'] for r in first], [r['id'] for r in second])
            self.assertEqual([r['position'] for r in second], [1, 2])
            self.assertTrue(all(r['status'] == 'planned' and not r['executed'] for r in second))

    def test_queue_receipt_finishes_without_second_model_or_duplicate_action(self):
        from atnos.agentic.orchestrator import AgentContext, OrchestratorAgent
        from atnos.tools.registry import ToolRegistry
        from atnos.llm.base import ToolCall, ChatResponse
        class Backend:
            calls = 0
            async def chat_complete(self, **kwargs):
                self.calls += 1
                if self.calls > 1:
                    raise AssertionError('do not ask a second model to certify a saved receipt')
                return ChatResponse(content='', tool_calls=(ToolCall(name='ai_queue_tasks', arguments={'tasks': ['test held']}),))
        backend = Backend()
        with self.app.state.database.session_scope() as session:
            registry = ToolRegistry()
            registry.register_tool(AiQueueTasksTool(session=session, conversation_id=42))
            result = asyncio.run(OrchestratorAgent().run('queue test', AgentContext(
                system_prompt='', backend=backend, registry=registry, required_tool_names=('ai_queue_tasks',))))
            self.assertEqual(result.stopped_reason, 'completed')
            self.assertEqual(backend.calls, 1)

    def test_wb_request_never_queues_legacy_campaign_or_picks_piper(self):
        with self.app.state.database.session_scope() as session:
            service = CreativeVideoOperatorService(session, settings=self.settings,
                provider_registry=self.app.state.providers, plugin_registry=self.app.state.plugins)
            with patch.object(service, '_queue_content_campaign', side_effect=AssertionError('legacy lane used')):
                result = service.maybe_handle(content='Zleć jedną testową rolkę Wealth Blueprint z Ryan B.',
                    conversation_id=42, project_id=None, allow_local_file_write=True)
            self.assertTrue(result.activated)
            self.assertEqual(result.metadata['creative_video_operator']['workflow_key'], 'production_order')
            self.assertFalse(result.kick_scheduler)

    def test_new_immediate_intake_cannot_ignore_requested_voice_or_no_paid_apis(self):
        with TestClient(self.app) as client, patch('atnos.production.order_store.ProductionOrderStore.create', side_effect=AssertionError('must fail before durable order or dispatch')):
            for direction in ['Głos Ryan B, bez publikacji.', 'Bez płatnych API.']:
                response = client.post('/api/production/orders', json={
                    'project': 'wealth_blueprint', 'topic': 'Test constraints',
                    'direction': direction, 'delivery_mode': 'local_only'})
                self.assertEqual(response.status_code, 400)
                self.assertIn('Nie uruchomiono produkcji', response.json()['detail'])
