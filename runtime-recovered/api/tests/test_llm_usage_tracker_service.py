from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import build_test_settings, make_temp_dir

from atnos.db.models.enums import ModelTask
from atnos.providers.base import ProviderUnavailableError
from atnos.providers.google_gemini import GoogleGeminiProviderAdapter
from atnos.services.llm_usage_tracker_service import LlmUsageTrackerService


class LlmUsageTrackerServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.settings = build_test_settings(self.root)

    def test_records_gemini_and_ollama_counters(self) -> None:
        tracker = LlmUsageTrackerService(
            self.settings,
            now=datetime.fromisoformat("2026-05-27T10:00:00+02:00"),
        )

        first = tracker.record_request(provider_key="gemini-cloud", model_name="gemini-2.5-flash")
        second = tracker.record_request(provider_key="ollama-local", model_name="qwen3:8b")
        third = tracker.record_request(provider_key="ollama-local", model_name="qwen3-vl:8b")

        self.assertEqual(first.gemini_requests_today, 1)
        self.assertEqual(second.ollama_fast_requests_today, 1)
        self.assertEqual(third.ollama_vision_requests_today, 1)

    def test_resets_usage_when_date_changes(self) -> None:
        tracker = LlmUsageTrackerService(
            self.settings,
            now=datetime.fromisoformat("2026-05-27T10:00:00+02:00"),
        )
        tracker.record_request(provider_key="gemini-cloud", model_name="gemini-2.5-flash")

        next_day = LlmUsageTrackerService(
            self.settings,
            now=datetime.fromisoformat("2026-05-28T10:00:00+02:00"),
        )

        self.assertEqual(next_day.get_today_stats().gemini_requests_today, 0)

    def test_gemini_limit_statuses(self) -> None:
        self.settings.gemini_daily_request_limit = 3
        self.settings.gemini_daily_warning_threshold = 2
        tracker = LlmUsageTrackerService(self.settings)

        self.assertEqual(tracker.check_gemini_limit(), "ok")
        tracker.record_request(provider_key="gemini-cloud", model_name="gemini-2.5-flash")
        tracker.record_request(provider_key="gemini-cloud", model_name="gemini-2.5-flash")
        self.assertEqual(tracker.check_gemini_limit(), "warning")
        tracker.record_request(provider_key="gemini-cloud", model_name="gemini-2.5-flash")
        self.assertEqual(tracker.check_gemini_limit(), "exceeded")

    def test_gemini_adapter_blocks_request_when_daily_limit_is_exceeded(self) -> None:
        self.settings.gemini_daily_request_limit = 1
        tracker = LlmUsageTrackerService(self.settings)
        tracker.record_request(provider_key="gemini-cloud", model_name="gemini-2.5-flash")
        adapter = GoogleGeminiProviderAdapter(app_root=self.root, settings=self.settings)

        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=False), self.assertRaises(
            ProviderUnavailableError
        ):
            adapter.execute(
                task=ModelTask.GENERAL_CHAT,
                context=SimpleNamespace(
                    provider_prompt="Hej",
                    local_attachments=tuple(),
                    allow_external_attachment_content=False,
                ),
                provider=SimpleNamespace(endpoint_url=None),
                model_config=SimpleNamespace(runtime_model_name="gemini-2.5-flash", max_output_tokens=64),
            )

    def test_gemini_adapter_records_successful_request(self) -> None:
        adapter = GoogleGeminiProviderAdapter(app_root=self.root, settings=self.settings)

        class _Response:
            status_code = 200

            @staticmethod
            def json() -> dict[str, object]:
                return {
                    "candidates": [
                        {
                            "content": {"parts": [{"text": "Dziala"}]},
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 2,
                        "candidatesTokenCount": 3,
                        "totalTokenCount": 5,
                    },
                }

        class _Client:
            def __init__(self, *, timeout: float) -> None:
                self.timeout = timeout

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

            def post(self, *args, **kwargs):
                return _Response()

        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=False), patch(
            "atnos.providers.google_gemini.httpx.Client",
            _Client,
        ):
            result = adapter.execute(
                task=ModelTask.GENERAL_CHAT,
                context=SimpleNamespace(
                    provider_prompt="Hej",
                    local_attachments=tuple(),
                    allow_external_attachment_content=False,
                ),
                provider=SimpleNamespace(endpoint_url=None),
                model_config=SimpleNamespace(runtime_model_name="gemini-2.5-flash", max_output_tokens=64),
            )

        self.assertEqual(result.content, "Dziala")
        self.assertEqual(result.raw_metadata["gemini_requests_today"], 1)
        self.assertEqual(LlmUsageTrackerService(self.settings).get_today_stats().gemini_requests_today, 1)


if __name__ == "__main__":
    unittest.main()
