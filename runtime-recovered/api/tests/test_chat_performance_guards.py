from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import text

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.providers.base import ProviderExecutionResult
from atnos.services.access_reality_service import AccessRealityService
from atnos.services.browser_access_diagnostics_service import BrowserAccessDiagnosticsService
from atnos.services.capability_action_service import CapabilityActionService
from atnos.services.obsidian_read_service import ObsidianReadService
from atnos.services.prompt_optimizer_service import PromptOptimizerService

from test_support import bootstrap_test_app, create_test_app, make_temp_dir


def test_plain_chat_does_not_count_obsidian_notes_for_capability_snapshot() -> None:
    temp_dir = make_temp_dir()
    try:
        root = Path(temp_dir.name)
        app = create_test_app(root)
        try:
            with TestClient(app) as client:
                provider = app.state.providers.get("ollama-local")
                assert provider is not None
                with patch.object(
                    provider,
                    "execute",
                    return_value=ProviderExecutionResult(content="Mocked answer."),
                ), patch.object(
                    ObsidianReadService,
                    "_count_notes",
                    side_effect=AssertionError("plain chat should not count Obsidian notes"),
                ):
                    response = client.post(
                        "/chat",
                        json={"content": "Napisz krótko, jak poprawić produktywność."},
                    )

            assert response.status_code == 200
            assert response.json()["selected_provider_key"] == "ollama-local"
        finally:
            app.state.database.dispose()
    finally:
        temp_dir.cleanup()


def test_fast_local_chat_keeps_provider_prompt_under_compact_budget() -> None:
    temp_dir = make_temp_dir()
    try:
        root = Path(temp_dir.name)
        app = create_test_app(root)
        try:
            with TestClient(app) as client:
                provider = app.state.providers.get("ollama-local")
                assert provider is not None
                with patch.object(
                    provider,
                    "execute",
                    return_value=ProviderExecutionResult(content="gotowe"),
                ) as execute_mock:
                    response = client.post(
                        "/chat",
                        json={
                            "content": "Odpowiedz jednym slowem: gotowe.",
                            "execution_preferences": {
                                "active_work_mode": "quick",
                                "quality_profile": "fast-turnaround",
                                "memory_enabled": False,
                                "file_enabled": False,
                                "internet_enabled": False,
                                "provider_posture": "strict-local",
                            },
                        },
                    )

            assert response.status_code == 200
            with app.state.database.session_scope() as session:
                trace = session.execute(
                    text(
                        """
                    select metadata_json from execution_traces
                    where step_name = 'provider-execution'
                    order by id desc limit 1
                    """
                    )
                ).scalar_one()
            metadata = json.loads(trace) if isinstance(trace, str) else trace
            assert metadata["provider_prompt_chars"] < 2_000
            provider_context = execute_mock.call_args.kwargs["context"]
            assert "AUTHORITATIVE RUNTIME IDENTITY" in provider_context.provider_prompt
            assert "Runtime model: qwen3:14b" in provider_context.provider_prompt
        finally:
            app.state.database.dispose()
    finally:
        temp_dir.cleanup()


def test_prompt_optimizer_fast_guard_only_activates_for_prompt_work() -> None:
    service = PromptOptimizerService()

    assert not service.should_optimize("Napisz krótko, jak poprawić produktywność.")
    assert service.should_optimize("napisz prompt do Codexa, żeby naprawił UI")


def test_capability_preview_fast_guard_only_activates_for_capability_work() -> None:
    assert not CapabilityActionService.might_build_preview_from_chat(
        "Napisz krótko, jak poprawić produktywność."
    )
    assert CapabilityActionService.might_build_preview_from_chat(
        "prezentacja o local-first AI w 5 slajdach"
    )
    assert CapabilityActionService.might_build_preview_from_chat(
        "codziennie przygotuj raport z kolejki shortów"
    )
    long_operational_goal = (
        "Cel glowny: napraw runtime, interfejs, routing i integracje. "
        + "Zweryfikuj kazdy etap dowodem i nie tworz placeholderow. " * 80
        + "Na koncu przygotuj raport z wykonania."
    )
    assert not CapabilityActionService.might_build_preview_from_chat(long_operational_goal)


