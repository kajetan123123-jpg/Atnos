"""Scoped memory queries must not lose their source to unrelated chunk budgets."""
import sqlite3

import pytest

from test_support import build_test_settings
from test_semantic_knowledge_index_service import FakeEmbeddingClient, _write_document
from atnos.services.semantic_knowledge_index_service import SemanticKnowledgeIndexService


@pytest.mark.parametrize('filters', [
    {'document_paths': ('z-target.md',)},
    {'project_ids': ('target',)},
    {'topics': ('target-topic',)},
    {'source_types': ('obsidian_note',)},
    {'modified_after': '2026-10-01'},
    {'modified_before': '2026-01-01'},
])
def test_filters_precede_unrelated_chunk_limit(tmp_path, monkeypatch, filters):
    settings = build_test_settings(tmp_path)
    for name, project in [('a-noise.md', 'noise'), ('z-target.md', 'target')]:
        _write_document(settings.knowledge_dir / name, title=project, topic=project+'-topic',
                        project_id=project, body='Watchdog recovery restores a failed service.')
    service = SemanticKnowledgeIndexService(settings, embedding_client=FakeEmbeddingClient())
    service.reindex()
    with sqlite3.connect(settings.semantic_knowledge_index_path) as db:
        db.execute("UPDATE knowledge_documents SET source_mtime='2026-05-01'")
        db.execute("UPDATE knowledge_documents SET source_type='obsidian_note',source_mtime=? WHERE path='z-target.md'",
                   ('2025-01-01' if 'modified_before' in filters else '2026-10-29',))
        first = db.execute('SELECT document_path FROM knowledge_chunks WHERE active=1 LIMIT 1').fetchone()[0]
        assert first != 'z-target.md', 'Fixture must reproduce unrelated chunks taking the budget'
    monkeypatch.setattr('atnos.services.semantic_knowledge_index_service.MAX_QUERY_CHUNKS', 1)
    matches = service.lookup('watchdog recovery', **filters)
    assert len(matches) == 1
    assert matches[0].path == 'z-target.md'


def test_nonmatching_filter_is_not_an_empty_or_broken_index(tmp_path):
    settings = build_test_settings(tmp_path)
    _write_document(settings.knowledge_dir/'source.md', title='Source', topic='runtime',
                    body='Watchdog recovery restores a failed service.')
    service = SemanticKnowledgeIndexService(settings, embedding_client=FakeEmbeddingClient())
    service.reindex()
    assert service.lookup('watchdog recovery', document_paths=("absent' OR 1=1 --",)) == []


def test_scoped_topic_keeps_unicode_case_matching(tmp_path):
    settings = build_test_settings(tmp_path)
    _write_document(settings.knowledge_dir/'source.md', title='Source', topic='ŁĄCZNOŚĆ',
                    body='Watchdog recovery restores a failed service.')
    service = SemanticKnowledgeIndexService(settings, embedding_client=FakeEmbeddingClient())
    service.reindex()
    assert len(service.lookup('watchdog recovery', topics=('łączność',))) == 1
