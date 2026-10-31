from __future__ import annotations

from pathlib import Path
import json
import unittest

from atnos.browser_operator.logging import write_browser_action_log, write_browser_failure_log
from atnos.browser_operator.memory import (
    find_recipe,
    load_recipes,
    mark_recipe_failed,
    save_recipe,
    update_recipe_after_success,
)
from atnos.browser_operator.models import BrowserAction, BrowserRecipe
from atnos.browser_operator.policies import action_requires_human_approval, classify_browser_risk
from atnos.browser_operator.verifier import (
    verify_download_exists,
    verify_no_error_message,
    verify_prompt_pasted,
    verify_text_visible,
    verify_url_contains,
)

from .test_support import build_test_settings, make_temp_dir


class BrowserOperatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.settings = build_test_settings(self.root)

    def test_risk_classification_marks_submit_and_upload_as_high(self) -> None:
        submit_action = BrowserAction("submit", None, None, "Submit the form.", "Form sent.", "low")
        upload_action = BrowserAction("upload_file", "#file", "F:/x.png", "Upload a file.", "File attached.", "low")
        self.assertEqual(classify_browser_risk(submit_action), "high")
        self.assertEqual(classify_browser_risk(upload_action), "high")
        self.assertTrue(action_requires_human_approval(upload_action, self.settings))

    def test_recipe_loading_and_saving_roundtrip(self) -> None:
        recipe = BrowserRecipe(
            site="example.com",
            task="extract_text",
            steps=[BrowserAction("goto", "https://example.com", None, "Open page.", "Page visible.", "low")],
            human_required=["first_login"],
            risks=["UI may change"],
            notes="Test recipe",
        )
        save_recipe(self.settings, recipe)

        loaded = load_recipes(self.settings)
        found = find_recipe(self.settings, "example.com", "extract_text")

        self.assertEqual(len(loaded), 1)
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.site, "example.com")
        self.assertEqual(found.task, "extract_text")
        self.assertEqual(found.steps[0].action_type, "goto")

    def test_recipe_success_and_failure_stats_are_persisted(self) -> None:
        recipe = BrowserRecipe(site="example.com", task="login")
        save_recipe(self.settings, recipe)

        update_recipe_after_success(self.settings, site="example.com", task="login")
        mark_recipe_failed(
            self.settings,
            site="example.com",
            task="login",
            reason="captcha",
            failed_step="observe",
            screenshot_path="F:/tmp/failure.png",
        )

        recipes_path = self.settings.app_root / "data" / "browser" / "site_recipes.yaml"
        payload = recipes_path.read_text(encoding="utf-8")
        self.assertIn("success_count: 1", payload)
        self.assertIn("failure_count: 1", payload)
        self.assertIn("captcha", payload)

    def test_action_and_failure_logs_are_written(self) -> None:
        action = BrowserAction("goto", "https://example.com", None, "Open page.", "Page visible.", "low")
        write_browser_action_log(
            self.settings,
            url="https://example.com",
            action=action,
            result="ok",
            screenshot_path="F:/tmp/screenshot.png",
        )
        failure_path = write_browser_failure_log(
            self.settings,
            site="example.com",
            task="login",
            failed_step="find_input",
            reason="selector changed",
            screenshot_path="F:/tmp/failure.png",
        )
        lines = self.settings.browser_log_path.read_text(encoding="utf-8").strip().splitlines()
        entry = json.loads(lines[-1])
        self.assertEqual(entry["action_type"], "goto")
        self.assertEqual(entry["result"], "ok")
        self.assertTrue(failure_path.exists())

    def test_verifier_helpers_cover_basic_contract(self) -> None:
        download_path = self.root / "download.txt"
        download_path.write_text("ok", encoding="utf-8")
        self.assertTrue(verify_url_contains("https://example.com/dashboard", "dashboard"))
        self.assertTrue(verify_text_visible("Prompt zostal wklejony do pola.", "wklejony"))
        self.assertTrue(verify_download_exists(download_path))
        self.assertTrue(verify_no_error_message("Everything passed successfully."))
        self.assertFalse(verify_no_error_message("Something went wrong."))
        self.assertTrue(verify_prompt_pasted("Oceń ten plan konfiguracji Atnosa", "konfiguracji Atnosa"))


if __name__ == "__main__":
    unittest.main()
