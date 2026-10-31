from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.intelligence.storage import connect, init_intelligence_db, upsert_intelligence_account_config, utc_now_iso
from atnos.production.autopilot import ProductionAutopilotService
from atnos.production.job_recovery import ProductionJobRecoveryService
from atnos.production.orchestrator import ProductionOrchestrator
from atnos.production.scene_checkpoint import SceneCheckpointStore
from test_support import create_test_app


def _old_iso(minutes: int = 120) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()


def _seed_topic(settings, *, topic_id: str, status: str = "in_production", score: float = 80.0) -> None:
    now = utc_now_iso()
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO topic_queue (
                id, account_id, title, description, status, total_score, created_at, updated_at
            )
            VALUES (?, 'acct-1', ?, 'Test topic', ?, ?, ?, ?)
            """,
            (topic_id, f"Topic {topic_id}", status, score, now, now),
        )
        db.commit()


def _seed_job(
    settings,
    *,
    job_id: str,
    topic_id: str,
    status: str = "running",
    current_step: str = "script",
    updated_at: str | None = None,
    artifact_dir: str = "",
    voice: dict | None = None,
    asset_plan: dict | None = None,
) -> None:
    timestamp = updated_at or utc_now_iso()
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO pipeline_jobs (
                id, account_id, topic_id, status, current_step, artifact_dir,
                audio_timestamps, asset_plan, created_at, updated_at
            )
            VALUES (?, 'acct-1', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                topic_id,
                status,
                current_step,
                artifact_dir,
                json.dumps(voice or {}),
                json.dumps(asset_plan or {}),
                timestamp,
                timestamp,
            ),
        )
        db.execute(
            """
            INSERT INTO pipeline_job_steps (
                job_id, step, status, progress, attempt, message, started_at
            )
            VALUES (?, ?, 'running', 0.25, 1, 'still running', ?)
            """,
            (job_id, current_step, timestamp),
        )
        db.commit()


def test_recovery_marks_stale_running_job_error_and_restores_topic(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_topic(settings, topic_id="topic-stale")
    _seed_job(settings, job_id="job-stale", topic_id="topic-stale", updated_at=_old_iso())

    result = ProductionJobRecoveryService(settings).recover_stale_jobs(stale_after_minutes=60)

    assert result["recovered_count"] == 1
    assert result["restored_topic_count"] == 1
    assert result["jobs"][0]["job_id"] == "job-stale"
    assert result["jobs"][0]["topic_restored_to_approved"] is True
    with connect(settings) as db:
        job = db.execute("SELECT status, error, review_status FROM pipeline_jobs WHERE id = 'job-stale'").fetchone()
        topic = db.execute("SELECT status FROM topic_queue WHERE id = 'topic-stale'").fetchone()
        step = db.execute(
            "SELECT status, message, completed_at FROM pipeline_job_steps WHERE job_id = 'job-stale' AND step = 'script'"
        ).fetchone()

    assert job["status"] == "error"
    assert job["review_status"] == "stale_recovered"
    assert "Recovered stale production job" in job["error"]
    assert topic["status"] == "approved"
    assert step["status"] == "error"
    assert step["completed_at"]


def test_recovery_does_not_touch_fresh_running_job(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_topic(settings, topic_id="topic-fresh")
    _seed_job(settings, job_id="job-fresh", topic_id="topic-fresh", updated_at=utc_now_iso())

    result = ProductionJobRecoveryService(settings).recover_stale_jobs(stale_after_minutes=60)

    assert result["recovered_count"] == 0
    with connect(settings) as db:
        job = db.execute("SELECT status FROM pipeline_jobs WHERE id = 'job-fresh'").fetchone()
        topic = db.execute("SELECT status FROM topic_queue WHERE id = 'topic-fresh'").fetchone()

    assert job["status"] == "running"
    assert topic["status"] == "in_production"


def test_restart_recovery_queues_only_verified_video_checkpoint_for_resume(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_topic(settings, topic_id="topic-resume")
    artifact_dir = tmp_path / "production" / "job-resume"
    video_dir = artifact_dir / "video"
    video_dir.mkdir(parents=True)
    narration = artifact_dir / "narration.wav"
    narration.write_bytes(b"reusable narration")
    scenes = [{"scene_id": index, "visual_prompt": f"scene {index}"} for index in range(1, 6)]
    _seed_job(
        settings,
        job_id="job-resume",
        topic_id="topic-resume",
        current_step="video",
        updated_at=utc_now_iso(),
        artifact_dir=str(artifact_dir),
        voice={"output_file": str(narration)},
        asset_plan={"scenes": scenes},
    )
    checkpoint = SceneCheckpointStore(
        video_dir / "scene_checkpoint.json",
        job_id="job-resume",
        scene_ids=[f"scene_{index:02d}" for index in range(1, 6)],
    )
    scene_one = video_dir / "s01.mp4"
    scene_one.write_bytes(b"durable scene artifact")
    input_hash = "a" * 64
    checkpoint.begin("scene_01", input_hash=input_hash)
    checkpoint.complete(
        "scene_01",
        input_hash=input_hash,
        artifact_path=scene_one,
        result={"file": str(scene_one), "status": "generated"},
    )
    checkpoint.begin("scene_02", input_hash="b" * 64)

    result = ProductionJobRecoveryService(settings).recover_stale_jobs(
        stale_after_minutes=60,
        resume_video_jobs=True,
        recover_all_active=True,
    )

    assert result["recovered_count"] == 1
    assert result["resume_queued_count"] == 1
    assert result["resume_job_ids"] == ["job-resume"]
    assert result["restored_topic_count"] == 0
    assert result["jobs"][0]["resume_queued"] is True
    with connect(settings) as db:
        job = db.execute(
            "SELECT status, current_step, error, review_status FROM pipeline_jobs WHERE id = 'job-resume'"
        ).fetchone()
        topic = db.execute("SELECT status FROM topic_queue WHERE id = 'topic-resume'").fetchone()
    assert dict(job) == {
        "status": "queued",
        "current_step": "video",
        "error": None,
        "review_status": "resume_queued",
    }
    assert topic["status"] == "in_production"


def test_restart_recovery_rejects_video_resume_without_verified_inputs(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_topic(settings, topic_id="topic-invalid-resume")
    artifact_dir = tmp_path / "production" / "job-invalid-resume"
    artifact_dir.mkdir(parents=True)
    _seed_job(
        settings,
        job_id="job-invalid-resume",
        topic_id="topic-invalid-resume",
        current_step="video",
        updated_at=utc_now_iso(),
        artifact_dir=str(artifact_dir),
        voice={"output_file": str(artifact_dir / "missing.wav")},
        asset_plan={"scenes": [{"scene_id": 1}]},
    )

    result = ProductionJobRecoveryService(settings).recover_stale_jobs(
        resume_video_jobs=True,
        recover_all_active=True,
    )

    assert result["resume_queued_count"] == 0
    assert result["restored_topic_count"] == 1
    assert result["jobs"][0]["resume_queued"] is False
    assert result["jobs"][0]["resume_reason"] == "reusable_narration_missing"
    with connect(settings) as db:
        job = db.execute(
            "SELECT status, review_status FROM pipeline_jobs WHERE id = 'job-invalid-resume'"
        ).fetchone()
        topic = db.execute("SELECT status FROM topic_queue WHERE id = 'topic-invalid-resume'").fetchone()
    assert job["status"] == "error"
    assert job["review_status"] == "stale_recovered"
    assert topic["status"] == "approved"


def test_recovery_endpoint_emits_ws_event(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))

    with TestClient(app) as client:
        _seed_topic(settings, topic_id="topic-api")
        _seed_job(settings, job_id="job-api", topic_id="topic-api", updated_at=_old_iso())
        response = client.post("/api/production/recover-stale", json={"stale_after_minutes": 60})

    assert response.status_code == 200
    assert response.json()["recovered_count"] == 1
    events = getattr(app.state, "production_ws_events", [])
    assert any(event.get("type") == "pipeline_recovery" and event.get("recovered_count") == 1 for event in events)


def test_autopilot_recovers_stale_job_before_selecting_topic(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    upsert_intelligence_account_config(
        settings,
        account_id="acct-1",
        niches=["finance"],
        region="US",
        content_profile="wealth_blueprint",
        automation_mode="auto_private",
    )
    _seed_topic(settings, topic_id="topic-stale", status="in_production", score=90)
    _seed_job(settings, job_id="job-stale", topic_id="topic-stale", updated_at=_old_iso())
    calls: list[str] = []

    async def fake_start(self, *, account_id: str, topic_id: str):
        calls.append(topic_id)
        return {"id": f"job-new-{topic_id}", "account_id": account_id, "topic_id": topic_id, "status": "qa_failed"}

    monkeypatch.setattr(
        "atnos.production.autopilot.build_voice_provider_status",
        lambda settings: {"public_voice_ready": True},
    )
    monkeypatch.setattr(ProductionOrchestrator, "start", fake_start)

    result = asyncio.run(ProductionAutopilotService(settings).run_once(max_jobs=1, account_id="acct-1"))

    assert result["recovery"]["recovered_count"] == 1
    assert result["started_count"] == 1
    assert calls == ["topic-stale"]
