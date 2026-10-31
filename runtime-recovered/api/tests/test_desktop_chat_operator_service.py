from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.services.desktop_chat_operator_service import DesktopChatOperatorService
from atnos.services.integration_config_service import ObsidianEffectiveConfig
from atnos.services.local_host_control_service import (
    LocalDesktopAppView,
    LocalHostCommandResultView,
)

from test_support import bootstrap_test_app, create_test_app, make_temp_dir


class DesktopChatOperatorServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.app = create_test_app(self.root)
        self.addCleanup(self.app.state.database.dispose)
        bootstrap_test_app(self.app)

    def _with_service(self, callback) -> None:
        with self.app.state.database.session_scope() as session:
            service = DesktopChatOperatorService(
                session,
                settings=self.app.state.settings,
            )
            callback(service)

    def test_does_not_activate_for_regular_chat(self) -> None:
        def run(service: DesktopChatOperatorService) -> None:
            result = service.maybe_handle(content="napisz mi plan dnia")
            self.assertFalse(result.activated)

        self._with_service(run)

    def test_creates_obsidian_note_when_requested(self) -> None:
        app_view = LocalDesktopAppView(
            app_key="obsidian",
            label="Obsidian",
            category="knowledge",
            installed=True,
            running=True,
            window_visible=True,
            launch_supported=True,
            focus_supported=True,
            preferred_entry_path="C:/Users/test/Desktop/Obsidian.lnk",
            preferred_entry_kind="shortcut",
            running_processes=("obsidian.exe",),
            visible_window_titles=("Notes - Obsidian",),
            notes=(),
        )
        command = LocalHostCommandResultView(
            action_kind="desktop_app_focus",
            command="focus-window",
            exit_code=0,
            stdout="Focused Obsidian window.",
            stderr="",
            workdir=str(self.root),
            elevated=False,
            started_at="2026-05-16T11:00:00+00:00",
            finished_at="2026-05-16T11:00:01+00:00",
        )

        def run(service: DesktopChatOperatorService) -> None:
            obsidian_config = ObsidianEffectiveConfig(
                vault_path=self.root.resolve(),
                export_enabled=True,
                source="test",
                path_exists=True,
                warnings=(),
            )
            with patch(
                "atnos.services.desktop_chat_operator_service.IntegrationConfigService.get_obsidian_config",
                return_value=obsidian_config,
            ), patch(
                "atnos.services.desktop_chat_operator_service.LocalHostControlService.list_desktop_apps",
                return_value=[app_view],
            ), patch(
                "atnos.services.desktop_chat_operator_service.LocalHostControlService.focus_desktop_app",
                return_value=command,
            ):
                result = service.maybe_handle(content="zapisz do obsydiana: test notatki operatora")
            self.assertTrue(result.activated)
            self.assertEqual(result.action, "obsidian_note")
            self.assertIn("Sciezka vaulta:", result.assistant_content or "")
            self.assertIn("obsidian_note", str(result.metadata))

        self._with_service(run)

    def test_dispatches_prompt_to_kimi_when_requested(self) -> None:
        kimi_app = LocalDesktopAppView(
            app_key="desktop-kimi",
            label="Kimi",
            category="utility",
            installed=True,
            running=True,
            window_visible=True,
            launch_supported=True,
            focus_supported=True,
            preferred_entry_path="C:/Users/test/Desktop/Kimi.lnk",
            preferred_entry_kind="shortcut",
            running_processes=("kimi.exe",),
            visible_window_titles=("Kimi",),
            notes=(),
        )
        command = LocalHostCommandResultView(
            action_kind="desktop_app_focus",
            command="focus-window",
            exit_code=0,
            stdout="Focused Kimi window.",
            stderr="",
            workdir=str(self.root),
            elevated=False,
            started_at="2026-05-16T11:05:00+00:00",
            finished_at="2026-05-16T11:05:01+00:00",
        )

        def run(service: DesktopChatOperatorService) -> None:
            with patch(
                "atnos.services.desktop_chat_operator_service.LocalHostControlService.list_desktop_apps",
                return_value=[kimi_app],
            ), patch(
                "atnos.services.desktop_chat_operator_service.LocalHostControlService.focus_desktop_app",
                return_value=command,
            ), patch(
                "atnos.services.desktop_chat_operator_service.LocalHostControlService.focus_desktop_app_input",
                return_value=command,
            ), patch(
                "atnos.services.desktop_chat_operator_service.LocalHostControlService.type_text",
                return_value=command,
            ) as type_text, patch(
                "atnos.services.desktop_chat_operator_service.LocalHostControlService.press_keys",
                return_value=command,
            ) as press_keys, patch.object(
                DesktopChatOperatorService,
                "_wait_for_kimi_answer",
                return_value=("Plan publikacji: rano hook, wieczorem podsumowanie.", "uia_text_change"),
            ):
                result = service.maybe_handle(content="zapytaj Kimi o plan publikacji na jutro")
            self.assertTrue(result.activated)
            self.assertEqual(result.action, "kimi_prompt")
            self.assertIn("Kimi zwrocil zweryfikowana odpowiedz", result.assistant_content or "")
            self.assertIn("Plan publikacji", result.assistant_content or "")
            type_text.assert_called_once()
            press_keys.assert_called_once()

        self._with_service(run)

    def test_dispatches_prompt_to_kimi_for_polish_use_phrase(self) -> None:
        kimi_app = LocalDesktopAppView(
            app_key="desktop-kimi",
            label="Kimi",
            category="utility",
            installed=True,
            running=True,
            window_visible=True,
            launch_supported=True,
            focus_supported=True,
            preferred_entry_path="C:/Users/test/Desktop/Kimi.lnk",
            preferred_entry_kind="shortcut",
            running_processes=("kimi.exe",),
            visible_window_titles=("Kimi",),
            notes=(),
        )
        command = LocalHostCommandResultView(
            action_kind="desktop_app_focus",
            command="focus-window",
            exit_code=0,
            stdout="Focused Kimi window.",
            stderr="",
            workdir=str(self.root),
            elevated=False,
            started_at="2026-05-16T11:05:00+00:00",
            finished_at="2026-05-16T11:05:01+00:00",
        )

        def run(service: DesktopChatOperatorService) -> None:
            with patch(
                "atnos.services.desktop_chat_operator_service.LocalHostControlService.list_desktop_apps",
                return_value=[kimi_app],
            ), patch(
                "atnos.services.desktop_chat_operator_service.LocalHostControlService.focus_desktop_app",
                return_value=command,
            ), patch(
                "atnos.services.desktop_chat_operator_service.LocalHostControlService.focus_desktop_app_input",
                return_value=command,
            ), patch(
                "atnos.services.desktop_chat_operator_service.LocalHostControlService.type_text",
                return_value=command,
            ) as type_text, patch(
                "atnos.services.desktop_chat_operator_service.LocalHostControlService.press_keys",
                return_value=command,
            ) as press_keys, patch.object(
                DesktopChatOperatorService,
                "_wait_for_kimi_answer",
                return_value=("Plan publikacji: rano hook, wieczorem podsumowanie.", "uia_text_change"),
            ):
                result = service.maybe_handle(content="użyj Kimi do planu publikacji na jutro")
            self.assertTrue(result.activated)
            self.assertEqual(result.action, "kimi_prompt")
            self.assertIn("Kimi zwrocil zweryfikowana odpowiedz", result.assistant_content or "")
            type_text.assert_called_once()
            press_keys.assert_called_once()

        self._with_service(run)

    def test_kimi_uses_screenshot_answer_fallback_when_uia_has_no_text(self) -> None:
        kimi_app = LocalDesktopAppView(
            app_key="desktop-kimi",
            label="Kimi",
            category="utility",
            installed=True,
            running=True,
            window_visible=True,
            launch_supported=True,
            focus_supported=True,
            preferred_entry_path="C:/Users/test/Desktop/Kimi.lnk",
            preferred_entry_kind="shortcut",
            running_processes=("kimi.exe",),
            visible_window_titles=("Kimi",),
            notes=(),
        )
        command = LocalHostCommandResultView(
            action_kind="desktop_app_focus",
            command="focus-window",
            exit_code=0,
            stdout="Focused Kimi window.",
            stderr="",
            workdir=str(self.root),
            elevated=False,
            started_at="2026-05-16T11:05:00+00:00",
            finished_at="2026-05-16T11:05:01+00:00",
        )

        def run(service: DesktopChatOperatorService) -> None:
            with patch(
                "atnos.services.desktop_chat_operator_service.LocalHostControlService.list_desktop_apps",
                return_value=[kimi_app],
            ), patch(
                "atnos.services.desktop_chat_operator_service.LocalHostControlService.focus_desktop_app",
                return_value=command,
            ), patch(
                "atnos.services.desktop_chat_operator_service.LocalHostControlService.focus_desktop_app_input",
                return_value=command,
            ), patch(
                "atnos.services.desktop_chat_operator_service.LocalHostControlService.type_text",
                return_value=command,
            ), patch(
                "atnos.services.desktop_chat_operator_service.LocalHostControlService.press_keys",
                return_value=command,
            ), patch(
                "atnos.services.desktop_chat_operator_service.LocalHostControlService.capture_foreground_window_screenshot",
                return_value=SimpleNamespace(screenshot_path="F:/tmp/kimi-answer.png"),
            ), patch.object(
                DesktopChatOperatorService,
                "_wait_for_kimi_answer",
                return_value=("", ""),
            ), patch.object(
                DesktopChatOperatorService,
                "_extract_answer_from_screenshot",
                return_value="To jest odpowiedz odczytana ze screenshota.",
            ):
                result = service.maybe_handle(content="zapytaj Kimi o test desktop capture")
            self.assertTrue(result.activated)
            self.assertEqual(result.action, "kimi_prompt")
            self.assertIn("To jest odpowiedz odczytana ze screenshota.", result.assistant_content or "")
            metadata = result.metadata["desktop_operator"]
            self.assertEqual(metadata["answer_method"], "screenshot_vision")
            self.assertEqual(metadata["screenshot_path"], "F:/tmp/kimi-answer.png")

        self._with_service(run)

    def test_screenshot_text_extraction_filters_prompt_and_ui_noise(self) -> None:
        text = "\n".join(
            [
                "odpowiedz jednym krotkim zdaniem: test autonomicznego desktop capture",
                "To jest test autonomicznego przechwytywania pulpitu.",
                "Think",
                "Throw me a hard one. I'm ready.",
            ]
        )

        answer = DesktopChatOperatorService._extract_answer_from_screenshot_text(
            screenshot_text=text,
            prompt="odpowiedz jednym krotkim zdaniem: test autonomicznego desktop capture",
        )

        self.assertEqual(answer, "To jest test autonomicznego przechwytywania pulpitu.")

    def test_opens_comfort_via_alias_resolver(self) -> None:
        def run(service: DesktopChatOperatorService) -> None:
            with patch(
                "atnos.services.desktop_chat_operator_service.LocalHostControlService.open_or_resolve_desktop_app",
                return_value={
                    "status": "opened",
                    "query": "Comfort",
                    "app_key": "comfy-desktop",
                    "label": "Comfy Desktop",
                    "resolved_alias": "comfy-desktop",
                    "stdout": "Opened Comfy Desktop",
                    "stderr": "",
                    "exit_code": 0,
                },
            ) as open_app:
                result = service.maybe_handle(content="otwórz aplikację Comfort")
            self.assertTrue(result.activated)
            self.assertEqual(result.action, "desktop_app_open")
            self.assertIn("Comfy Desktop", result.assistant_content or "")
            meta = result.metadata["desktop_operator"]
            self.assertEqual(meta["app_key"], "comfy-desktop")
            self.assertEqual(meta["resolved_alias"], "comfy-desktop")
            open_app.assert_called_once()
            self.assertEqual(open_app.call_args.kwargs.get("query"), "Comfort")

        self._with_service(run)

    def test_web_site_request_is_not_stolen_by_desktop_operator(self) -> None:
        def run(service: DesktopChatOperatorService) -> None:
            with patch(
                "atnos.services.desktop_chat_operator_service.LocalHostControlService.open_or_resolve_desktop_app"
            ) as open_app:
                result = service.maybe_handle(
                    content="otwórz mi stronę Forteca IT i znajdź NIP oraz telefon kontaktowy"
                )
            self.assertFalse(result.activated)
            open_app.assert_not_called()

        self._with_service(run)

    def test_show_capabilities_is_not_stolen_as_app_open(self) -> None:
        def run(service: DesktopChatOperatorService) -> None:
            with patch(
                "atnos.services.desktop_chat_operator_service.LocalHostControlService.open_or_resolve_desktop_app"
            ) as open_app:
                result = service.maybe_handle(content="pokaż twoich umiejętności")
            self.assertFalse(result.activated)
            open_app.assert_not_called()

        self._with_service(run)

    def test_capability_questions_and_agent_work_do_not_launch_desktop_apps(self) -> None:
        phrases=(
            'jesteś w stanie zrobić zadanie w pętli? odpalić kilku workerów?',
            'Czy jesteś w stanie uruchomić kilka agentów?',
            'odpal kilku workerów',
            'uruchom zadanie w pętli',
            'restart serwera',
            'Jak działa polecenie otwórz Obsidian?',
            'Nie uruchom teraz Notatnika',
            'Co oznacza open source?',
            'pokaż możliwości Obsidiana',
        )
        def run(service: DesktopChatOperatorService) -> None:
            for phrase in phrases:
                with self.subTest(phrase=phrase), patch('atnos.services.desktop_chat_operator_service.LocalHostControlService') as host:
                    result=service.maybe_handle(content=phrase)
                    self.assertFalse(result.activated)
                    host.assert_not_called()
        self._with_service(run)

    def test_open_ambiguous_panel_is_not_stolen_as_app_open(self) -> None:
        def run(service: DesktopChatOperatorService) -> None:
            with patch(
                "atnos.services.desktop_chat_operator_service.LocalHostControlService.open_or_resolve_desktop_app"
            ) as open_app:
                result = service.maybe_handle(content="otwórz panel")
            self.assertFalse(result.activated)
            open_app.assert_not_called()

        self._with_service(run)

    def test_install_and_launch_verbs_route_to_desktop_resolver(self) -> None:
        # Avoid Obsidian/Kimi — those use a hardcoded alias path, not query resolver.
        phrases = (
            "zainstaluj Wallpaper Engine",
            "uruchom CapCut",
            "odpal Discord",
            "włącz Steam",
            "launch Cursor",
            "proszę otwórz Notatnik",
            "please open Notepad",
        )

        def run(service: DesktopChatOperatorService) -> None:
            for phrase in phrases:
                with patch(
                    "atnos.services.desktop_chat_operator_service.LocalHostControlService.open_or_resolve_desktop_app",
                    return_value={
                        "status": "opened",
                        "query": "X",
                        "app_key": "demo",
                        "label": "Demo App",
                        "resolved_alias": None,
                        "stdout": "ok",
                        "stderr": "",
                        "exit_code": 0,
                    },
                ) as open_app:
                    result = service.maybe_handle(content=phrase)
                self.assertTrue(result.activated, phrase)
                self.assertEqual(result.action, "desktop_app_open", phrase)
                text = (result.assistant_content or "").casefold()
                self.assertNotIn("midjourney", text)
                self.assertNotIn("dall-e", text)
                self.assertNotIn("moje możliwości ograniczają się", text)
                open_app.assert_called_once()
                self.assertTrue(str(open_app.call_args.kwargs.get("query") or "").strip())

        self._with_service(run)

    def test_not_found_stays_operator_style_without_image_pivot(self) -> None:
        def run(service: DesktopChatOperatorService) -> None:
            with patch(
                "atnos.services.desktop_chat_operator_service.LocalHostControlService.open_or_resolve_desktop_app",
                return_value={
                    "status": "not_found",
                    "query": "NieznanySoft",
                    "resolved_alias": None,
                    "matches": [],
                    "hint": "Nie znaleziono lokalnej aplikacji.",
                },
            ):
                result = service.maybe_handle(content="uruchom NieznanySoft")
            self.assertTrue(result.activated)
            self.assertEqual(result.action, "desktop_app_open")
            text = result.assistant_content or ""
            self.assertIn("Status:", text)
            self.assertIn("Kandydaci:", text)
            self.assertIn(".exe", text.casefold())
            self.assertNotIn("Midjourney", text)
            self.assertNotIn("DALL-E", text)
            self.assertNotIn("nie mogę uruchomić", text.casefold())

        self._with_service(run)


if __name__ == "__main__":
    unittest.main()
