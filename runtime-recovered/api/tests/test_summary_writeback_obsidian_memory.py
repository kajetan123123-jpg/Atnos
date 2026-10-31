from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from test_support import build_test_settings, make_temp_dir

from atnos.services.summary_writeback_service import SummaryWritebackService


def test_regular_chat_writeback_persists_only_explicit_durable_memory() -> None:
    temp_dir = make_temp_dir()
    root = Path(temp_dir.name)
    vault = root / "vault"
    vault.mkdir()
    settings = build_test_settings(root)
    object.__setattr__(settings, "obsidian_vault_path", vault)

    service = SummaryWritebackService(Mock())
    service.conversations = Mock()
    service.conversations.update_summary = Mock()
    service.execution_traces = Mock()
    service.execution_traces.create.return_value = "memory-trace"

    conversation = SimpleNamespace(
        id=42,
        title="Atnos chat memory",
        project_id=None,
        project=None,
        summary=None,
    )
    user_message = SimpleNamespace(
        id=8,
        sequence_number=1,
        content="Pamiętaj, że Atnos ma odpowiadać po polsku, krótko i konkretnie.",
    )
    assistant_message = SimpleNamespace(
        id=9,
        sequence_number=2,
        content="Zapamiętam tę preferencję.",
    )
    runtime_preferences = SimpleNamespace(
        memory_enabled=True,
        memory_enabled_source="default",
        work_mode="auto",
        work_mode_source="default",
        response_style="concise-direct",
        response_style_source="default",
        persona_profile=None,
        persona_profile_source="global",
    )

    with patch("atnos.core.config.Settings", return_value=settings):
        trace = service.refresh_after_turn(
            conversation=conversation,
            user_message=user_message,
            assistant_message=assistant_message,
            attachments=(),
            runtime_preferences=runtime_preferences,
            project_context_applied=False,
        )

    assert trace == "memory-trace"
    note_files = list((vault / "atnos-chat" / "memory").glob("*.md"))
    assert len(note_files) == 1
    note_content = note_files[0].read_text(encoding="utf-8")
    assert "conversation:42#message:9" in note_content
    assert "odpowiadać po polsku, krótko i konkretnie" in note_content
    metadata = service.execution_traces.create.call_args.kwargs["metadata_json"]
    assert metadata["obsidian_memory_written"] is True


def test_regular_chat_writeback_does_not_store_ephemeral_turns() -> None:
    temp_dir = make_temp_dir()
    root = Path(temp_dir.name)
    vault = root / "vault"
    vault.mkdir()
    settings = build_test_settings(root)
    object.__setattr__(settings, "obsidian_vault_path", vault)

    service = SummaryWritebackService(Mock())
    service.conversations = Mock()
    service.conversations.update_summary = Mock()
    service.execution_traces = Mock()
    service.execution_traces.create.return_value = "memory-trace"
    runtime_preferences = SimpleNamespace(
        memory_enabled=True,
        memory_enabled_source="default",
        work_mode="auto",
        work_mode_source="default",
        response_style=None,
        response_style_source="default",
        persona_profile=None,
        persona_profile_source="global",
    )

    with patch("atnos.core.config.Settings", return_value=settings):
        service.refresh_after_turn(
            conversation=SimpleNamespace(id=42, title="Temporary question", project_id=None, project=None, summary=None),
            user_message=SimpleNamespace(id=10, sequence_number=1, content="Jaka jest temperatura na zewnątrz?"),
            assistant_message=SimpleNamespace(id=11, sequence_number=2, content="Nie mam bieżących danych pogodowych."),
            attachments=(),
            runtime_preferences=runtime_preferences,
            project_context_applied=False,
        )

    assert not (vault / "atnos-chat" / "memory").exists()
    metadata = service.execution_traces.create.call_args.kwargs["metadata_json"]
    assert metadata["obsidian_memory_written"] is False
