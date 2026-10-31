"""Regress real local-chat failures without invoking a model."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

from atnos.llm.base import ChatResponse, ToolCall
from atnos.services.chat_tool_focus import ToolFocus, ground_file_first_line
from atnos.tools.base import ToolResult
from test_support import create_test_app, make_temp_dir

BAD = json.loads((Path(__file__).parent/'fixtures/chat_accuracy_bad_responses.json').read_text(encoding='utf-8-sig'))
FIRST = '"""Closed actions actually implemented by the painted planning-desk renderer."""'
FOCUS = ToolFocus('file_read', ('fs_read',), ('fs_read',))

class SourceGroundingTest(unittest.TestCase):
    def test_file_tool_content_is_literal_while_other_tool_contracts_stay_json(self):
        text = FIRST+'\npath = r"E:\\Atnos\\file.txt"\n```\n'
        result=ToolResult(success=True,data={'path':r'E:\source.py','content':text,'truncated':False})
        literal=result.to_tool_message_content(literal_text=True)
        self.assertIn(text,literal)
        self.assertIn('UNTRUSTED FILE TEXT',literal)
        self.assertIn('````\n',literal)
        self.assertEqual(json.loads(result.to_tool_message_content())['data']['content'],text)
        failed=ToolResult(success=False,error='Not found')
        self.assertFalse(json.loads(failed.to_tool_message_content(literal_text=True))['success'])

    def setUp(self):
        self.path = r'E:\audit\example.py'
        self.request = f'Przeczytaj {self.path}. Zacytuj dosłownie pierwszą linię oraz podaj nazwę funkcji.'
        self.read = {'path':self.path,'content':FIRST+'\ndef planning_desk_action(): pass\n','truncated':False}

    def test_real_bad_quote_is_not_a_completed_answer(self):
        replacement, trace = ground_file_first_line(self.request, FOCUS, [self.read], BAD['file'])
        self.assertIn(FIRST, replacement)
        self.assertNotIn('\\u201d', replacement)
        self.assertEqual(trace['status'], 'model_quote_mismatch')
        self.assertIn('częściowo wykonane', replacement)

    def test_valid_inline_quote_keeps_the_compound_answer(self):
        answer=f'Pierwszy wiersz: `{FIRST}`\n\nFunkcja: `planning_desk_action`.'
        replacement, trace = ground_file_first_line(self.request, FOCUS, [self.read], answer)
        self.assertIsNone(replacement)
        self.assertEqual(trace['status'], 'verified')

    def test_wrong_source_or_missing_read_cannot_prove_a_quote(self):
        for reads in ([], [{**self.read,'path':r'E:\different.py'}]):
            with self.subTest(reads=bool(reads)):
                replacement, trace = ground_file_first_line(self.request, FOCUS, reads, FIRST)
                self.assertEqual(trace['status'], 'unverified')
                self.assertIn('brak jednoznacznego', replacement)

    def test_truncated_first_line_cannot_be_promoted(self):
        replacement, trace = ground_file_first_line(self.request, FOCUS,
            [{**self.read,'content':FIRST[:20], 'truncated':True}], FIRST[:20])
        self.assertEqual(trace['status'],'unverified')
        self.assertIn('ucięty',replacement)

    def test_no_quote_request_does_not_apply_a_first_line_gate(self):
        self.assertIsNone(ground_file_first_line(f'Przeczytaj {self.path} i wyjaśnij kod.', FOCUS, [self.read], 'Wyjaśnienie.'))
        self.assertIsNone(ground_file_first_line(self.request, None, [self.read], FIRST))

    def test_chat_marks_real_bad_model_response_incomplete_and_preserves_no_write(self):
        tmp = make_temp_dir(); self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        app = create_test_app(root); self.addCleanup(app.state.database.dispose)
        target=root/'example.py';target.write_text(self.read['content'],encoding='utf-8')
        class Backend:
            model='isolated-local'
            def __init__(self):
                self.responses=[ChatResponse(content='',tool_calls=(ToolCall(name='fs_read',arguments={'path':str(target)}),)),
                                ChatResponse(content=BAD['file'])]
            async def chat_complete(self, **kwargs):
                return self.responses.pop(0)
        with patch('atnos.services.chat_orchestration_service.ChatOrchestrationService._build_agentic_tool_loop_backend',return_value=Backend()), TestClient(app) as client:
            data=client.post('/chat',json={'content':f'Przeczytaj {target}. Zacytuj dosłownie pierwszą linię i podaj nazwę funkcji. Tylko odczyt, bez zapisu pamięci.',
                'execution_preferences':{'memory_enabled':False,'file_enabled':True,'internet_enabled':False,'active_work_mode':'quick'}}).json()
        trace=data['assistant_message_metadata_json']['agentic_tool_loop']
        self.assertEqual(trace['status'],'incomplete')
        self.assertEqual(trace['quote_grounding']['status'],'model_quote_mismatch')
        self.assertEqual(trace['tools_used'],['fs_read'])
        self.assertFalse(trace['settings']['memory_write_enabled'])
        self.assertIn(FIRST,data['assistant_message'])
        self.assertNotIn('\\u201d',data['assistant_message'])

if __name__=='__main__':
    unittest.main()
