from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.intelligence.storage import connect, utc_now_iso  # noqa: E402
from atnos.production.provider_doctor import build_provider_doctor_status  # noqa: E402
from test_support import bootstrap_test_app, create_test_app  # noqa: E402


def test_provider_doctor_returns_single_operator_snapshot_without_secrets(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    bootstrap_test_app(app)

    with TestClient(app) as client:
        response = client.get("/api/production/provider-doctor", params={"include_oauth": "false"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "needs_attention"
    assert "voice_provider" in payload["critical_blockers"]
    assert "visual_backend" in payload["critical_blockers"]
    assert "public_upload" in payload["critical_blockers"]
    assert {card["key"] for card in payload["cards"]} >= {
        "voice_provider",
        "visual_backend",
        "youtube_oauth",
        "public_upload",
        "cost_guard",
        "latest_production_evidence",
    }
    action_ids = {action["id"] for action in payload["operator_actions"]}
    assert "run_voice_provider_check" in action_ids
    assert "run_visual_backend_check" in action_ids
    assert all("publish" not in str(action["id"]).casefold() for action in payload["operator_actions"])
    assert "api_key" not in json.dumps(payload).casefold()
    assert "secret" not in json.dumps(payload).casefold()


def test_provider_doctor_blocks_stale_voice_evidence(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    bootstrap_test_app(app)

    monkeypatch.setattr(
        "atnos.production.provider_doctor.build_voice_provider_status",
        lambda _settings: {
            "checked": True,
            "report_stale": True,
            "report_age_hours": 12.0,
            "report_max_age_hours": 6.0,
            "public_voice_ready": False,
            "selected_tts_backend": "openai_tts",
            "next_action": "run_voice_provider_doctor",
            "providers": {"openai_tts": {"live_ready": True}},
            "report_path": "data/reports/stale_voice.json",
        },
    )

    payload = build_provider_doctor_status(app.state.settings, include_oauth=False)
    voice = next(card for card in payload["cards"] if card["key"] == "voice_provider")

    assert voice["status"] == "blocked"
    assert voice["next_action"] == "run_voice_provider_doctor"
    assert voice["evidence"]["report_stale"] is True
    assert "voice_provider" in payload["critical_blockers"]


def test_provider_doctor_surfaces_latest_fallback_render_evidence(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    bootstrap_test_app(app)
    settings = app.state.settings
    now = utc_now_iso()
    artifact_dir = settings.data_dir / "production" / "acct-1" / "prod-failed-1"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    qa_path = artifact_dir / "qa_report.json"
    qa_report = {
        "overall": "failed",
        "auto_uploadable": False,
        "blocking_failures": ["final_mp4_placeholder_fallback"],
    }
    qa_path.write_text(json.dumps(qa_report), encoding="utf-8")
    audio_timestamps = {
        "quality_tier": "local_neural_fallback",
        "voice_backend_grade": "acceptable_neural_fallback",
        "engines": ["kokoro_local"],
    }
    video_manifest = {
        "backend": {"mode": "placeholder"},
        "fallback_used": True,
        "reason": "billing_hard_limit_reached",
        "clips": [{"status": "generated", "backend": "motion_graphic"}],
    }
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO pipeline_jobs (
                id, account_id, topic_id, status, current_step, audio_timestamps,
                video_manifest, qa_report, artifact_dir, qa_report_path, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "prod-failed-1",
                "acct-1",
                "topic-1",
                "qa_failed",
                "qa",
                json.dumps(audio_timestamps),
                json.dumps(video_manifest),
                json.dumps(qa_report),
                str(artifact_dir),
                str(qa_path),
                now,
                now,
            ),
        )
        db.commit()

    with TestClient(app) as client:
        response = client.get(
            "/api/production/provider-doctor",
            params={"account_id": "acct-1", "include_oauth": "false"},
        )

    assert response.status_code == 200
    payload = response.json()
    latest = next(card for card in payload["cards"] if card["key"] == "latest_production_evidence")
    assert latest["status"] == "blocked"
    assert latest["evidence"]["job_id"] == "prod-failed-1"
    assert "latest_job_voice_not_public_quality" in latest["evidence"]["blockers"]
    assert "latest_job_visual_fallback_used" in latest["evidence"]["blockers"]
    assert "latest_job_qa_not_uploadable" in latest["evidence"]["blockers"]
    voice = next(card for card in payload["cards"] if card["key"] == "voice_provider")
    visual = next(card for card in payload["cards"] if card["key"] == "visual_backend")
    assert voice["evidence"]["latest_job_voice"]["quality_tier"] == "local_neural_fallback"
    assert visual["evidence"]["latest_job_visual"]["fallback_used"] is True
    actions = {action["id"]: action for action in payload["operator_actions"]}
    assert actions["regenerate_latest_failed_job"]["endpoint"] == "/api/production/jobs/prod-failed-1/regenerate"
    assert actions["regenerate_latest_failed_job"]["safe_to_run"] is False
    assert actions["regenerate_latest_failed_job"]["requires_owner_action"] is True


def test_provider_doctor_action_executes_safe_voice_check(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    bootstrap_test_app(app)
    report_path = app.state.settings.data_dir / "reports" / "voice_provider_doctor_action.json"

    async def fake_voice_check(_settings):
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("{}", encoding="utf-8")
        return {
            "generated_at": "2026-06-20T00:00:00+00:00",
            "public_voice_ready": False,
            "next_action": "enable_openai_tts_cloud_backend",
            "report_path": str(report_path),
            "providers": {
                "openai_tts": {
                    "configured": True,
                    "live_checked": True,
                    "live_ready": False,
                    "status_code": 429,
                    "error": "insufficient_quota",
                }
            },
        }

    monkeypatch.setattr("atnos.api.routes.production.write_voice_provider_doctor_report", fake_voice_check)

    with TestClient(app) as client:
        response = client.post("/api/production/provider-doctor/actions/run_voice_provider_check")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["action"]["id"] == "run_voice_provider_check"
    assert payload["action"]["safe_to_run"] is True
    assert payload["action"]["requires_owner_action"] is True
    assert payload["result"]["next_action"] == "enable_openai_tts_cloud_backend"
    assert payload["provider_doctor"]["status"] == "needs_attention"


def test_provider_doctor_action_rejects_owner_gated_action(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    bootstrap_test_app(app)

    with TestClient(app) as client:
        response = client.post("/api/production/provider-doctor/actions/review_public_upload_gate")

    assert response.status_code == 403
    payload = response.json()
    assert payload["detail"]["message"] == "Provider Doctor action is manual or requires owner approval."
    assert payload["detail"]["action"]["requires_owner_action"] is True
