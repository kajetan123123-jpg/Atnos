from __future__ import annotations

from pathlib import Path
import sys
import unittest

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_support import create_test_app, make_temp_dir, bootstrap_test_app

from atnos.db.models.enums import VoiceProviderMode
from atnos.repositories.user_repository import UserRepository
from atnos.repositories.voice_profile_repository import VoiceProfileRepository
from atnos.services.settings_control_center_service import SettingsControlCenterService
from atnos.services.voice_synthesis_service import VoiceSynthesisService


class SettingsControlCenterStabilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_ensure_voice_profiles_does_not_duplicate_default(self) -> None:
        """STAB-SET-001: existing default + missing presets must not 500 UNIQUE."""
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        bootstrap_test_app(app)

        with app.state.database.session_scope() as session:
            owner = UserRepository(session).get_owner()
            assert owner is not None
            voices = VoiceProfileRepository(session)
            # Simulate pre-okropek DB: default uses another key.
            voices.create(
                user_id=owner.id,
                provider_id=None,
                name="Polski creator premium",
                voice_key="pl_maja",
                provider_mode=VoiceProviderMode.REMOTE,
                style="warm",
                language="pl-PL",
                speaking_rate="-10%",
                supports_realtime=False,
                auto_read_responses=True,
                is_default=True,
                notes="legacy default",
            )
            service = SettingsControlCenterService(
                session,
                settings=app.state.settings,
                provider_registry=app.state.providers,
            )
            service._ensure_voice_profiles(owner.id)
            rows = voices.list_for_user(owner.id)
            defaults = [row for row in rows if row.is_default]
            self.assertEqual(len(defaults), 1)
            keys = {row.voice_key for row in rows}
            self.assertIn("pl_maja", keys)
            self.assertIn(VoiceSynthesisService.DEFAULT_POLISH_VOICE, keys)

        with TestClient(app) as client:
            response = client.get("/settings/control-center")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("voices", payload)


if __name__ == "__main__":
    unittest.main()
