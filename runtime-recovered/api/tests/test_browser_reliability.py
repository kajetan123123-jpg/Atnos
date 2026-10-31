from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from atnos.repositories.browser_domain_whitelist_repository import BrowserDomainWhitelistRepository
from atnos.repositories.user_repository import UserRepository
from atnos.services.browser_recipe_runner_service import (
    BrowserRecipeApprovalRequiredError,
    BrowserRecipeBlockedError,
    BrowserRecipeError,
    BrowserRecipeRunnerService,
)
from atnos.services.runtime_settings_service import RuntimeSettingsService

from .test_support import bootstrap_test_app, create_test_app


class _StubBrowserRuntime:
    def __init__(self, managed_executable: Path, downloads_dir: Path) -> None:
        self.managed_executable = managed_executable
        self.downloads_dir = downloads_dir

    def describe(self):
        return SimpleNamespace(
            ready=True,
            status_reason="ready",
            downloads_dir=self.downloads_dir,
            browser_executable=self.managed_executable,
        )

    def resolve_managed_browser_executable(self):
        return self.managed_executable

    def is_managed_runtime(self, executable: Path | str | None) -> bool:
        if executable is None:
            return False
        return Path(executable) == self.managed_executable


class _StubBrowserRecipeRunnerService(BrowserRecipeRunnerService):
    def __init__(self, *args, responses: list[dict[str, object]], **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._responses = list(responses)
        self.seen_specs: list[dict[str, object]] = []
        managed = (self.settings.playwright_browsers_path / "managed-chromium.exe").resolve()
        self.browser_runtime = _StubBrowserRuntime(managed, self.settings.browser_downloads_dir)

    def _invoke_runner(self, spec: dict[str, object], *, timeout_ms: int) -> dict[str, object]:
        self.seen_specs.append(spec)
        return self._responses.pop(0)


class BrowserReliabilityTest(unittest.TestCase):
    def test_runner_subprocess_uses_utf8_decoding_for_node_payload(self) -> None:
        with TemporaryDirectory(prefix="atnos-browser-reliability-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            try:
                bootstrap_test_app(app)
                runner = BrowserRecipeRunnerService(app.state.settings)

                with patch.object(
                    runner,
                    "_resolve_browser_runner_script",
                    return_value=root / "playwright_trusted_auto_runner.mjs",
                ), patch("atnos.services.browser_recipe_runner_service.subprocess.run") as mocked_run:
                    mocked_run.return_value = SimpleNamespace(
                        returncode=0,
                        stdout='{"ok": true, "results": [], "artifacts": {}, "finalUrl": "https://example.com/"}',
                        stderr="",
                    )
                    payload = runner._invoke_runner(
                        {"steps": [{"type": "navigate", "url": "https://example.com/"}]},
                        timeout_ms=15_000,
                    )

                self.assertTrue(payload["ok"])
                _, kwargs = mocked_run.call_args
                self.assertEqual(kwargs["encoding"], "utf-8")
                self.assertEqual(kwargs["errors"], "replace")
            finally:
                app.state.database.dispose()

    def test_default_browser_spec_uses_headed_defaults_and_overlay_probes(self) -> None:
        with TemporaryDirectory(prefix="atnos-browser-reliability-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            try:
                bootstrap_test_app(app)
                runner = _StubBrowserRecipeRunnerService(
                    app.state.settings,
                    responses=[
                        {
                            "ok": True,
                            "results": [{"type": "navigate", "ok": True}],
                            "artifacts": {},
                            "finalUrl": "https://example.com/",
                        }
                    ],
                )

                result = runner.run(
                    {
                        "steps": [
                            {"type": "navigate", "url": "https://example.com/"},
                        ]
                    }
                )

                self.assertTrue(result["ok"])
                self.assertEqual(len(runner.seen_specs), 1)
                spec = runner.seen_specs[0]
                self.assertFalse(spec["headless"])
                self.assertEqual(spec["actionTimeoutMs"], 15_000)
                self.assertEqual(spec["totalTimeoutMs"], 300_000)
                self.assertEqual(spec["retryLimit"], 2)
                self.assertGreater(len(spec["cookieSelectors"]), 0)
                self.assertGreater(len(spec["modalCloseSelectors"]), 0)
                self.assertEqual(spec["overlayProbeTimeoutMs"], 3_000)
            finally:
                app.state.database.dispose()

    def test_browser_crash_is_retried_once_then_succeeds(self) -> None:
        with TemporaryDirectory(prefix="atnos-browser-reliability-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            try:
                bootstrap_test_app(app)
                runner = _StubBrowserRecipeRunnerService(
                    app.state.settings,
                    responses=[
                        {"ok": False, "code": "browser_crashed", "error": "Chromium died."},
                        {
                            "ok": True,
                            "results": [{"type": "navigate", "ok": True}],
                            "artifacts": {},
                            "finalUrl": "https://example.com/",
                        },
                    ],
                )

                result = runner.run(
                    {
                        "steps": [
                            {"type": "navigate", "url": "https://example.com/"},
                        ]
                    }
                )

                self.assertTrue(result["ok"])
                self.assertEqual(len(runner.seen_specs), 2)
            finally:
                app.state.database.dispose()

    def test_browser_crash_fails_after_second_crash(self) -> None:
        with TemporaryDirectory(prefix="atnos-browser-reliability-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            try:
                bootstrap_test_app(app)
                runner = _StubBrowserRecipeRunnerService(
                    app.state.settings,
                    responses=[
                        {"ok": False, "code": "browser_crashed", "error": "Chromium died."},
                        {"ok": False, "code": "browser_crashed", "error": "Chromium died again."},
                    ],
                )

                with self.assertRaises(BrowserRecipeError) as error:
                    runner.run(
                        {
                            "steps": [
                                {"type": "navigate", "url": "https://example.com/"},
                            ]
                        }
                    )

                self.assertEqual(error.exception.code, "browser_crashed")
                self.assertEqual(len(runner.seen_specs), 2)
            finally:
                app.state.database.dispose()

    def test_whitelist_only_mode_requires_approval_for_unlisted_domain(self) -> None:
        with TemporaryDirectory(prefix="atnos-browser-reliability-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            try:
                bootstrap_test_app(app)
                with app.state.database.session_scope() as session:
                    controls = RuntimeSettingsService(session, app.state.settings).snapshot()
                    runner = _StubBrowserRecipeRunnerService(
                        app.state.settings,
                        session=session,
                        runtime_controls=replace(controls, browser_allow_any_url=False),
                        responses=[],
                    )

                    with self.assertRaises(BrowserRecipeApprovalRequiredError) as error:
                        runner.run(
                            {
                                "steps": [
                                    {"type": "navigate", "url": "https://example.com/"},
                                ]
                            }
                        )

                    self.assertEqual(error.exception.code, "approval_required")
            finally:
                app.state.database.dispose()

    def test_blacklisted_domains_are_blocked_even_when_allow_any_url_is_enabled(self) -> None:
        with TemporaryDirectory(prefix="atnos-browser-reliability-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            try:
                bootstrap_test_app(app)
                runner = _StubBrowserRecipeRunnerService(
                    app.state.settings,
                    responses=[],
                )

                with self.assertRaises(BrowserRecipeBlockedError) as error:
                    runner.run(
                        {
                            "steps": [
                                {"type": "navigate", "url": "https://paypal.com/checkout"},
                            ]
                        }
                    )

                self.assertEqual(error.exception.code, "blocked_domain")
            finally:
                app.state.database.dispose()

    def test_private_network_and_non_http_targets_are_blocked(self) -> None:
        with TemporaryDirectory(prefix="atnos-browser-reliability-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            try:
                bootstrap_test_app(app)
                runner = _StubBrowserRecipeRunnerService(app.state.settings, responses=[])

                for url, expected_code in (
                    ("http://127.0.0.1:8000/private", "blocked_private_network"),
                    ("http://169.254.169.254/latest/meta-data", "blocked_private_network"),
                    ("http://metadata.google.internal/", "blocked_private_network"),
                    ("file:///C:/Windows/System32/drivers/etc/hosts", "blocked_protocol"),
                ):
                    with self.subTest(url=url):
                        with self.assertRaises(BrowserRecipeBlockedError) as error:
                            runner.run({"steps": [{"type": "navigate", "url": url}]})
                        self.assertEqual(error.exception.code, expected_code)
            finally:
                app.state.database.dispose()

    def test_persisted_whitelist_allows_navigation_when_runtime_is_whitelist_only(self) -> None:
        with TemporaryDirectory(prefix="atnos-browser-reliability-") as tmpdir:
            root = Path(tmpdir)
            app = create_test_app(root)
            try:
                bootstrap_test_app(app)
                with app.state.database.session_scope() as session:
                    owner = UserRepository(session).get_owner()
                    assert owner is not None
                    BrowserDomainWhitelistRepository(session).create(
                        user_id=owner.id,
                        domain="example.com",
                    )
                    controls = RuntimeSettingsService(session, app.state.settings).snapshot()
                    runner = _StubBrowserRecipeRunnerService(
                        app.state.settings,
                        session=session,
                        runtime_controls=replace(controls, browser_allow_any_url=False),
                        responses=[
                            {
                                "ok": True,
                                "results": [{"type": "navigate", "ok": True}],
                                "artifacts": {},
                                "finalUrl": "https://example.com/",
                            }
                        ],
                    )

                    result = runner.run(
                        {
                            "steps": [
                                {"type": "navigate", "url": "https://example.com/"},
                            ]
                        }
                    )

                self.assertTrue(result["ok"])
                self.assertEqual(len(runner.seen_specs), 1)
            finally:
                app.state.database.dispose()


if __name__ == "__main__":
    unittest.main()
