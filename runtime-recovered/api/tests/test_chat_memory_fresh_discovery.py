"""External Obsidian edits must affect discovery before the catalog TTL expires."""
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from atnos.services import chat_session_memory as memory
from memory.obsidian_note_store import NoteSchema, write_note
from test_support import build_test_settings, make_temp_dir


@pytest.fixture
def workspace():
    with make_temp_dir() as temp:
        root = Path(temp)
        settings = build_test_settings(root)
        vault = root/'vault'
        vault.mkdir()
        object.__setattr__(settings, 'obsidian_vault_path', vault)
        yield settings, vault
        memory.invalidate_memory_catalog(vault)


def note(settings, title, body, kind='memory'):
    return write_note(NoteSchema(title=title, agent='atnos-chat', type=kind,
        created_at=datetime.now(UTC).isoformat(), status='active'), body, settings=settings)


def test_external_body_update_is_searchable_before_ttl(workspace):
    settings, _ = workspace
    source = note(settings, 'Production source', 'Stage: ALPHABRAVO')
    assert 'ALPHABRAVO' in memory.load_recent_memory_brief(settings=settings, query='ALPHABRAVO')
    source.write_text(source.read_text('utf-8').replace('ALPHABRAVO', 'ZULUDELTA'), encoding='utf-8')
    assert 'ZULUDELTA' in memory.load_recent_memory_brief(settings=settings, query='ZULUDELTA')
    assert 'Production source' not in memory.load_recent_memory_brief(settings=settings, query='ALPHABRAVO')


def test_new_external_note_is_discovered_through_both_caches(workspace):
    settings, _ = workspace
    source = note(settings, 'Existing note', 'Existing subject')
    memory.load_recent_memory_brief(settings=settings, query='NEW-DISCOVERY')
    # Copy a valid note as an external editor would, not via write_note/invalidation.
    new_source = source.with_name('new-discovery.md')
    new_source.write_text(source.read_text('utf-8').replace('Existing note', 'NEW-DISCOVERY')
                          .replace('Existing subject', 'External new source 921'), encoding='utf-8')
    assert 'External new source 921' in memory.load_recent_memory_brief(settings=settings, query='NEW-DISCOVERY')


def test_external_file_rename_keeps_the_real_source_link(workspace):
    settings, _ = workspace
    source = note(settings, 'Named source', 'EXACT-CURRENT-CONTENT')
    memory.load_recent_memory_brief(settings=settings, query='Named source')
    source.rename(source.with_name('renamed-by-user.md'))
    brief = memory.load_recent_memory_brief(settings=settings, query='Named source')
    assert 'EXACT-CURRENT-CONTENT' in brief
    assert 'atnos-chat/memory/renamed-by-user' in brief
    assert '[[atnos-chat/memory/named-source|' not in brief


def test_plain_note_after_external_edit_survives_cold_catalog(workspace):
    settings, vault = workspace
    source = note(settings, 'EXTERNAL-CURRENT', 'Old content')
    memory.load_recent_memory_brief(settings=settings, query='EXTERNAL-CURRENT')
    source.write_text('Etap: aktualna ocena grafiki. CURRENT-COLD-729.', encoding='utf-8')
    memory.invalidate_memory_catalog(vault)
    brief = memory.load_recent_memory_brief(settings=settings, query='EXTERNAL-CURRENT')
    assert 'CURRENT-COLD-729' in brief and 'Old content' not in brief
    assert 'atnos-chat/memory/external-current' in brief


def test_deleted_top_match_does_not_hide_surviving_source(workspace):
    settings, _ = workspace
    old = note(settings, 'Reserved source', 'FALLBACK-SELECT unique original')
    note(settings, 'Other source', 'FALLBACK-SELECT surviving source')
    memory.load_recent_memory_brief(settings=settings, query='Reserved source', limit=1)
    old.unlink()
    assert 'surviving source' in memory.load_recent_memory_brief(settings=settings, query='FALLBACK-SELECT', limit=1)


def test_unchanged_catalog_does_not_reparse_all_note_bodies(workspace):
    settings, _ = workspace
    for index in range(12):
        note(settings, f'Subject {index}', f'Body for subject {index}')
    memory.load_recent_memory_brief(settings=settings, query='Subject 7', limit=1)
    with patch.object(memory, 'read_notes', wraps=memory.read_notes) as discovery:
        brief = memory.load_recent_memory_brief(settings=settings, query='Subject 7', limit=1)
    assert 'Body for subject 7' in brief
    discovery.assert_not_called()


def test_conflicting_named_sources_are_exposed_even_with_limit_one(workspace):
    import os
    settings, _ = workspace
    old = note(settings, 'CHANNEL STATUS', 'OLD-PLAN: CHANNEL STATUS CHANNEL STATUS edit voice', kind='decision')
    os.utime(old, (1_700_000_000, 1_700_000_000))
    note(settings, 'CHANNEL STATUS', 'CURRENT-SOURCE: waiting for image review', kind='memory')
    brief = memory.load_recent_memory_brief(settings=settings, query='Przeczytaj CHANNEL STATUS', limit=1)
    assert 'KONFLIKT ŹRÓDEŁ' in brief
    assert brief.index('CURRENT-SOURCE') < brief.index('OLD-PLAN')
    assert 'atnos-chat/memory/channel-status' in brief
    assert 'atnos-chat/decision/channel-status' in brief
    assert 'historia, nie bieżący stan' in brief


def test_explicit_source_title_wins_over_repeated_mentions_in_test_learning(workspace):
    settings, _ = workspace
    note(settings, 'CHANNEL STATUS', 'CURRENT-PRODUCTION: waiting for visual approval')
    note(settings, 'Test pamięci', 'CHANNEL STATUS ' * 12 + 'Completed memory test, not a rendered film.', kind='learning')
    brief = memory.load_recent_memory_brief(settings=settings, query='Przeczytaj CHANNEL STATUS', limit=1)
    assert 'CURRENT-PRODUCTION' in brief
    assert 'Completed memory test' not in brief
    learning = memory.load_recent_memory_brief(settings=settings, query='Przeczytaj Test pamięci', limit=1)
    assert 'Completed memory test' in learning
    assert 'wniosek, nie dowód stanu produkcji' in learning


def test_decision_context_exposes_historical_provenance_instead_of_live_state():
    from types import SimpleNamespace
    from atnos.services.decision_log_service import DecisionLogService
    service = DecisionLogService.__new__(DecisionLogService)
    source_date = datetime(2020, 1, 2, tzinfo=UTC)
    entry = SimpleNamespace(kind=SimpleNamespace(value='decision'), made_at=source_date,
        conversation_id=42, evidence_message_ids=[8], summary='OLD-STATE', due_at=None)
    service.recall_active = lambda project_id: SimpleNamespace(entries=[entry])
    context = service.build_context_block(project_id=None)
    assert 'not verified current state' in context
    assert 'freshly read source notes' in context and 'disclose the conflict' in context
    assert '2020-01-02' in context and 'conversation=42; messages=8' in context
