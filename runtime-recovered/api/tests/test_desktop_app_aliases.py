from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import build_test_settings, make_temp_dir

from atnos.services.local_host_control_service import (
    LocalDesktopAppView,
    LocalHostControlService,
    load_persisted_desktop_aliases,
    persist_desktop_alias_file,
)


class DesktopAppAliasTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.settings = build_test_settings(Path(self.temp_dir.name))

    def _host_with_catalog(self, apps: list[LocalDesktopAppView]) -> LocalHostControlService:
        host = object.__new__(LocalHostControlService)
        host.settings = self.settings
        host.session = MagicMock()
        host._build_desktop_app_catalog = lambda: [  # type: ignore[method-assign]
            MagicMock(to_view=lambda app=app: app) for app in apps
        ]
        return host

    def test_query_alias_map_includes_comfort(self) -> None:
        self.assertEqual(
            LocalHostControlService.DESKTOP_APP_QUERY_ALIASES["comfort"],
            "comfy-desktop",
        )
        comfy = next(
            spec
            for spec in LocalHostControlService.KNOWN_DESKTOP_APPS
            if spec.app_key == "comfy-desktop"
        )
        self.assertIn("comfort", comfy.aliases)

    def test_comfort_alias_scores_comfy_desktop(self) -> None:
        apps = [
            LocalDesktopAppView(
                app_key="comfy-desktop",
                label="Comfy Desktop",
                category="ai-video",
                installed=True,
                running=False,
                window_visible=False,
                launch_supported=True,
                focus_supported=False,
                preferred_entry_path=r"E:\fake\Comfy Desktop.exe",
                preferred_entry_kind="exe",
                running_processes=(),
                visible_window_titles=(),
                notes=(),
            ),
            LocalDesktopAppView(
                app_key="capcut",
                label="CapCut",
                category="video",
                installed=True,
                running=False,
                window_visible=False,
                launch_supported=True,
                focus_supported=False,
                preferred_entry_path=r"E:\fake\CapCut.exe",
                preferred_entry_kind="exe",
                running_processes=(),
                visible_window_titles=(),
                notes=(),
            ),
        ]
        host = self._host_with_catalog(apps)
        result = host.open_or_resolve_desktop_app(query="Comfort", list_only=True)
        self.assertEqual(result["resolved_alias"], "comfy-desktop")
        self.assertEqual(result["matches"][0]["app_key"], "comfy-desktop")
        self.assertEqual(result["matches"][0]["score"], 100)

    def test_ambiguous_panel_query_is_blocked(self) -> None:
        host = self._host_with_catalog([])
        result = host.open_or_resolve_desktop_app(query="panel", list_only=False)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "system_panel_ambiguous")

    def test_score_start_menu_rejects_short_substring(self) -> None:
        self.assertEqual(
            LocalHostControlService._score_start_menu_name(
                name="NVIDIA Control Panel",
                query="panel",
            ),
            0,
        )
        self.assertEqual(
            LocalHostControlService._score_start_menu_name(
                name="Panel sterowania",
                query="panel",
            ),
            0,
        )
        self.assertEqual(
            LocalHostControlService._score_start_menu_name(
                name="Panel sterowania",
                query="panel sterowania",
            ),
            100,
        )
        self.assertGreaterEqual(
            LocalHostControlService._score_start_menu_name(
                name="CapCut",
                query="capcut",
            ),
            100,
        )

    def test_blocked_system_panel_result_detection(self) -> None:
        self.assertTrue(
            LocalHostControlService._is_blocked_system_panel_result(
                "Panel sterowania",
                app_id="Microsoft.Windows.ControlPanel",
            )
        )
        self.assertFalse(
            LocalHostControlService._is_blocked_system_panel_result(
                "CapCut",
                app_id="CapCut",
            )
        )

    def test_typo_alias_capkut_and_obsidain(self) -> None:
        self.assertEqual(
            LocalHostControlService.DESKTOP_APP_QUERY_ALIASES["capkut"],
            "capcut",
        )
        self.assertEqual(
            LocalHostControlService.DESKTOP_APP_QUERY_ALIASES["obsidain"],
            "obsidian",
        )

    def test_fuzzy_resolves_near_miss_label(self) -> None:
        apps = [
            LocalDesktopAppView(
                app_key="obsidian",
                label="Obsidian",
                category="knowledge",
                installed=True,
                running=False,
                window_visible=False,
                launch_supported=True,
                focus_supported=False,
                preferred_entry_path=r"E:\fake\Obsidian.exe",
                preferred_entry_kind="exe",
                running_processes=(),
                visible_window_titles=(),
                notes=(),
            ),
            LocalDesktopAppView(
                app_key="capcut",
                label="CapCut",
                category="video",
                installed=True,
                running=False,
                window_visible=False,
                launch_supported=True,
                focus_supported=False,
                preferred_entry_path=r"E:\fake\CapCut.exe",
                preferred_entry_kind="exe",
                running_processes=(),
                visible_window_titles=(),
                notes=(),
            ),
        ]
        host = self._host_with_catalog(apps)
        # Intentionally not in static alias map — pure fuzzy on label.
        result = host.open_or_resolve_desktop_app(query="Obsidan", list_only=True)
        self.assertGreaterEqual(result["matches"][0]["score"], 82)
        self.assertEqual(result["matches"][0]["app_key"], "obsidian")
        self.assertEqual(result["matches"][0]["match_via"], "fuzzy")

    def test_persisted_desktop_alias_round_trip(self) -> None:
        path = persist_desktop_alias_file(
            query="komfort",
            app_key="comfy-desktop",
            settings=self.settings,
        )
        self.assertIsNotNone(path)
        loaded = load_persisted_desktop_aliases(self.settings)
        self.assertEqual(loaded.get("komfort"), "comfy-desktop")

        apps = [
            LocalDesktopAppView(
                app_key="comfy-desktop",
                label="Comfy Desktop",
                category="ai-video",
                installed=True,
                running=False,
                window_visible=False,
                launch_supported=True,
                focus_supported=False,
                preferred_entry_path=r"E:\fake\Comfy Desktop.exe",
                preferred_entry_kind="exe",
                running_processes=(),
                visible_window_titles=(),
                notes=(),
            ),
        ]
        host = self._host_with_catalog(apps)
        result = host.open_or_resolve_desktop_app(query="komfort", list_only=True)
        self.assertEqual(result["resolved_alias"], "comfy-desktop")
        self.assertEqual(result["matches"][0]["app_key"], "comfy-desktop")
        self.assertEqual(result["matches"][0]["score"], 100)

    def test_fuzzy_score_rejects_short_and_distant(self) -> None:
        self.assertEqual(
            LocalHostControlService._fuzzy_match_score("abc", ["obsidian"]),
            0,
        )
        self.assertEqual(
            LocalHostControlService._fuzzy_match_score("zzzzzz", ["obsidian"]),
            0,
        )

    def test_wallpaper_engine_alias_and_steam_launch_supported(self) -> None:
        self.assertEqual(
            LocalHostControlService.DESKTOP_APP_QUERY_ALIASES["wallpaper engine"],
            "wallpaper-engine",
        )
        apps = [
            LocalDesktopAppView(
                app_key="wallpaper-engine",
                label="Wallpaper Engine",
                category="desktop",
                installed=True,
                running=False,
                window_visible=False,
                launch_supported=True,
                focus_supported=False,
                preferred_entry_path=None,
                preferred_entry_kind="steam",
                running_processes=(),
                visible_window_titles=(),
                notes=("steam_app_id=431960",),
            ),
        ]
        host = self._host_with_catalog(apps)
        listed = host.open_or_resolve_desktop_app(query="wallpaper engine", list_only=True)
        self.assertEqual(listed["resolved_alias"], "wallpaper-engine")
        self.assertEqual(listed["matches"][0]["app_key"], "wallpaper-engine")
        self.assertEqual(listed["matches"][0]["score"], 100)

        with patch.object(
            host,
            "open_desktop_app",
            return_value=MagicMock(
                exit_code=0,
                stdout="Opened via Steam",
                stderr="",
            ),
        ) as opened:
            result = host.open_or_resolve_desktop_app(query="tapeta engine", list_only=False)
        self.assertEqual(result["status"], "opened")
        self.assertEqual(result["app_key"], "wallpaper-engine")
        opened.assert_called_once_with("wallpaper-engine")


if __name__ == "__main__":
    unittest.main()
