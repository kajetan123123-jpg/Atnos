from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from atnos.services.browser_recipes.generic_form_fill import run_generic_form_fill
from atnos.services.browser_recipes.link_collect import run_link_collect
from atnos.services.browser_recipes.screenshot_capture import run_screenshot_capture
from atnos.services.browser_recipes.webpage_text_extract import run_webpage_text_extract

from .test_support import build_test_settings


class _RecipeRunnerStub:
    def __init__(self, settings, responses: list[dict[str, object]]) -> None:
        self.settings = settings
        self._responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def run(self, recipe: dict[str, object]) -> dict[str, object]:
        self.calls.append(recipe)
        for step in recipe.get("steps", []):
            if isinstance(step, dict) and step.get("type") == "screenshot":
                target = Path(str(step.get("path")))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        return self._responses.pop(0)


class BrowserRecipesTest(unittest.TestCase):
    def test_webpage_text_extract_returns_text_and_truncation_notice(self) -> None:
        with TemporaryDirectory(prefix="atnos-browser-recipes-") as tmpdir:
            settings = build_test_settings(Path(tmpdir))
            runner = _RecipeRunnerStub(
                settings,
                responses=[
                    {
                        "results": [
                            {"type": "extract", "value": "A" * 50_500},
                        ]
                    }
                ],
            )

            result = run_webpage_text_extract(
                runner,
                {"url": "https://example.com/article", "selector": "article"},
            )

            self.assertTrue(result["ok"])
            self.assertEqual(len(result["text"]), 50_000)
            self.assertTrue(result["truncated"])
            self.assertIn("truncated", result["notice"])

    def test_screenshot_capture_returns_attachment_descriptor(self) -> None:
        with TemporaryDirectory(prefix="atnos-browser-recipes-") as tmpdir:
            settings = build_test_settings(Path(tmpdir))
            runner = _RecipeRunnerStub(
                settings,
                responses=[
                    {
                        "ok": True,
                        "results": [{"type": "screenshot", "path": "placeholder"}],
                        "artifacts": {},
                        "finalUrl": "https://example.com/",
                    }
                ],
            )

            result = run_screenshot_capture(
                runner,
                {"url": "https://example.com", "conversation_id": 55},
            )

            self.assertTrue(result["ok"])
            attachment = result["attachment"]
            self.assertEqual(attachment["mime"], "image/png")
            self.assertIn("55", attachment["storage_path"])
            self.assertGreater(attachment["size_bytes"], 0)
            self.assertTrue((settings.app_root / attachment["storage_path"]).exists())

    def test_generic_form_fill_returns_preview_without_submit(self) -> None:
        with TemporaryDirectory(prefix="atnos-browser-recipes-") as tmpdir:
            settings = build_test_settings(Path(tmpdir))
            runner = _RecipeRunnerStub(
                settings,
                responses=[
                    {
                        "ok": True,
                        "results": [{"type": "fill_fields", "ok": True}],
                        "artifacts": {},
                        "finalUrl": "https://example.com/form",
                    }
                ],
            )

            result = run_generic_form_fill(
                runner,
                {
                    "url": "https://example.com/form",
                    "fields": [
                        {"label": "Email", "value": "owner@example.com"},
                    ],
                    "conversation_id": 12,
                },
            )

            self.assertTrue(result["ok"])
            self.assertTrue(result["requires_approval"])
            self.assertEqual(result["preview_kind"], "browser_form_submit")
            self.assertEqual(result["preview_payload"]["url"], "https://example.com/form")
            self.assertIn("screenshot", result["preview_payload"])
            called_steps = runner.calls[0]["steps"]
            self.assertEqual([step["type"] for step in called_steps], ["navigate", "fill_fields", "screenshot"])

    def test_link_collect_returns_structured_links(self) -> None:
        with TemporaryDirectory(prefix="atnos-browser-recipes-") as tmpdir:
            settings = build_test_settings(Path(tmpdir))
            runner = _RecipeRunnerStub(
                settings,
                responses=[
                    {
                        "results": [
                            {
                                "type": "collect_links",
                                "value": [
                                    {"text": "Docs", "url": "https://example.com/docs"},
                                    {"text": "Pricing", "url": "https://example.com/pricing"},
                                ],
                            }
                        ]
                    }
                ],
            )

            result = run_link_collect(
                runner,
                {"url": "https://example.com", "selector": "a[href]"},
            )

            self.assertTrue(result["ok"])
            self.assertEqual(len(result["links"]), 2)
            self.assertEqual(result["links"][0]["text"], "Docs")
            self.assertEqual(result["links"][1]["url"], "https://example.com/pricing")


if __name__ == "__main__":
    unittest.main()
