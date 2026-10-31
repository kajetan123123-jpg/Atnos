from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from atnos.services.chat_orchestration_service import ChatOrchestrationService


def _make_service() -> tuple[ChatOrchestrationService, Mock]:
    service = object.__new__(ChatOrchestrationService)
    context_builder = Mock()
    context_builder.build.return_value = SimpleNamespace(
        visible_attachments=(),
        provider_prompt_chars=100,
        active_document_preview_chars=0,
        active_document_preview_limit=None,
        summary="prompt_mode=compact",
    )
    service.context_builder = context_builder
    service.task_extraction = Mock()
    service.task_extraction.extract.return_value = []
    service._build_provider_conversation_history = lambda **_kwargs: ()
    service.settings = object()
    service.session = object()
    return service, context_builder


def _arguments(*, local_first: bool) -> dict:
    return {
        "selection": SimpleNamespace(
            model_config=SimpleNamespace(runtime_model_name="qwen3.5:9b", task=SimpleNamespace(value="chat")),
            provider=SimpleNamespace(local_first=local_first, key="ollama-local" if local_first else "cloud-test"),
        ),
        "conversation_result": SimpleNamespace(
            conversation=SimpleNamespace(id=7, project=SimpleNamespace(name="Wealth Blueprint")),
            user_message=SimpleNamespace(content="Jaki głos wybraliśmy?"),
        ),
        "request": SimpleNamespace(content="Jaki głos wybraliśmy?", active_document_action=None),
        "attachments": [],
        "execution_policy": object(),
        "runtime_preferences": SimpleNamespace(
            memory_enabled=True,
            quality_profile="standard",
            quality_profile_source="default",
        ),
        "intent": SimpleNamespace(document_action=None),
        "selected_review_note": None,
        "capability_snapshot": {},
        "additional_system_sections": (),
        "knowledge_section_used": False,
    }


def test_normal_local_chat_receives_relevant_obsidian_memory_and_records_use() -> None:
    service, context_builder = _make_service()
    expected_memory = "OBSIDIAN_MEMORY_TEST: Wealth Blueprint uses Ryan B voice."

    with patch(
        "atnos.services.chat_session_memory.load_recent_memory_brief",
        return_value=expected_memory,
    ) as load_memory:
        attempt = service._build_provider_execution_attempt(**_arguments(local_first=True))

    load_memory.assert_called_once()
    assert load_memory.call_args.kwargs["query"] == "Jaki głos wybraliśmy? Wealth Blueprint"
    assert expected_memory in context_builder.build.call_args.kwargs["additional_system_sections"]
    assert attempt.trace_metadata["obsidian_memory_context_used"] is True


def test_normal_cloud_chat_does_not_receive_local_obsidian_memory() -> None:
    service, context_builder = _make_service()

    with patch("atnos.services.chat_session_memory.load_recent_memory_brief") as load_memory:
        attempt = service._build_provider_execution_attempt(**_arguments(local_first=False))

    load_memory.assert_not_called()
    assert "OBSIDIAN_MEMORY_TEST" not in str(context_builder.build.call_args.kwargs["additional_system_sections"])
    assert attempt.trace_metadata["obsidian_memory_context_used"] is False
