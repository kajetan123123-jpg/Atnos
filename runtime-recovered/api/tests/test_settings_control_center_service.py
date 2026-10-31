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

from atnos.services.access_reality_service import AccessRealityService
from atnos.services.local_short_runtime_probe_service import LocalShortRuntimeComponent, LocalShortRuntimeSnapshot
from atnos.services.settings_control_center_service import SettingsControlCenterService


class SettingsControlCenterServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_payload_avoids_live_access_reality_probes(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        access_calls: list[bool] = []

        with TestClient(app):
            with app.state.database.session_scope() as session:
                service = SettingsControlCenterService(
                    session,
                    settings=app.state.settings,
                    provider_registry=app.state.providers,
                )
                original_access_snapshot = AccessRealityService.build_snapshot

                def access_spy(access_service, *, include_live_health: bool = True):
                    access_calls.append(include_live_health)
                    return original_access_snapshot(
                        access_service,
                        include_live_health=include_live_health,
                    )

                with patch(
                    "atnos.services.settings_control_center_service.AccessRealityService.build_snapshot",
                    autospec=True,
                    side_effect=access_spy,
                ):
                    payload = service.get_payload()

        self.assertIn("access_reality", payload)
        self.assertEqual(access_calls, [False])

    def test_payload_includes_local_short_runtime_surface(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)

        with TestClient(app):
            with app.state.database.session_scope() as session:
                service = SettingsControlCenterService(
                    session,
                    settings=app.state.settings,
                    provider_registry=app.state.providers,
                )
                with patch(
                    "atnos.services.settings_control_center_service.LocalShortRuntimeProbeService.snapshot",
                    return_value=LocalShortRuntimeSnapshot(
                        enabled=True,
                        ready=False,
                        reason="Brakuje gotowych runtime'ow: Piper.",
                        components=(
                            LocalShortRuntimeComponent(
                                key="piper",
                                label="Piper",
                                ready=False,
                                path="F:/Atnos/data/models/piper/pl_PL-darkman-medium.onnx",
                                reason="Polish Piper model file is missing.",
                            ),
                        ),
                    ),
                ):
                    payload = service.get_payload()

        self.assertIn("runtime", payload)
        self.assertFalse(payload["runtime"]["local_shorts_ready"])
        self.assertEqual(len(payload["runtime"]["local_shorts_runtime"]), 1)
        self.assertEqual(payload["runtime"]["local_shorts_runtime"][0]["key"], "piper")


if __name__ == "__main__":
    unittest.main()
