from __future__ import annotations

from pathlib import Path
import json
import sys
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import SecretStr

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import bootstrap_test_app, create_test_app, make_temp_dir
from atnos.services.desktop_browser_observer_service import BrowserPageLearningView
from atnos.services.desktop_browser_observer_service import DesktopBrowserObservationView
from atnos.services.desktop_browser_observer_service import InstagramFeedLearningView
from atnos.services.desktop_browser_observer_service import InstagramResearchCycleView
from atnos.services.browser_access_diagnostics_service import (
    BrowserAccessDiagnosticsService,
    ChromeBrowserAccessDiagnosticsView,
)
from atnos.services.browser_runtime_service import ChromeBridgeLaunchResult
from atnos.services.local_host_control_service import (
    LocalHostBrowserRecipeResultView,
    LocalHostCommandResultView,
    LocalHostControlService,
    LocalHostWindowMatchView,
)
from atnos.services.local_host_control_service import LocalDesktopAppView
from atnos.services.local_host_control_service import _ResolvedDesktopAppSpec
from atnos.services.screen_capture_service import ScreenArtifact


class LocalHostControlApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.app = create_test_app(self.root)
        self.addCleanup(self.app.state.database.dispose)
        self.capability_token = "focused-test-local-capability"
        self.app.state.settings.local_host_capability_token = SecretStr(self.capability_token)
        self.app.state.settings.local_host_capability_expires_at = int(time.time()) + 3600
        self.app.state.settings.local_host_raw_powershell_enabled = True
        self.capability_headers = {
            "X-Atnos-Local-Capability": self.capability_token,
        }

    def test_local_host_capabilities_report_windows_executor_state(self) -> None:
        with TestClient(self.app) as client:
            response = client.get("/local-host/capabilities")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("host_label", payload)
        self.assertIn("current_user", payload)
        self.assertIn("command_log_path", payload)
        self.assertTrue(str(payload["command_log_path"]).endswith("commands.log"))
        self.assertIn("desktop_input_available", payload)
        self.assertIn("browser_recipe_available", payload)

    def test_local_host_run_executes_powershell_and_writes_audit_log(self) -> None:
        with TestClient(self.app) as client:
            response = client.post(
                "/local-host/run",
                headers=self.capability_headers,
                json={
                    "command": "Write-Output 'local-host-ok'",
                    "timeout_s": 30,
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["action_kind"], "powershell")
        self.assertEqual(payload["exit_code"], 0)
        self.assertIn("local-host-ok", payload["stdout"])

        log_path = self.root / "data" / "local-host-control" / "commands.log"
        self.assertTrue(log_path.exists())
        self.assertIn("local-host-ok", log_path.read_text(encoding="utf-8"))

    def test_local_host_audit_log_redacts_nested_computer_control_secrets(self) -> None:
        service = LocalHostControlService.__new__(LocalHostControlService)
        service.command_log_path = self.root / "data" / "local-host-control" / "commands.log"
        synthetic_secret = "sk-test-local-host-boundary-1234567890"

        service._append_log(
            {
                "action_kind": "browser_recipe",
                "command": f"provider --auth_token={synthetic_secret}",
                "steps": [
                    {
                        "action": "type",
                        "text": synthetic_secret,
                        "metadata": {
                            "clientSecret": synthetic_secret,
                            "token_count": 12,
                            "auth_method": "oauth2",
                        },
                    }
                ],
                "safe": "visible",
            }
        )

        persisted = service.command_log_path.read_text(encoding="utf-8")
        record = json.loads(persisted)
        self.assertNotIn(synthetic_secret, persisted)
        self.assertEqual(record["steps"][0]["metadata"]["clientSecret"], "[REDACTED]")
        self.assertEqual(record["steps"][0]["metadata"]["token_count"], 12)
        self.assertEqual(record["steps"][0]["metadata"]["auth_method"], "oauth2")
        self.assertEqual(record["safe"], "visible")

    def test_local_host_run_denies_missing_or_invalid_capability(self) -> None:
        with TestClient(self.app) as client:
            missing = client.post(
                "/local-host/run",
                json={"command": "Write-Output 'must-not-run'"},
            )
            invalid = client.post(
                "/local-host/run",
                headers={"X-Atnos-Local-Capability": "wrong-token"},
                json={"command": "Write-Output 'must-not-run'"},
            )

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(invalid.status_code, 401)
        self.assertEqual(
            missing.headers.get("www-authenticate"),
            "AtnosLocalCapability",
        )
        self.assertFalse(
            (self.root / "data" / "local-host-control" / "commands.log").exists()
        )

    def test_local_host_run_denies_expired_capability(self) -> None:
        self.app.state.settings.local_host_capability_expires_at = int(time.time()) - 1

        with TestClient(self.app) as client:
            response = client.post(
                "/local-host/run",
                headers=self.capability_headers,
                json={"command": "Write-Output 'must-not-run'"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("expired", response.json()["detail"].lower())

    def test_local_host_capability_session_reports_expiry_without_token_value(self) -> None:
        expected_expiry = self.app.state.settings.local_host_capability_expires_at

        with TestClient(self.app) as client:
            response = client.get(
                "/local-host/capability-session",
                headers=self.capability_headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "authorized", "expires_at": expected_expiry},
        )
        self.assertNotIn(self.capability_token, response.text)

    def test_local_host_run_rejects_workdir_outside_allowlist(self) -> None:
        outside_workdir = self.root.parent / "outside-local-host-allowlist"

        with TestClient(self.app) as client:
            response = client.post(
                "/local-host/run",
                headers=self.capability_headers,
                json={
                    "command": "Write-Output 'must-not-run'",
                    "workdir": str(outside_workdir),
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("app_root or data_dir", response.json()["detail"])

    def test_local_host_run_is_disabled_in_production_even_with_capability(self) -> None:
        self.app.state.settings.app_env = "production"

        with TestClient(self.app) as client:
            response = client.post(
                "/local-host/run",
                headers=self.capability_headers,
                json={"command": "Write-Output 'must-not-run'"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Raw PowerShell is disabled in production.")

    def test_settings_control_center_includes_local_host_snapshot(self) -> None:
        with TestClient(self.app) as client:
            response = client.get("/settings/control-center")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("local_host", payload)
        self.assertIn("execution_enabled", payload["local_host"])
        self.assertIn("chrome_diagnostics", payload["local_host"])
        self.assertIn("desktop_apps", payload["local_host"])
        self.assertEqual(payload["omega_protocol"]["mode"], "fast-settings")
        self.assertIn("skills_dashboard", payload)
        self.assertIn("summary", payload["skills_dashboard"])
        voice_keys = {item["voice_key"] for item in payload["voices"]}
        self.assertIn("pl_maja", voice_keys)
        self.assertIn("pl_piotr", voice_keys)

    def test_local_host_input_dispatches_to_desktop_action(self) -> None:
        mocked_result = LocalHostCommandResultView(
            action_kind="mouse_click",
            command="mouse.click(button='left', clicks=1, x=120, y=240)",
            exit_code=0,
            stdout="Clicked left mouse button 1 time(s) at (120, 240).",
            stderr="",
            workdir=str(self.root),
            elevated=False,
            started_at="2026-05-11T22:00:00+00:00",
            finished_at="2026-05-11T22:00:01+00:00",
        )
        with patch(
            "atnos.api.routes.local_host.LocalHostControlService.click_mouse",
            return_value=mocked_result,
        ):
            with TestClient(self.app) as client:
                response = client.post(
                    "/local-host/input",
                    headers=self.capability_headers,
                    json={
                        "action": "click_mouse",
                        "button": "left",
                        "clicks": 1,
                        "x": 120,
                        "y": 240,
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["action_kind"], "mouse_click")
        self.assertIn("(120, 240)", payload["stdout"])

    def test_local_host_input_denies_missing_capability_before_dispatch(self) -> None:
        with patch(
            "atnos.api.routes.local_host.LocalHostControlService.click_mouse",
        ) as click_mouse:
            with TestClient(self.app) as client:
                response = client.post(
                    "/local-host/input",
                    json={"action": "click_mouse"},
                )

        self.assertEqual(response.status_code, 401)
        click_mouse.assert_not_called()

    def test_local_host_side_effect_routes_deny_missing_capability(self) -> None:
        requests = (
            ("/local-host/apps/obsidian/open", None),
            ("/local-host/browser/run", {"steps": []}),
            ("/local-host/browser/chrome-bridge/start", {}),
            ("/local-host/screen/screenshot", {"label": "denied"}),
            ("/local-host/screen/record", {"label": "denied", "duration_seconds": 1}),
        )

        with TestClient(self.app) as client:
            responses = [client.post(path, json=payload) for path, payload in requests]

        self.assertEqual([response.status_code for response in responses], [401] * len(requests))

    def test_local_host_input_rejects_operations_outside_allowlist(self) -> None:
        with TestClient(self.app) as client:
            response = client.post(
                "/local-host/input",
                headers=self.capability_headers,
                json={"action": "launch_process"},
            )

        self.assertEqual(response.status_code, 422)

    def test_local_host_screen_screenshot_returns_artifact(self) -> None:
        screenshot_path = self.root / "data" / "visual-qa" / "captures" / "desktop.png"
        metadata_path = screenshot_path.with_suffix(".png.json")
        artifact = ScreenArtifact(
            kind="screenshot",
            path=screenshot_path,
            metadata_path=metadata_path,
            label="desktop-check",
            created_at="2026-06-04T10:00:00",
            duration_seconds=None,
        )

        with patch("atnos.api.routes.local_host.ScreenCaptureService") as service_cls:
            service_cls.return_value.capture_screenshot.return_value = artifact
            with TestClient(self.app) as client:
                response = client.post(
                    "/local-host/screen/screenshot",
                    headers=self.capability_headers,
                    json={"label": "desktop-check"},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["kind"], "screenshot")
        self.assertEqual(payload["path"], str(screenshot_path))
        service_cls.return_value.capture_screenshot.assert_called_once_with(label="desktop-check")

    def test_local_host_screen_record_returns_artifact(self) -> None:
        recording_path = self.root / "data" / "visual-qa" / "recordings" / "desktop.mp4"
        metadata_path = recording_path.with_suffix(".mp4.json")
        artifact = ScreenArtifact(
            kind="screen_recording",
            path=recording_path,
            metadata_path=metadata_path,
            label="desktop-record",
            created_at="2026-06-04T10:00:00",
            duration_seconds=5,
        )

        with patch("atnos.api.routes.local_host.ScreenCaptureService") as service_cls:
            service_cls.return_value.record_screen.return_value = artifact
            with TestClient(self.app) as client:
                response = client.post(
                    "/local-host/screen/record",
                    headers=self.capability_headers,
                    json={"label": "desktop-record", "duration_seconds": 5},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["kind"], "screen_recording")
        self.assertEqual(payload["duration_seconds"], 5)
        service_cls.return_value.record_screen.assert_called_once_with(
            label="desktop-record",
            duration_seconds=5,
        )

    def test_local_host_browser_recipe_returns_structured_result(self) -> None:
        mocked_result = LocalHostBrowserRecipeResultView(
            action_kind="browser_recipe",
            ok=True,
            final_url="https://example.com/",
            results=[{"stepIndex": 0, "type": "navigate", "ok": True}],
            artifacts={"final_screenshot": "F:/AtnosSystem/AppData/Roaming/Atnos/data/browser-final.png"},
            started_at="2026-05-11T22:00:00+00:00",
            finished_at="2026-05-11T22:00:02+00:00",
        )
        with patch(
            "atnos.api.routes.local_host.LocalHostControlService.run_browser_recipe",
            return_value=mocked_result,
        ):
            with TestClient(self.app) as client:
                response = client.post(
                    "/local-host/browser/run",
                    headers=self.capability_headers,
                    json={
                        "headless": False,
                        "steps": [
                            {"type": "navigate", "url": "https://example.com/"},
                        ],
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["action_kind"], "browser_recipe")
        self.assertEqual(payload["final_url"], "https://example.com/")

    def test_local_host_instagram_observer_returns_structured_reel_summaries(self) -> None:
        mocked_result = DesktopBrowserObservationView(
            action_kind="browser_observe_instagram",
            ok=True,
            target_url="https://www.instagram.com/reels/",
            browser_process_name="opera.exe",
            title_hint="instagram",
            started_at="2026-05-12T19:00:00+00:00",
            finished_at="2026-05-12T19:00:10+00:00",
            items=[
                {
                    "index": 1,
                    "screenshot_path": "F:/Apps/Atnos/data/browser-downloads/desktop-captures/instagram-1.png",
                    "window_title": "Instagram",
                    "provider_key": "ollama-local",
                    "model_key": "ollama-local/vision-default",
                    "summary": "Krotka rolka o modzie ulicznej.",
                    "error": None,
                }
            ],
        )
        with patch(
            "atnos.api.routes.local_host.DesktopBrowserObserverService.observe_instagram_reels",
            return_value=mocked_result,
        ):
            with TestClient(self.app) as client:
                response = client.post(
                    "/local-host/browser/observe-instagram",
                    headers=self.capability_headers,
                    json={
                        "count": 1,
                        "browser_process_name": "opera.exe",
                        "title_hint": "instagram",
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["action_kind"], "browser_observe_instagram")
        self.assertEqual(len(payload["items"]), 1)
        self.assertIn("modzie ulicznej", payload["items"][0]["summary"])

    def test_local_host_instagram_learning_returns_note_paths(self) -> None:
        mocked_result = InstagramFeedLearningView(
            action_kind="browser_learn_instagram",
            ok=True,
            target_url="https://www.instagram.com/reels/",
            browser_process_name="opera.exe",
            title_hint="instagram",
            note_id="instagram-instagram-reproducible-content-scan-20260512-010101",
            curated_path="F:/AtnosLibrary/curated/instagram/test.md",
            knowledge_path="F:/Apps/Atnos/runtime-recovered/api/knowledge/imported/instagram/test.md",
            vault_note_path="Atnos/Research/BrowserLearning/instagram/test.md",
            summary="Najlatwiej odtwarzalne sa proste formaty lifestyle i teaser.",
            started_at="2026-05-12T01:01:01+00:00",
            finished_at="2026-05-12T01:01:20+00:00",
            items=[
                {
                    "index": 1,
                    "source_excerpt": "Zdjęcie profilowe user123",
                    "analysis": "Da się zrobić jako zwykła modelka albo AI modelka.",
                    "provider_key": "ollama-local",
                    "model_key": "ollama-local/document-default",
                    "error": None,
                }
            ],
        )
        with patch(
            "atnos.api.routes.local_host.DesktopBrowserObserverService.learn_reproducible_instagram_formats",
            return_value=mocked_result,
        ):
            with TestClient(self.app) as client:
                response = client.post(
                    "/local-host/browser/learn-instagram",
                    headers=self.capability_headers,
                    json={
                        "count": 3,
                        "browser_process_name": "opera.exe",
                        "title_hint": "instagram",
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["action_kind"], "browser_learn_instagram")
        self.assertIn("curated/instagram", payload["curated_path"])
        self.assertIn("Atnos/Research/BrowserLearning/instagram", payload["vault_note_path"])
        self.assertIn("Najlatwiej odtwarzalne", payload["summary"])

    def test_local_host_instagram_research_returns_targeted_profiles(self) -> None:
        mocked_result = InstagramResearchCycleView(
            action_kind="browser_learn_instagram_targeted",
            ok=True,
            profile_count=2,
            note_id="instagram-targeted-research-cycle-20260512-020202",
            curated_path="F:/AtnosLibrary/curated/instagram/cycle.md",
            knowledge_path="F:/Apps/Atnos/runtime-recovered/api/knowledge/imported/instagram/cycle.md",
            vault_note_path="Atnos/Research/BrowserLearning/instagram/cycle.md",
            summary="Atnos ma monitorowac AI modelki, zwykle modelki i nowe AI workflow.",
            started_at="2026-05-12T02:02:02+00:00",
            finished_at="2026-05-12T02:03:00+00:00",
            profiles=[
                {
                    "profile_key": "ai-modelki",
                    "label": "AI modelki",
                    "lane": "ai-model",
                    "search_query": "ai model fashion reel",
                    "target_url": "https://www.instagram.com/explore/search/keyword/?q=ai+model+fashion+reel",
                    "note_id": "note-1",
                    "curated_path": "F:/AtnosLibrary/curated/instagram/note-1.md",
                    "knowledge_path": "F:/Apps/Atnos/runtime-recovered/api/knowledge/imported/instagram/note-1.md",
                    "vault_note_path": "Atnos/Research/BrowserLearning/instagram/note-1.md",
                    "summary": "Najmocniejsze sa beauty i teaser reels.",
                    "items": [],
                    "error": None,
                },
                {
                    "profile_key": "nowe-ai",
                    "label": "Nowe AI do podlaczenia",
                    "lane": "ai-tooling",
                    "search_query": "ai video workflow runway kling pika",
                    "target_url": "https://www.instagram.com/explore/search/keyword/?q=ai+video+workflow+runway+kling+pika",
                    "note_id": "note-2",
                    "curated_path": "F:/AtnosLibrary/curated/instagram/note-2.md",
                    "knowledge_path": "F:/Apps/Atnos/runtime-recovered/api/knowledge/imported/instagram/note-2.md",
                    "vault_note_path": "Atnos/Research/BrowserLearning/instagram/note-2.md",
                    "summary": "Warto sledzic workflow video i lip-sync.",
                    "items": [],
                    "error": None,
                },
            ],
        )
        with patch(
            "atnos.api.routes.local_host.DesktopBrowserObserverService.learn_targeted_instagram_strategy",
            return_value=mocked_result,
        ):
            with TestClient(self.app) as client:
                response = client.post(
                    "/local-host/browser/research-instagram",
                    headers=self.capability_headers,
                    json={
                        "count_per_profile": 2,
                        "profile_keys": ["ai-modelki", "nowe-ai"],
                        "browser_process_name": "opera.exe",
                        "title_hint": "instagram",
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["action_kind"], "browser_learn_instagram_targeted")
        self.assertEqual(payload["profile_count"], 2)
        self.assertEqual(len(payload["profiles"]), 2)
        self.assertIn("AI modelki", payload["profiles"][0]["label"])
        self.assertIn("Atnos/Research/BrowserLearning/instagram", payload["vault_note_path"])

    def test_local_host_browser_page_learning_returns_obsidian_note_path(self) -> None:
        mocked_result = BrowserPageLearningView(
            action_kind="browser_learn_page",
            ok=True,
            target_url="https://example.com/ai-tool",
            final_url="https://example.com/ai-tool",
            selector="#response",
            note_id="general-browser-learning-example-com-20260516-010101",
            curated_path="F:/AtnosLibrary/curated/general/browser-learning.md",
            knowledge_path="F:/Apps/Atnos/runtime-recovered/api/knowledge/imported/general/browser-learning.md",
            vault_note_path="Atnos/Capture/browser-learning.md",
            extracted_text_excerpt="To narzedzie zwraca gotowy plan promptu i liste ograniczen.",
            analysis="Najwazniejszy wniosek: Atnos moze uzywac tego narzedzia do szybkiego draftu promptow.",
            provider_key="ollama-local",
            model_key="ollama-local/document-default",
            runtime_fallback="cdp_target_get_targets; chrome_bridge_already_live",
            started_at="2026-05-16T10:00:00+00:00",
            finished_at="2026-05-16T10:00:12+00:00",
            browser_artifacts={"save_target": "obsidian"},
        )
        with patch(
            "atnos.api.routes.local_host.DesktopBrowserObserverService.learn_from_webpage",
            return_value=mocked_result,
        ):
            with TestClient(self.app) as client:
                response = client.post(
                    "/local-host/browser/learn-page",
                    headers=self.capability_headers,
                    json={
                        "target_url": "https://example.com/ai-tool",
                        "selector": "#response",
                        "steps_before_extract": [
                            {"type": "type", "selector": "#prompt", "text": "Napisz mi prompt do researchu"},
                            {"type": "click", "selector": "#send"},
                            {"type": "verify_state", "selector": "#response"},
                        ],
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["action_kind"], "browser_learn_page")
        self.assertEqual(payload["selector"], "#response")
        self.assertIn("Atnos/Capture", payload["vault_note_path"])
        self.assertIn("draftu promptow", payload["analysis"])

    def test_local_host_browser_page_learning_accepts_missing_target_url_for_active_cdp_tab(self) -> None:
        mocked_result = BrowserPageLearningView(
            action_kind="browser_learn_page",
            ok=True,
            target_url="https://example.com/active",
            final_url="https://example.com/active",
            selector="body",
            note_id="general-browser-learning-active-20260516-010101",
            curated_path="F:/AtnosLibrary/curated/general/browser-learning.md",
            knowledge_path="F:/Apps/Atnos/runtime-recovered/api/knowledge/imported/general/browser-learning.md",
            vault_note_path="Atnos/Capture/browser-learning.md",
            extracted_text_excerpt="Aktywna karta Chrome ma czytelna tresc.",
            analysis="Najwazniejszy wniosek: aktywna karta zostala przeanalizowana przez CDP.",
            provider_key="ollama-local",
            model_key="ollama-local/atnos-core",
            runtime_fallback="cdp_target_get_targets; chrome_bridge_already_live",
            started_at="2026-05-16T10:00:00+00:00",
            finished_at="2026-05-16T10:00:12+00:00",
            browser_artifacts={"save_target": "obsidian"},
        )
        with patch(
            "atnos.api.routes.local_host.DesktopBrowserObserverService.learn_from_webpage",
            return_value=mocked_result,
        ) as learn_from_webpage:
            with TestClient(self.app) as client:
                response = client.post(
                    "/local-host/browser/learn-page",
                    headers=self.capability_headers,
                    json={},
                )

        self.assertEqual(response.status_code, 200)
        learn_from_webpage.assert_called_once()
        self.assertIsNone(learn_from_webpage.call_args.kwargs["target_url"])
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["target_url"], "https://example.com/active")

    def test_local_host_chrome_diagnostics_returns_structured_browser_access(self) -> None:
        mocked_result = ChromeBrowserAccessDiagnosticsView(
            browser_running=True,
            process_count=4,
            mode="session_fallback",
            summary="Atnos widzi tylko ostatni dostepny snapshot sesji Chrome, nie pelne live taby.",
            reasons=(
                "Chrome nie wystawia live DevTools dla biezacej sesji.",
                "Atnos czyta ostatni dostepny snapshot sesji z lokalnych plikow Chrome.",
            ),
            remote_debugging_port=None,
            visible_windows=(
                {
                    "hwnd": 101,
                    "title": "GitHub - Google Chrome",
                    "process_id": 1234,
                    "process_name": "chrome.exe",
                    "is_foreground": True,
                },
            ),
            tabs=(
                {
                    "title": None,
                    "url": "https://github.com/",
                    "source": "session",
                },
            ),
            detected_apps=("GitHub",),
            session_source_file="C:/Users/test/AppData/Local/Google/Chrome/User Data/Default/Sessions/Session_1",
            checked_at="2026-05-16T10:00:00+00:00",
        )
        with patch(
            "atnos.api.routes.local_host.BrowserAccessDiagnosticsService.diagnose_chrome",
            return_value=mocked_result,
        ):
            with TestClient(self.app) as client:
                response = client.get("/local-host/browser/chrome-diagnostics?tab_limit=10")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["mode"], "session_fallback")
        self.assertTrue(payload["browser_running"])
        self.assertEqual(payload["detected_apps"], ["GitHub"])
        self.assertEqual(payload["tabs"][0]["url"], "https://github.com/")

    def test_local_host_chrome_bridge_start_returns_live_diagnostics(self) -> None:
        launch_result = ChromeBridgeLaunchResult(
            ok=True,
            status="restarted_existing",
            port=9222,
            browser_executable=Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
            using_default_profile=True,
            restarted_existing=True,
            command=(
                "C:/Program Files/Google/Chrome/Application/chrome.exe",
                "--remote-debugging-port=9222",
            ),
            notes=("Restarted the existing Chrome session to expose a live DevTools bridge.",),
            started_at="2026-05-16T10:05:00+00:00",
        )
        diagnostics = ChromeBrowserAccessDiagnosticsView(
            browser_running=True,
            process_count=6,
            mode="devtools_live",
            summary="Atnos ma live odczyt kart przez Chrome DevTools.",
            reasons=("Chrome jest uruchomiony z --remote-debugging-port.",),
            remote_debugging_port=9222,
            visible_windows=(),
            tabs=(
                {
                    "title": "GitHub",
                    "url": "https://github.com/",
                    "source": "devtools",
                },
            ),
            detected_apps=("GitHub",),
            session_source_file=None,
            checked_at="2026-05-16T10:05:10+00:00",
        )
        with patch(
            "atnos.api.routes.local_host.BrowserRuntimeService.ensure_chrome_devtools_bridge",
            return_value=launch_result,
        ), patch(
            "atnos.api.routes.local_host.BrowserAccessDiagnosticsService.diagnose_chrome",
            return_value=diagnostics,
        ):
            with TestClient(self.app) as client:
                response = client.post(
                    "/local-host/browser/chrome-bridge/start",
                    headers=self.capability_headers,
                    json={"port": 9222, "restart_existing": True, "timeout_s": 20},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "restarted_existing")
        self.assertEqual(payload["diagnostics"]["mode"], "devtools_live")
        self.assertEqual(payload["diagnostics"]["tabs"][0]["url"], "https://github.com/")

    def test_local_host_desktop_apps_returns_structured_catalog(self) -> None:
        mocked_apps = [
            LocalDesktopAppView(
                app_key="obsidian",
                label="Obsidian",
                category="knowledge",
                installed=True,
                running=False,
                window_visible=False,
                launch_supported=True,
                focus_supported=False,
                preferred_entry_path="C:/Users/test/Desktop/Obsidian.lnk",
                preferred_entry_kind="shortcut",
                running_processes=(),
                visible_window_titles=(),
                notes=("entry=shortcut",),
            )
        ]
        with patch(
            "atnos.api.routes.local_host.LocalHostControlService.list_desktop_apps",
            return_value=mocked_apps,
        ):
            with TestClient(self.app) as client:
                response = client.get("/local-host/apps")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["app_key"], "obsidian")
        self.assertTrue(payload[0]["launch_supported"])

    def test_settings_operator_profile_preserves_confirmation_boundaries(self) -> None:
        with TestClient(self.app) as client:
            response = client.post("/settings/control-center/runtime/full-access")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["policy"]["allow_local_file_read"])
        self.assertTrue(payload["policy"]["allow_local_file_write"])
        self.assertTrue(payload["runtime"]["browser_control_enabled"])
        self.assertTrue(payload["runtime"]["external_actions_enabled"])
        self.assertFalse(payload["runtime"]["browser_allow_any_url"])
        self.assertFalse(payload["runtime"]["login_automation_enabled"])
        self.assertFalse(payload["runtime"]["two_factor_automation_enabled"])
        self.assertFalse(payload["runtime"]["publish_automation_enabled"])
        self.assertFalse(payload["runtime"]["public_upload_enabled"])
        self.assertEqual(payload["runtime"]["autonomy_mode"], "supervised")
        self.assertEqual(payload["policy"]["automation_level"], "assisted")
        self.assertEqual(payload["policy"]["verification_mode"], "strict")

    def test_local_host_can_open_desktop_app(self) -> None:
        mocked_result = LocalHostCommandResultView(
            action_kind="desktop_app_open",
            command="Start-Process -FilePath 'C:/Users/test/Desktop/Obsidian.lnk'",
            exit_code=0,
            stdout="Opened Obsidian via shortcut.",
            stderr="",
            workdir=str(self.root),
            elevated=False,
            started_at="2026-05-16T10:00:00+00:00",
            finished_at="2026-05-16T10:00:01+00:00",
        )
        with patch(
            "atnos.api.routes.local_host.LocalHostControlService.open_desktop_app",
            return_value=mocked_result,
        ):
            with TestClient(self.app) as client:
                response = client.post(
                    "/local-host/apps/obsidian/open",
                    headers=self.capability_headers,
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["action_kind"], "desktop_app_open")
        self.assertIn("Obsidian", payload["stdout"])

    def test_open_desktop_app_uses_windows_startfile_for_local_apps(self) -> None:
        mocked_app = LocalDesktopAppView(
            app_key="obsidian",
            label="Obsidian",
            category="knowledge",
            installed=True,
            running=False,
            window_visible=False,
            launch_supported=True,
            focus_supported=False,
            preferred_entry_path="C:/Users/test/Desktop/Obsidian.lnk",
            preferred_entry_kind="shortcut",
            running_processes=(),
            visible_window_titles=(),
            notes=("entry=shortcut",),
        )
        bootstrap_test_app(self.app)
        fake_window = LocalHostWindowMatchView(
            hwnd=101,
            title="Obsidian",
            process_id=4242,
            process_name="obsidian.exe",
            is_foreground=False,
        )
        with self.app.state.database.session_scope() as session:
            service = LocalHostControlService(session, settings=self.app.state.settings)
            with patch.object(service, "list_desktop_apps", return_value=[mocked_app]), patch.object(
                service,
                "_resolve_app_entry",
                return_value=(Path("C:/Users/test/Desktop/Obsidian.lnk"), "shortcut"),
            ), patch.object(
                service,
                "_wait_for_app_window",
                return_value=fake_window,
            ), patch.object(
                service,
                "_force_foreground_window",
                return_value=True,
            ), patch("os.startfile", create=True) as mocked_startfile:
                result = service.open_desktop_app("obsidian")

        mocked_startfile.assert_called_once()
        launched_path = Path(mocked_startfile.call_args.args[0])
        self.assertEqual(launched_path, Path("C:/Users/test/Desktop/Obsidian.lnk"))
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Opened Obsidian via shortcut.", result.stdout)

    def test_browser_access_diagnostics_detects_known_apps_from_session_urls(self) -> None:
        detected = BrowserAccessDiagnosticsService._detect_apps(
            [
                "https://www.instagram.com/",
                "https://github.com/",
                "https://www.youtube.com/",
                "https://mail.google.com/mail/u/0/",
            ]
        )

        self.assertEqual(detected, ("Instagram", "GitHub", "YouTube", "Gmail"))

    def test_vscode_window_match_does_not_treat_codex_as_visual_studio_code(self) -> None:
        vscode_spec = next(
            spec for spec in LocalHostControlService.KNOWN_DESKTOP_APPS if spec.app_key == "vscode"
        )
        codex_window = LocalHostWindowMatchView(
            hwnd=101,
            title="Codex",
            process_id=4242,
            process_name="codex.exe",
            is_foreground=True,
        )

        matched = LocalHostControlService._window_matches_app(codex_window, vscode_spec)

        self.assertFalse(matched)

    def test_known_desktop_apps_include_user_ai_and_production_tools(self) -> None:
        app_keys = {spec.app_key for spec in LocalHostControlService.KNOWN_DESKTOP_APPS}

        self.assertIn("kimi", app_keys)
        self.assertIn("comfy-desktop", app_keys)
        self.assertIn("atnos-desktop", app_keys)
        self.assertIn("capcut", app_keys)
        self.assertIn("github-desktop", app_keys)
        self.assertIn("docker-desktop", app_keys)
        self.assertIn("git-bash", app_keys)

    def test_atnos_desktop_prefers_real_exe_paths_over_shortcut_patterns(self) -> None:
        atnos_spec = next(
            spec for spec in LocalHostControlService.KNOWN_DESKTOP_APPS if spec.app_key == "atnos-desktop"
        )

        self.assertTrue(any("Atnos Desktop.exe" in pattern for pattern in atnos_spec.path_patterns))
        self.assertFalse(any(pattern.lower().endswith(".lnk") for pattern in atnos_spec.path_patterns))
        self.assertIn("ATNOS.lnk", atnos_spec.shortcut_names)

    def test_resolve_app_entry_skips_broken_shortcut_targets(self) -> None:
        bootstrap_test_app(self.app)
        git_bash_spec = next(
            spec for spec in LocalHostControlService.KNOWN_DESKTOP_APPS if spec.app_key == "git-bash"
        )
        with self.app.state.database.session_scope() as session:
            service = LocalHostControlService(session, settings=self.app.state.settings)
            with patch.object(service, "_expand_path_pattern", return_value=[]), patch.object(
                service,
                "_find_shortcut",
                return_value=Path("C:/Users/Public/Desktop/Git Bash.lnk"),
            ), patch.object(
                service,
                "_resolve_shortcut_target",
                return_value=Path("Z:/missing/git-bash.exe"),
            ):
                entry_path, entry_kind = service._resolve_app_entry(git_bash_spec)

        self.assertIsNone(entry_path)
        self.assertIsNone(entry_kind)

    def test_list_desktop_apps_discovers_valid_shortcuts_outside_known_catalog(self) -> None:
        bootstrap_test_app(self.app)
        discovered_exe = self.root / "apps" / "GammaTool.exe"
        discovered_exe.parent.mkdir(parents=True, exist_ok=True)
        discovered_exe.write_text("", encoding="utf-8")
        shortcut = self.root / "Desktop" / "Gamma Tool.lnk"
        shortcut.parent.mkdir(parents=True, exist_ok=True)
        shortcut.write_text("", encoding="utf-8")
        with self.app.state.database.session_scope() as session:
            service = LocalHostControlService(session, settings=self.app.state.settings)
            with patch.object(
                service,
                "_resolve_shortcut_targets_bulk",
                return_value=((shortcut, discovered_exe),),
            ), patch.object(service, "_list_processes_by_name", return_value={}), patch.object(
                service,
                "find_windows",
                return_value=[],
            ):
                apps = service.list_desktop_apps()

        discovered = next((item for item in apps if item.app_key == "desktop-gamma-tool"), None)
        self.assertIsNotNone(discovered)
        assert discovered is not None
        self.assertEqual(discovered.label, "Gamma Tool")
        self.assertTrue(discovered.launch_supported)
        self.assertIn("source=shortcut-discovery", discovered.notes)

    def test_open_desktop_app_can_use_discovered_shortcut_target(self) -> None:
        bootstrap_test_app(self.app)
        discovered_exe = self.root / "apps" / "Kimi.exe"
        discovered_exe.parent.mkdir(parents=True, exist_ok=True)
        discovered_exe.write_text("", encoding="utf-8")
        discovered_spec = _ResolvedDesktopAppSpec(
            app_key="desktop-kimi",
            label="Kimi",
            category="utility",
            process_names=("kimi.exe",),
            title_hints=("kimi",),
            resolved_entry_path=discovered_exe.resolve(),
            resolved_entry_kind="shortcut-target",
            running_processes=(),
            visible_window_titles=(),
            notes=("entry=shortcut-target", "source=shortcut-discovery"),
        )
        with self.app.state.database.session_scope() as session:
            service = LocalHostControlService(session, settings=self.app.state.settings)
            fake_window = LocalHostWindowMatchView(
                hwnd=202,
                title="Kimi",
                process_id=5252,
                process_name="kimi.exe",
                is_foreground=False,
            )
            with patch.object(
                service,
                "_get_desktop_app_runtime_spec",
                return_value=discovered_spec,
            ), patch.object(
                service,
                "_wait_for_app_window",
                return_value=fake_window,
            ), patch.object(
                service,
                "_force_foreground_window",
                return_value=True,
            ), patch("os.startfile", create=True) as mocked_startfile:
                result = service.open_desktop_app("desktop-kimi")

        mocked_startfile.assert_called_once_with(str(discovered_exe.resolve()))
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Opened Kimi via shortcut-target.", result.stdout)


if __name__ == "__main__":
    unittest.main()