def test_recurring_report_request_is_scheduled_instead_of_generated_immediately() -> None:
    service = object.__new__(CapabilityActionService)
    service.preview = lambda **kwargs: kwargs  # type: ignore[method-assign]

    preview = service.build_preview_from_chat(content="codziennie przygotuj raport z kolejki shortow")

    assert preview is not None
    assert preview["action"] == "schedule_recurring_task"


def test_chrome_diagnostics_uses_short_cache_for_repeated_checks() -> None:
    temp_dir = make_temp_dir()
    BrowserAccessDiagnosticsService.clear_cache()
    try:
        root = Path(temp_dir.name)
        app = create_test_app(root)
        try:
            bootstrap_test_app(app)
            with app.state.database.session_scope() as session:
                service = BrowserAccessDiagnosticsService(session, settings=app.state.settings)
                with patch.object(service, "_list_chrome_processes", return_value=[]), patch.object(
                    service.local_host,
                    "find_windows",
                    return_value=[],
                ), patch.object(service, "_read_session_urls", return_value=((), None)):
                    first = service.diagnose_chrome(tab_limit=20)

                with patch.object(
                    service,
                    "_list_chrome_processes",
                    side_effect=AssertionError("second diagnostics call should use cache"),
                ):
                    second = service.diagnose_chrome(tab_limit=20)

            assert first.mode == "unavailable"
            assert second.mode == first.mode
            assert second.checked_at == first.checked_at
        finally:
            app.state.database.dispose()
            BrowserAccessDiagnosticsService.clear_cache()
    finally:
        temp_dir.cleanup()


def test_access_reality_compact_chat_block_uses_short_cache() -> None:
    temp_dir = make_temp_dir()
    AccessRealityService.clear_cache()
    try:
        root = Path(temp_dir.name)
        app = create_test_app(root)
        try:
            bootstrap_test_app(app)
            with app.state.database.session_scope() as session:
                service = AccessRealityService(
                    session,
                    settings=app.state.settings,
                    provider_registry=app.state.providers,
                )
                with patch.object(service, "build_snapshot", wraps=service.build_snapshot) as build_snapshot:
                    first = service.build_chat_block(
                        user_message="Napisz krótko, jak poprawić produktywność.",
                        include_live_health=False,
                    )
                    second = service.build_chat_block(
                        user_message="Napisz krótko, jak poprawić produktywność.",
                        include_live_health=False,
                    )

            assert first == second
            assert build_snapshot.call_count == 1
        finally:
            app.state.database.dispose()
            AccessRealityService.clear_cache()
    finally:
        temp_dir.cleanup()


def test_access_reality_expanded_chat_block_does_not_use_compact_cache() -> None:
    temp_dir = make_temp_dir()
    AccessRealityService.clear_cache()
    try:
        root = Path(temp_dir.name)
        app = create_test_app(root)
        try:
            bootstrap_test_app(app)
            with app.state.database.session_scope() as session:
                service = AccessRealityService(
                    session,
                    settings=app.state.settings,
                    provider_registry=app.state.providers,
                )
                with patch.object(service, "build_snapshot", wraps=service.build_snapshot) as build_snapshot:
                    first = service.build_chat_block(
                        user_message="Do czego masz dostep w Atnosie?",
                        include_live_health=False,
                    )
                    second = service.build_chat_block(
                        user_message="Do czego masz dostep w Atnosie?",
                        include_live_health=False,
                    )

            assert first == second
            assert build_snapshot.call_count == 2
        finally:
            app.state.database.dispose()
            AccessRealityService.clear_cache()
    finally:
        temp_dir.cleanup()
