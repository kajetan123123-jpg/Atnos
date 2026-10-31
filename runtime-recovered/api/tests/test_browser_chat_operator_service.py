from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import sys
import unittest
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.services.browser_access_diagnostics_service import ChromeBrowserAccessDiagnosticsView
from atnos.services.browser_chat_operator_service import BrowserChatOperatorService
from atnos.services.desktop_browser_observer_service import BrowserPageLearningView

from test_support import build_test_settings, make_temp_dir


class BrowserChatOperatorServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
    def _with_service(self, callback) -> None:
        from atnos.db.session import DatabaseManager
        from atnos.providers.registry import build_stub_provider_registry

        settings = build_test_settings(self.root)
        database = DatabaseManager(settings)
        database.initialize()
        self.addCleanup(database.dispose)
        with database.session_scope() as session:
            service = BrowserChatOperatorService(
                session,
                settings=settings,
                provider_registry=build_stub_provider_registry(),
            )
            callback(service)

    def test_does_not_activate_for_regular_chat(self) -> None:
        def run(service: BrowserChatOperatorService) -> None:
            result = service.maybe_handle(content="napisz mi plan dnia na jutro")
            self.assertFalse(result.activated)

        self._with_service(run)

    def test_lists_tabs_for_chrome_request(self) -> None:
        diagnostics = ChromeBrowserAccessDiagnosticsView(
            browser_running=True,
            process_count=3,
            mode="devtools_live",
            summary="Atnos ma live odczyt kart przez Chrome DevTools.",
            reasons=("Chrome jest uruchomiony z --remote-debugging-port.",),
            remote_debugging_port=9222,
            visible_windows=(),
            tabs=(
                {"title": "GitHub", "url": "https://github.com/", "source": "devtools"},
            ),
            detected_apps=("GitHub",),
            session_source_file=None,
            checked_at="2026-05-16T10:05:10+00:00",
        )

        def run(service: BrowserChatOperatorService) -> None:
            with patch(
                "atnos.services.browser_chat_operator_service.BrowserAccessDiagnosticsService"
            ) as diagnostics_service:
                diagnostics_service.return_value.diagnose_chrome.return_value = diagnostics
                result = service.maybe_handle(content="jakie karty sa otwarte w chrome?")
            self.assertTrue(result.activated)
            self.assertEqual(result.action, "list_tabs")
            self.assertIn("https://github.com/", result.assistant_content or "")

        self._with_service(run)

    def test_learns_from_active_page_when_requested(self) -> None:
        learning = BrowserPageLearningView(
            action_kind="browser_learn_active_page",
            ok=True,
            target_url="https://github.com/",
            final_url="https://github.com/",
            selector="active-browser-window",
            note_id="general-browser-learning-github-20260516-020000",
            curated_path="F:/AtnosLibrary/curated/general/browser-learning-github.md",
            knowledge_path="F:/Apps/Atnos/runtime-recovered/api/knowledge/imported/general/browser-learning-github.md",
            vault_note_path="Atnos/Research/BrowserLearning/general/browser-learning-github.md",
            extracted_text_excerpt="GitHub ma otwarte repo i pasek zakladek.",
            analysis="Wniosek: Atnos moze wyciagnac stan aktywnej karty i zapisac go do Obsidiana.",
            provider_key="ollama-local",
            model_key="ollama-local/document-default",
            runtime_fallback="active_browser_window_capture",
            started_at="2026-05-16T10:10:00+00:00",
            finished_at="2026-05-16T10:10:07+00:00",
            browser_artifacts={"foreground_screenshot": "F:/AtnosSystem/AppData/Roaming/Atnos/data/browser-active.png"},
        )

        def run(service: BrowserChatOperatorService) -> None:
            with patch(
                "atnos.services.browser_chat_operator_service.DesktopBrowserObserverService"
            ) as observer_service:
                observer_service.return_value.learn_from_active_browser_page.return_value = learning
                result = service.maybe_handle(
                    content="przeanalizuj otwarta karte chrome i zapisz wnioski do obsydiana"
                )
            self.assertTrue(result.activated)
            self.assertEqual(result.action, "learn_active_page")
            self.assertIn("Atnos/Research/BrowserLearning/general/browser-learning-github.md", result.assistant_content or "")

        self._with_service(run)

    def test_web_research_respects_memory_disabled(self) -> None:
        learning = BrowserPageLearningView(
            action_kind="browser_learn_page",
            ok=True,
            target_url="https://example.com/",
            final_url="https://example.com/",
            selector="body",
            note_id=None,
            curated_path=None,
            knowledge_path=None,
            vault_note_path=None,
            extracted_text_excerpt="Example source text.",
            analysis="The page contains general information.",
            provider_key="ollama-local",
            model_key="ollama-local/document-default",
            runtime_fallback="memory_persistence_disabled",
            started_at="2026-10-18T10:00:00+00:00",
            finished_at="2026-10-18T10:00:01+00:00",
            browser_artifacts={},
            skill_instruction_context={"instruction_status": "loaded", "loaded_instructions": [{"skill_id": "atnos:browser-operator", "sha256": "test-digest"}]},
        )

        def run(service: BrowserChatOperatorService) -> None:
            with patch(
                "atnos.services.browser_chat_operator_service.DesktopBrowserObserverService.learn_from_webpage",
                return_value=learning,
            ) as learn:
                result = service.maybe_handle(
                    content="sprawdź https://example.com/ i podsumuj stronę",
                    memory_enabled=False,
                )
            self.assertTrue(result.activated)
            self.assertEqual(result.action, "learn_page")
            self.assertIsNone(result.metadata["browser_operator"]["vault_note_path"])
            self.assertIsNone(result.metadata["browser_operator"]["curated_path"])
            self.assertFalse(learn.call_args.kwargs["persist_memory"])
            self.assertEqual(result.metadata["browser_operator"]["instruction_context"], learning.skill_instruction_context)
            self.assertNotIn("zapisalem wnioski", result.assistant_content or "")
            incomplete = replace(learning, analysis="analiza niekompletna: timeout", runtime_fallback="analysis_incomplete_llm_failed; memory_persistence_disabled")
            with patch("atnos.services.browser_chat_operator_service.DesktopBrowserObserverService.learn_from_webpage", return_value=incomplete):
                incomplete_result = service.maybe_handle(content="sprawdź https://example.com/ i podsumuj stronę", memory_enabled=False)
            self.assertEqual(incomplete_result.metadata["browser_operator"]["status"], "incomplete")
            self.assertIn("Nie ukończyłem", incomplete_result.assistant_content or "")

        self._with_service(run)
