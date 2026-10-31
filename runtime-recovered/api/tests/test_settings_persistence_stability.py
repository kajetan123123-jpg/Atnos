from __future__ import annotations

from pathlib import Path
import sys
import unittest

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import create_test_app, make_temp_dir, bootstrap_test_app


class SettingsPersistenceStabilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_provider_posture_roundtrip_persists(self) -> None:
        """STAB-SET-002: save → GET reflects stored value (no optimistic-only success)."""
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        bootstrap_test_app(app)

        with TestClient(app) as client:
            before = client.get("/settings/provider-posture")
            self.assertEqual(before.status_code, 200)
            original = before.json().get("resolved")

            target = "strict-local"
            if original == target:
                target = "prefer-local"

            put = client.put("/settings/provider-posture", json={"value": target})
            self.assertEqual(put.status_code, 200, put.text)
            self.assertEqual(put.json().get("resolved"), target)

            after = client.get("/settings/provider-posture")
            self.assertEqual(after.status_code, 200)
            self.assertEqual(after.json().get("resolved"), target)

            # restore
            if original:
                client.put("/settings/provider-posture", json={"value": original})

    def test_provider_posture_rejects_invalid_value(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        bootstrap_test_app(app)

        with TestClient(app) as client:
            response = client.put("/settings/provider-posture", json={"value": "not-a-real-posture"})
            self.assertIn(response.status_code, {400, 422})

    def test_local_chat_model_switch_rejects_empty(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        bootstrap_test_app(app)

        with TestClient(app) as client:
            response = client.post(
                "/settings/runtime/local-chat-model",
                json={"runtime_model_name": "   "},
            )
            self.assertEqual(response.status_code, 400)
            self.assertIn("empty", (response.json().get("detail") or "").lower())
