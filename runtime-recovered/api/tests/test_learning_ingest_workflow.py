from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import create_test_app, make_temp_dir

from atnos.services.browser_recipe_runner_service import BrowserRecipeRunnerService
from atnos.services.integration_config_service import IntegrationConfigService
from atnos.services.learning_ingest_service import LearningIngestService
from atnos.services.trusted_auto_service import TrustedAutoService


class LearningIngestWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_learning_ingest_job_runs_browser_download_and_obsidian_write(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        vault_root = self.root / "vault"
        vault_root.mkdir(parents=True, exist_ok=True)

        with TestClient(app):
            with app.state.database.session_scope() as session:
                settings = app.state.settings
                IntegrationConfigService(session, settings).update_obsidian_config(
                    vault_path=str(vault_root),
                    export_enabled=True,
                )

                browser_final = settings.browser_downloads_dir / "browser-final-test.png"
                browser_final.write_bytes(b"fake-png-bytes")

                def fake_hf_download(*, local_dir, filename, **kwargs):
                    target = Path(local_dir) / filename
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("model card for ATNOS", encoding="utf-8")
                    return str(target)

                def fake_httpx_get(url, **kwargs):
                    if "raw.githubusercontent.com" in url:
                        return SimpleNamespace(status_code=200, content=b"# GitHub learning source\n", json=lambda: {})
                    raise AssertionError(f"Unexpected URL {url}")

                service = TrustedAutoService(session, settings)
                service.update_scheduler_controls(
                    autonomy_mode="trusted_auto",
                    scheduler_enabled=True,
                    default_retry_budget=1,
                )

                with patch(
                    "atnos.services.browser_recipe_runner_service.BrowserRecipeRunnerService.run",
                    return_value={
                        "ok": True,
                        "finalUrl": "https://youtube.fandom.com/wiki/Most-viewed_YouTube_shorts",
                        "results": [
                            {
                                "stepIndex": 1,
                                "type": "extract",
                                "value": "Most-viewed YouTube Shorts",
                            }
                        ],
                        "artifacts": {
                            "final_screenshot": browser_final.as_posix(),
                        },
                    },
                ), patch("huggingface_hub.hf_hub_download", side_effect=fake_hf_download), patch(
                    "atnos.services.learning_ingest_service.httpx.get",
                    side_effect=fake_httpx_get,
                ):
                    result = service.run_job_now(
                        project_id=1,
                        job_type="learning_ingest",
                        goal="Naucz Atnosa benchmarku shortow i pobierz publiczny artefakt z Hugging Face",
                        connector_key=None,
                        platform_key=None,
                        account_id=None,
                        trust_grant_id=None,
                        payload_json={
                            "topic": "satisfying fruit destruction shorts",
                            "browser_recipe": {
                                "steps": [
                                    {
                                        "type": "navigate",
                                        "url": "https://youtube.fandom.com/wiki/Most-viewed_YouTube_shorts",
                                    },
                                    {"type": "extract", "selector": "h1"},
                                ]
                            },
                            "huggingface_downloads": [
                                {
                                    "repo_id": "openai-community/gpt2",
                                    "repo_type": "model",
                                    "filename": "config.json",
                                    "label": "GPT-2 config",
                                }
                            ],
                            "github_downloads": [
                                {
                                    "owner": "openai",
                                    "repo": "openai-python",
                                    "path": "README.md",
                                    "ref": "main",
                                    "label": "OpenAI Python README",
                                }
                            ],
                        },
                        requires_trusted_auto=False,
                        execute_now=True,
                    )

        run = result["run"]
        self.assertIsNotNone(run)
        self.assertEqual(run["status"], "done")
        self.assertEqual(run["output_json"]["workflow"], "learning_ingest")
        self.assertEqual(run["output_json"]["topic"], "satisfying fruit destruction shorts")
        self.assertEqual(len(run["output_json"]["downloads"]), 3)
        self.assertTrue(run["output_json"]["vault_note_path"].startswith("Atnos/Education/LearningSessions/"))

        note_path = vault_root / Path(run["output_json"]["vault_note_path"])
        self.assertTrue(note_path.exists())
        note_text = note_path.read_text(encoding="utf-8")
        self.assertIn("Most-viewed YouTube Shorts", note_text)
        self.assertIn("GPT-2 config", note_text)
        self.assertIn("OpenAI Python README", note_text)

    def test_learning_ingest_blocks_unsafe_browser_steps(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            with app.state.database.session_scope() as session:
                service = LearningIngestService(session, app.state.settings)
                with self.assertRaises(RuntimeError):
                    service._run_browser_research(
                        {
                            "steps": [
                                {"type": "navigate", "url": "https://example.com"},
                                {"type": "run_powershell", "command": "Get-Process"},
                            ]
                        }
                    )

    def test_headless_browser_runner_falls_back_from_opera_to_managed_chromium(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            settings = app.state.settings
            service = BrowserRecipeRunnerService(settings)
            opera_path = Path(r"C:\Users\localuser\AppData\Local\Programs\Opera GX\opera.exe")
            managed_path = settings.playwright_browsers_path / "chromium-1217" / "chrome-win64" / "chrome.exe"
            managed_path.parent.mkdir(parents=True, exist_ok=True)
            managed_path.write_bytes(b"stub")
            script_path = self.root / "runner.mjs"
            script_path.write_text("// stub", encoding="utf-8")
            captured: dict[str, object] = {}

            def fake_run(command, **kwargs):
                spec_path = Path(command[-1])
                captured.update(json.loads(spec_path.read_text(encoding="utf-8")))
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=0,
                    stdout=json.dumps({"ok": True, "results": [], "artifacts": {}}),
                    stderr="",
                )

            with patch.object(
                service.browser_runtime,
                "describe",
                return_value=service.browser_runtime.describe().__class__(
                    profile_dir=settings.browser_profile_dir,
                    downloads_dir=settings.browser_downloads_dir,
                    trace_dir=settings.browser_trace_dir,
                    browsers_path=settings.playwright_browsers_path,
                    browser_executable=opera_path,
                    desktop_shell=None,
                    ready=True,
                    status_reason="Dedicated Opera GX runtime is available.",
                ),
            ), patch.object(
                service.browser_runtime,
                "is_managed_runtime",
                return_value=False,
            ), patch.object(
                service.browser_runtime,
                "resolve_managed_browser_executable",
                return_value=managed_path,
            ), patch.object(
                service,
                "_resolve_browser_runner_script",
                return_value=script_path,
            ), patch(
                "atnos.services.browser_recipe_runner_service.subprocess.run",
                side_effect=fake_run,
            ):
                result = service.run({"headless": True, "steps": [{"type": "navigate", "url": "https://example.com"}]})

        self.assertEqual(Path(str(captured["browserExecutable"])).resolve(), managed_path.resolve())
        self.assertIn("runtime_fallback", result)


if __name__ == "__main__":
    unittest.main()
