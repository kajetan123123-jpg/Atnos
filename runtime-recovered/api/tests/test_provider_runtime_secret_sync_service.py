from __future__ import annotations

from pathlib import Path
import os
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import create_test_app, make_temp_dir


class ProviderRuntimeSecretSyncServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.app = create_test_app(self.root)
        self.addCleanup(self.app.state.database.dispose)

    def test_openai_provider_card_syncs_secret_into_runtime_and_inventory(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            with TestClient(self.app) as client:
                update_response = client.put(
                    "/settings/provider-cards/openai",
                    json={"api_key": "sk-test-openai-key", "enabled": True},
                )
                self.assertEqual(update_response.status_code, 200)
                self.assertEqual(os.environ.get("OPENAI_API_KEY"), "sk-test-openai-key")

                inventory_response = client.get("/settings/runtime-inventory")
                self.assertEqual(inventory_response.status_code, 200)
                payload = inventory_response.json()

        provider_map = {
            item["provider_key"]: item
            for item in payload["providers"]
        }
        self.assertIn("openai-cloud", provider_map)
        self.assertEqual(provider_map["openai-cloud"]["status"], "ready")

    def test_disabling_provider_card_clears_synced_runtime_secret(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "stale-key"}, clear=False):
            with TestClient(self.app) as client:
                update_response = client.put(
                    "/settings/provider-cards/openai",
                    json={"enabled": False},
                )
                self.assertEqual(update_response.status_code, 200)
            self.assertNotIn("OPENAI_API_KEY", os.environ)

    def test_startup_keeps_existing_runtime_secret_when_vault_is_empty(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "env-configured-key"}, clear=False):
            with TestClient(self.app):
                self.assertEqual(os.environ.get("OPENAI_API_KEY"), "env-configured-key")


if __name__ == "__main__":
    unittest.main()
