from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from .test_support import create_test_app, make_temp_dir


def _read_json_lines(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class TestStartupTraceApi:
    def test_startup_trace_persists_ready_status_and_phase_progression(self) -> None:
        with make_temp_dir() as temp_dir:
            app = create_test_app(Path(temp_dir))
            status_path = app.state.settings.startup_status_file
            events_path = app.state.settings.startup_events_file

            with TestClient(app) as client:
                ready_response = client.get("/health/ready")
                assert ready_response.status_code == 200
                assert status_path.exists()

                snapshot = json.loads(status_path.read_text(encoding="utf-8"))
                assert snapshot["status"] == "ready"
                assert snapshot["startup_phase"] == "ready"
                assert snapshot["startup_last_checkpoint"] == "ready:ok"
                assert snapshot["startup_elapsed_ms"] >= 0

            assert events_path.exists()
            events = _read_json_lines(events_path)
            signatures = {
                (str(item["phase"]), str(item["checkpoint"]), str(item["outcome"]))
                for item in events
            }

            assert ("workspace", "workspace", "start") in signatures
            assert ("database_migrate", "database_migrate", "ok") in signatures
            assert ("bootstrap_defaults", "bootstrap_defaults", "start") in signatures
            assert ("bootstrap_defaults", "bootstrap_defaults/session_flush", "start") in signatures
            assert ("runtime_contract", "runtime_contract", "ok") in signatures
            assert ("ready", "ready", "ok") in signatures
