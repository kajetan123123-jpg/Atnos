from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import make_temp_dir

from atnos.core.config import Settings
from atnos.db.models.enums import ModelTask
from atnos.providers.google_gemini import GoogleGeminiProviderAdapter


class GeminiModelDefaultsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_gemini_defaults_to_flash_not_pro(self) -> None:
        adapter = GoogleGeminiProviderAdapter(app_root=self.root)
        with patch.dict("os.environ", {}, clear=True):
            selected = adapter._resolve_model_name(
                ModelTask.GENERAL_CHAT,
                model_config_runtime_name=None,
            )

        self.assertEqual(selected, "gemini-3.6-flash")

    def test_gemini_shared_model_env_overrides_default(self) -> None:
        adapter = GoogleGeminiProviderAdapter(app_root=self.root)
        with patch.dict("os.environ", {"GEMINI_MODEL": "gemini-2.5-flash-lite"}, clear=True):
            selected = adapter._resolve_model_name(
                ModelTask.SYNTHESIS,
                model_config_runtime_name=None,
            )

        self.assertEqual(selected, "gemini-2.5-flash-lite")

    def test_gemini_configuration_accepts_key_from_settings(self) -> None:
        settings = Settings(data_dir=self.root / "data", gemini_api_key="settings-test-key")
        adapter = GoogleGeminiProviderAdapter(app_root=self.root, settings=settings)

        with patch.dict("os.environ", {"GEMINI_API_KEY": ""}, clear=False):
            readiness = adapter.configuration_status(provider=None)

        self.assertTrue(readiness.ready)


if __name__ == "__main__":
    unittest.main()
