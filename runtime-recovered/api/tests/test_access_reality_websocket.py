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

from atnos.services.access_reality_service import (
    AccessRealitySnapshot,
    AccessRealitySummary,
    AccessSubjectStatus,
)


def _snapshot(
    *,
    snapshot_key: str,
    access_level: str,
) -> AccessRealitySnapshot:
    subject = AccessSubjectStatus(
        subject_key="provider.ollama-local",
        subject_type="provider",
        display_name="Ollama Local",
        access_level=access_level,
        summary="Provider state changed.",
    )
    counts = {
        "none": 0,
        "identity_only": 0,
        "metadata_only": 0,
        "configured": 0,
        "live_read": 0,
        "live_write": 0,
        "blocked": 0,
        "unavailable": 0,
    }
    counts[access_level] += 1
    return AccessRealitySnapshot(
        snapshot_key=snapshot_key,
        checked_at=subject.last_checked_at,
        summary=AccessRealitySummary(total_subjects=1, **counts),
        subjects=(subject,),
        operational_context={"app_env": "test"},
    )


class AccessRealityWebSocketTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = make_temp_dir()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_websocket_emits_access_change_only_after_digest_changes(self) -> None:
        app = create_test_app(self.root)
        self.addCleanup(app.state.database.dispose)
        access_calls: list[bool] = []
        frame_calls: list[tuple[bool, bool]] = []

        snapshots = iter(
            [
                _snapshot(snapshot_key="access-1", access_level="live_read"),
                _snapshot(snapshot_key="access-2", access_level="unavailable"),
            ]
        )

        def access_spy(_service, *, include_live_health: bool = True):
            access_calls.append(include_live_health)
            return next(snapshots)

        def frame_spy(_service, *, include_live_health: bool = True, fast_mode: bool = False):
            frame_calls.append((include_live_health, fast_mode))
            return []

        with (
            patch(
                "atnos.api.routes.ui.UiAdapterService.build_agent_frames",
                autospec=True,
                side_effect=frame_spy,
            ),
            patch(
                "atnos.api.routes.ui._build_ambient_state_payload",
                return_value={"ambient_listening_enabled": False},
            ),
            patch(
                "atnos.api.routes.ui.AccessRealityService.build_snapshot",
                autospec=True,
                side_effect=access_spy,
            ),
            TestClient(app) as client,
        ):
            with client.websocket_connect("/agent") as websocket:
                first_frames = [websocket.receive_json(), websocket.receive_json()]
                first_types = [frame["type"] for frame in first_frames]
                self.assertIn("access.reality", first_types)
                self.assertNotIn("access.reality.change", first_types)

                websocket.send_json({"type": "noop", "payload": {}})
                second_frames = []
                for _ in range(6):
                    frame = websocket.receive_json()
                    second_frames.append(frame)
                    if frame.get("type") == "access.reality.change":
                        break
                second_types = [frame["type"] for frame in second_frames]
                self.assertIn("access.reality", second_types)
                self.assertIn("access.reality.change", second_types)
                change_payload = next(
                    frame["payload"]
                    for frame in second_frames
                    if frame["type"] == "access.reality.change"
                )
                self.assertEqual(change_payload["snapshot_key"], "access-2")
                self.assertEqual(change_payload["changes"][0]["subject_key"], "provider.ollama-local")
                self.assertEqual(change_payload["changes"][0]["to_access_level"], "unavailable")

        self.assertEqual(access_calls, [False, False])
        self.assertEqual(frame_calls, [(False, True), (False, True)])


if __name__ == "__main__":
    unittest.main()
