from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from atnos.learning.reference_learning_service import ReferenceLearningService
from test_support import create_test_app


def test_reference_learning_status_starts_as_not_started(tmp_path: Path) -> None:
    settings = create_test_app(tmp_path).state.settings

    status = ReferenceLearningService(settings).status()

    assert status["status"] == "not_started"
    assert status["target_count"] == 40
    assert status["policy"] == "aggregate_patterns_only_no_source_copy"


def test_reference_learning_start_launches_exact_scientific_batch(tmp_path: Path, monkeypatch) -> None:
    settings = create_test_app(tmp_path).state.settings
    captured: dict[str, object] = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(pid=4242)

    monkeypatch.setattr("atnos.learning.reference_learning_service.subprocess.Popen", fake_popen)
    monkeypatch.setattr(ReferenceLearningService, "_pid_alive", staticmethod(lambda pid: pid == 4242))

    status = ReferenceLearningService(settings).start(target_count=40)

    command = [str(item) for item in captured["command"]]
    assert status["status"] == "running"
    assert status["pid"] == 4242
    assert command[command.index("--per-channel") + 1] == "8"
    assert command[command.index("--target-total") + 1] == "40"
    assert "--state-file" in command


def test_reference_learning_rejects_non_scientific_batch_size(tmp_path: Path) -> None:
    settings = create_test_app(tmp_path).state.settings

    try:
        ReferenceLearningService(settings).start(target_count=20)
    except ValueError as exc:
        assert "dokładnie 40" in str(exc)
    else:
        raise AssertionError("Expected a strict 40-video batch validation error")


def test_reference_learning_rejects_incomplete_completed_checkpoint(tmp_path: Path) -> None:
    settings = create_test_app(tmp_path).state.settings
    service = ReferenceLearningService(settings)
    service._write_state(
        {
            "status": "completed",
            "phase": "completed",
            "target_count": 40,
            "selected_count": 36,
        }
    )

    status = service.status()

    assert status["status"] == "failed"
    assert status["phase"] == "insufficient_sample"
    assert "36 z wymaganych 40" in status["message"]


def test_reference_learning_exposes_evidence_channel_distribution(tmp_path: Path) -> None:
    settings = create_test_app(tmp_path).state.settings
    service = ReferenceLearningService(settings)
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(
        json.dumps(
            {
                "selected_videos": [
                    {"configured_channel": "@one"},
                    {"configured_channel": "@one"},
                    {"configured_channel": "@two"},
                ]
            }
        ),
        encoding="utf-8",
    )
    service._write_state(
        {
            "status": "completed",
            "target_count": 3,
            "selected_count": 3,
            "evidence_path": str(evidence_path),
        }
    )

    status = service.status()

    assert status["channel_distribution"] == {"@one": 2, "@two": 1}


def test_reference_learning_api_exposes_status_and_start(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    monkeypatch.setattr(
        ReferenceLearningService,
        "status",
        lambda self: {"status": "completed", "target_count": 40, "selected_count": 40},
    )
    monkeypatch.setattr(
        ReferenceLearningService,
        "start",
        lambda self, **kwargs: {"status": "running", "target_count": kwargs["target_count"]},
    )

    with TestClient(app) as client:
        status_response = client.get("/api/production/reference-learning/status")
        start_response = client.post(
            "/api/production/reference-learning/start",
            json={"profile_key": "wealth_blueprint", "target_count": 40},
        )

    assert status_response.status_code == 200
    assert status_response.json()["selected_count"] == 40
    assert start_response.status_code == 202
    assert start_response.json()["status"] == "running"
