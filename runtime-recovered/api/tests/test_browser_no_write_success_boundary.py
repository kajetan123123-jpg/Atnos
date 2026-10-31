"""The normal successful analysis branch must stop before all knowledge writers.

Controlled extraction/analysis only: no browser, network, model or user vault.
"""
from pathlib import Path
from unittest.mock import Mock
import pytest
from atnos.services.desktop_browser_observer_service import DesktopBrowserObserverService, _CdpPageSnapshot
from test_support import build_test_settings

@pytest.mark.parametrize('denial, persist', [
    ('Tylko odczyt. Niczego nie zapisuj.', True),
    ('Read-only. Do not remember this page.', True),
    ('Nie zapisuj do Capture ani curated.', True),
    ('Sprawdź stronę.', False),
])
@pytest.mark.parametrize('outcome', ['success', 'analysis_failure', 'empty_content'])
def test_all_knowledge_writers_are_unreachable_even_after_successful_analysis(tmp_path, denial, persist, outcome):
    service = DesktopBrowserObserverService.__new__(DesktopBrowserObserverService)
    service.settings = build_test_settings(tmp_path)
    service.session = None
    service.provider_registry = None
    body = 'Actual controlled page text with enough information to analyse without fallback.'
    service._extract_webpage_via_cdp = lambda **kwargs: _CdpPageSnapshot(
        target_url='https://example.org/', final_url='https://example.org/', title='Controlled page',
        target_id='offline-no-write', extracted_text='' if outcome == 'empty_content' else body,
        extraction_mode='inner_text', runtime_notes=(), artifacts={})
    service._fetch_webpage_text_http = Mock(return_value=('', 'https://example.org/', 'Empty page'))
    service._summarize_webpage_text_resilient = Mock(
        return_value=('Controlled source-based analysis.', 'controlled', 'controlled/offline', ()))
    if outcome == 'analysis_failure':
        service._summarize_webpage_text_resilient.side_effect = RuntimeError('Controlled offline analysis failure')
    # Enforce at the parent and each sink. These failures would catch a save
    # even if the caller tried to swallow an exception and use fallback.
    sinks = {}
    for name in ('_save_page_learning_capture', '_write_obsidian_capture_note', '_write_fallback_capture_note'):
        sinks[name] = Mock(side_effect=AssertionError('Persistence forbidden: ' + name))
        setattr(service, name, sinks[name])
    service.knowledge_workspace = Mock()
    service.knowledge_workspace.save_curated_note.side_effect = AssertionError('No curated/catalog write')
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result = service.learn_from_webpage(target_url='https://example.org/', research_focus=denial, persist_memory=persist)
    for sink in sinks.values():
        sink.assert_not_called()
    service.knowledge_workspace.save_curated_note.assert_not_called()
    assert result.note_id is result.curated_path is result.knowledge_path is result.vault_note_path is None
    assert 'memory_persistence_disabled' in result.runtime_fallback
    assert 'saved_note_path' not in result.browser_artifacts
    assert before == {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    if outcome == 'success':
        service._summarize_webpage_text_resilient.assert_called_once()
        assert result.analysis == 'Controlled source-based analysis.'
    if outcome == 'empty_content':
        service._summarize_webpage_text_resilient.assert_not_called()
