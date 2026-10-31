import httpx
import time
import unittest
from unittest.mock import patch
from atnos.services.desktop_browser_observer_service import DesktopBrowserObserverService, _CdpPageSnapshot
from atnos.services.browser_chat_operator_service import BrowserChatOperatorService


class BrowserLiteralFieldsTest(unittest.TestCase):
    def snapshot(self, **changes):
        return _CdpPageSnapshot(**{'target_url':'https://audit.invalid/','final_url':'https://audit.invalid/',
            'title':'WB | Research', 'target_id':'fixture',
            'extracted_text':'One Balance, Two Jobs\nThis is an explanatory paragraph about the balance, not the heading.',
            'extraction_mode':'inner_text','runtime_notes':(), 'artifacts':{},
            'h1_headings':('One Balance, Two Jobs',),**changes})

    def read(self, snapshot, request='Przeczytaj https://audit.invalid/ i podaj dokładny nagłówek H1 i tytuł karty oraz źródło. Bez zapisu pamięci.'):
        service=DesktopBrowserObserverService.__new__(DesktopBrowserObserverService)
        with patch.object(service,'_extract_webpage_via_cdp',return_value=snapshot), \
             patch.object(service,'_summarize_webpage_text_resilient',side_effect=AssertionError('literal fields must not be rewritten by a model')), \
             patch.object(service,'_save_page_learning_capture',side_effect=AssertionError('read only')):
            return service.learn_from_webpage(target_url='https://audit.invalid/',research_focus=request,persist_memory=False)

    def test_title_heading_and_description_are_distinct_and_no_model_is_called(self):
        view=self.read(self.snapshot())
        self.assertIn('Tytuł karty: WB | Research',view.analysis)
        self.assertIn('Nagłówek H1:\nOne Balance, Two Jobs',view.analysis)
        self.assertNotIn('explanatory paragraph',view.analysis)
        self.assertIsNone(view.provider_key)
        self.assertIsNone(view.model_key)
        self.assertTrue(view.literal_source_fields)
        self.assertIsNone(view.vault_note_path)
        self.assertEqual(BrowserChatOperatorService._learning_status(view),'ok')

    def test_absent_h1_is_not_replaced_by_title_or_paragraph(self):
        view=self.read(self.snapshot(h1_headings=()))
        self.assertIn('Brak nagłówka H1',view.analysis)
        self.assertNotIn('Nagłówek H1:\nWB | Research',view.analysis)

    def test_missing_structured_source_is_unknown_not_success(self):
        view=self.read(self.snapshot(h1_headings=None))
        self.assertIn('UNKNOWN',view.analysis)
        self.assertEqual(BrowserChatOperatorService._learning_status(view),'incomplete')

    def test_multiple_unicode_headings_and_long_heading_are_not_cut_in_chat(self):
        long_heading='Żółć i oszczędności — '+('pełna treść '*150)+'KONIEC'
        view=self.read(self.snapshot(h1_headings=(long_heading,'Drugi nagłówek')))
        message=BrowserChatOperatorService._render_learning_message(view)
        self.assertIn(long_heading,message)
        self.assertIn('Drugi nagłówek',message)
        self.assertNotIn('Model analizy: `unknown`',message)

    def test_analysis_or_negated_request_does_not_get_silently_replaced_by_fields(self):
        for request in ('Przeanalizuj stronę i podaj nagłówek.', 'Nie podawaj nagłówka strony.', 'Podsumuj treść i podaj tytuł.',
                        'Podaj nagłówek i autora artykułu.', 'Podaj tytuł oraz datę publikacji.', 'Podaj H1 i wyjaśnij co oznacza.'):
            self.assertEqual(DesktopBrowserObserverService._literal_page_fields_request(request),())

    def test_field_names_in_a_url_are_not_an_instruction_to_read_fields(self):
        self.assertEqual(DesktopBrowserObserverService._literal_page_fields_request('Przeczytaj https://audit.invalid/h1/title'),())

    def test_http_fallback_extracts_real_h1_separately_and_preserves_three_value_contract(self):
        raw='<title>WB &amp; Research</title><!-- <h1>fake</h1> --><script>"<h1>fake</h1>"</script><h1>Plan <em>before</em> spending</h1><p>This paragraph explains the subject and must not become the heading.</p>'
        response=httpx.Response(200,text=raw,request=httpx.Request('GET','https://audit.invalid/'))
        with patch('atnos.services.desktop_browser_observer_service.httpx.Client') as client:
            client.return_value.__enter__.return_value.get.return_value=response
            legacy=DesktopBrowserObserverService._fetch_webpage_text_http('https://audit.invalid/')
            detailed=DesktopBrowserObserverService._fetch_webpage_text_http('https://audit.invalid/',include_headings=True)
        self.assertEqual(len(legacy),3)
        self.assertEqual(detailed[2],'WB & Research')
        self.assertEqual(detailed[3],('Plan before spending',))

if __name__=='__main__':
    unittest.main()
