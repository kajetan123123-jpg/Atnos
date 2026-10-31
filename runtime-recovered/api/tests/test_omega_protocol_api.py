from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import create_test_app, make_temp_dir

from atnos.services.local_runtime_probe_service import LocalRuntimeProbeView


class OmegaProtocolApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.app = create_test_app(self.root)
        self.addCleanup(self.app.state.database.dispose)
        self.vault_dir = self.root / "obsydian"
        self.vault_dir.mkdir(parents=True, exist_ok=True)
        self.app.state.settings.obsidian_vault_path = self.vault_dir
        self.app.state.settings.obsidian_export_enabled = True

    def test_activate_omega_protocol_enables_expanded_runtime_and_writes_note(self) -> None:
        fake_probes = [
            LocalRuntimeProbeView(
                key="ollama-local",
                label="Ollama Local",
                status="ready",
                ready=True,
                probe_kind="http",
                reason="ok",
                checked_at=self._now(),
                details={},
                suggestions=(),
            ),
            LocalRuntimeProbeView(
                key="comfyui",
                label="ComfyUI",
                status="missing",
                ready=False,
                probe_kind="filesystem",
                reason="missing",
                checked_at=self._now(),
                details={},
                suggestions=(),
            ),
        ]

        with patch(
            "atnos.services.omega_protocol_service.LocalRuntimeProbeService.probe_all",
            return_value=fake_probes,
        ):
            with TestClient(self.app) as client:
                response = client.post("/settings/control-center/omega-protocol/activate")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        runtime = payload["runtime"]
        self.assertTrue(runtime["browser_control_enabled"])
        self.assertTrue(runtime["external_actions_enabled"])
        self.assertTrue(runtime["scheduler_enabled"])
        self.assertEqual(runtime["autonomy_mode"], "supervised")
        self.assertFalse(runtime["login_automation_enabled"])
        self.assertFalse(runtime["two_factor_automation_enabled"])
        self.assertFalse(runtime["publish_automation_enabled"])
        omega = payload["omega_protocol"]
        self.assertEqual(omega["profile_key"], "omega_expanded")
        self.assertTrue(omega["activated"])
        self.assertEqual(omega["note_path"], "Atnos/System/Omega-Expanded-Protocol.md")
        self.assertGreaterEqual(len(omega["trusted_filesystem_roots"]), 2)

        note_path = self.vault_dir / "Atnos" / "System" / "Omega-Expanded-Protocol.md"
        self.assertTrue(note_path.exists())
        markdown = note_path.read_text(encoding="utf-8")
        self.assertIn("# Omega Expanded", markdown)
        self.assertIn("Plaintext passwords", markdown)

    @staticmethod
    def _now():
        from datetime import datetime, timezone

        return datetime.now(timezone.utc)


if __name__ == "__main__":
    unittest.main()
