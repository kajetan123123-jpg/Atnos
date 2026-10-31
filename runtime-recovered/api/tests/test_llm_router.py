from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from atnos.core.config import Settings
from atnos.llm.router import LLMRouter, PrivacyMode, TaskType
from atnos.services.llm_usage_tracker_service import LlmUsageTrackerService


class LLMRouterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.settings = Settings(
            data_dir=self.root / "data",
            ollama_base_url="http://127.0.0.1:11434",
            ollama_main_model="qwen3:14b",
            ollama_fast_model="qwen3:8b",
            ollama_vision_model="qwen3-vl:8b",
            gemini_api_key="",
            gemini_model="gemini-2.5-flash",
        )

    def test_detects_fast_text_and_vision(self) -> None:
        router = LLMRouter(self.settings)

        self.assertEqual(router.detect_task_type("ok"), TaskType.FAST)
        self.assertEqual(router.detect_task_type("Przeanalizuj screenshot", has_image=True), TaskType.VISION)

    def test_selects_local_main_for_text(self) -> None:
        router = LLMRouter(self.settings)

        backend = router.select_backend(TaskType.TEXT)

        self.assertEqual(backend.provider, "ollama-local")
        self.assertEqual(backend.model, "qwen3:14b")
        self.assertTrue(backend.supports_tools)
        self.assertFalse(backend.supports_vision)
        self.assertTrue(backend.endpoint.endswith("/api/chat"))

    def test_selects_local_fast_and_vision_roles(self) -> None:
        router = LLMRouter(self.settings)

        fast = router.select_backend(TaskType.FAST)
        vision = router.select_backend(TaskType.VISION)

        self.assertEqual(fast.model, "qwen3:8b")
        self.assertTrue(fast.supports_tools)
        self.assertEqual(vision.model, "qwen3-vl:8b")
        self.assertFalse(vision.supports_tools)
        self.assertTrue(vision.supports_vision)

    def test_premium_without_key_falls_back_to_local_main(self) -> None:
        with patch.dict("os.environ", {"GEMINI_API_KEY": ""}, clear=False):
            router = LLMRouter(self.settings)

            backend = router.select_backend(TaskType.PREMIUM)

        self.assertEqual(backend.provider, "ollama-local")
        self.assertEqual(backend.model, "qwen3:14b")

    def test_premium_with_key_uses_gemini_when_allowed(self) -> None:
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=False):
            router = LLMRouter(self.settings)

            backend = router.select_backend(TaskType.PREMIUM)

        self.assertEqual(backend.provider, "gemini-cloud")
        self.assertEqual(backend.model, "gemini-2.5-flash")
        self.assertTrue(backend.supports_tools)
        self.assertTrue(backend.supports_vision)

    def test_premium_uses_gemini_key_loaded_from_settings(self) -> None:
        settings = self.settings.model_copy(update={"gemini_api_key": "settings-test-key"})
        with patch.dict("os.environ", {"GEMINI_API_KEY": ""}, clear=False):
            router = LLMRouter(settings)

            backend = router.select_backend(TaskType.PREMIUM)

        self.assertEqual(backend.provider, "gemini-cloud")
        self.assertEqual(backend.model, "gemini-2.5-flash")

    def test_strict_local_blocks_premium_cloud(self) -> None:
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=False):
            router = LLMRouter(self.settings)

            backend = router.select_backend(TaskType.PREMIUM, privacy_mode=PrivacyMode.STRICT_LOCAL)

        self.assertEqual(backend.provider, "ollama-local")
        self.assertEqual(backend.model, "qwen3:14b")
        self.assertIn("strict-local", backend.routing_reason)

    def test_gemini_limit_falls_back_to_local_main(self) -> None:
        now = datetime(2026, 5, 27, 12, 0, tzinfo=ZoneInfo("Europe/Warsaw"))
        tracker = LlmUsageTrackerService(self.settings, now=now)
        for _ in range(self.settings.gemini_daily_request_limit):
            tracker.record_request(provider_key="gemini-cloud", model_name="gemini-2.5-flash")

        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=False):
            router = LLMRouter(self.settings, usage_tracker=tracker)

            backend = router.select_backend(TaskType.PREMIUM)

        self.assertEqual(backend.provider, "ollama-local")
        self.assertEqual(backend.model, "qwen3:14b")


if __name__ == "__main__":
    unittest.main()
