from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from atnos.core.config import Settings
from memory.obsidian_note_store import NoteSchema, cleanup_expired_notes, deduplicate_notes, read_notes, write_note


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        app_env="test",
        app_root=tmp_path,
        data_dir=tmp_path / "data",
        obsidian_vault_path=tmp_path / "vault",
        obsidian_export_enabled=True,
        atnos_write_obsidian_logs=True,
    )


def test_structured_obsidian_note_store_write_read_cleanup_and_deduplicate(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    now = datetime.now(UTC)

    schema = NoteSchema(
        title="Test topic",
        agent="atnos-test",
        type="test",
        created_at=now.isoformat(),
        tags=["audit"],
        status="done",
        linked_notes=["[[Other Note]]"],
    )
    path = write_note(schema, "Body content", settings=settings)

    assert path == tmp_path / "vault" / "atnos-test" / "test" / "test-topic.md"
    assert path.exists()
    assert "title: Test topic" in path.read_text(encoding="utf-8")

    notes = read_notes(type="test", agent="atnos-test", status="done", settings=settings)
    assert [note.title for note in notes] == ["Test topic"]

    expired = NoteSchema(
        title="Expired topic",
        agent="atnos-test",
        type="test",
        created_at=now.isoformat(),
        expires_at=(now - timedelta(seconds=1)).isoformat(),
        status="done",
    )
    expired_path = write_note(expired, "Expired body", settings=settings)

    assert cleanup_expired_notes(settings=settings) == 1
    assert not expired_path.exists()
    assert (tmp_path / "vault" / "_archive" / "atnos-test" / "test" / "expired-topic.md").exists()

    older = NoteSchema(
        title="Duplicate topic",
        agent="atnos-test-old",
        type="test",
        created_at=(now - timedelta(seconds=2)).isoformat(),
        status="done",
    )
    newer = NoteSchema(
        title="Duplicate topic",
        agent="atnos-test-new",
        type="test",
        created_at=(now - timedelta(seconds=1)).isoformat(),
        status="done",
    )
    write_note(older, "Older duplicate", settings=settings)
    write_note(newer, "Newer duplicate", settings=settings)

    assert deduplicate_notes("test", settings=settings) == 1
    remaining = [note for note in read_notes(type="test", settings=settings) if note.title == "Duplicate topic"]
    assert len(remaining) == 1
