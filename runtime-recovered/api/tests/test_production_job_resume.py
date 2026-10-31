from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.intelligence.storage import connect, init_intelligence_db, utc_now_iso
from atnos.production.job_resume import (
    _acquire_job_execution_lease,
    _release_job_execution_lease,
    resume_video_job,
)
from atnos.production.models import StepResult
from atnos.production.orchestrator import ProductionOrchestrator
from test_support import create_test_app


def test_job_execution_lease_blocks_a_concurrent_process(tmp_path: Path) -> None:
    lease = _acquire_job_execution_lease(tmp_path, "job-one")
    try:
        with pytest.raises(RuntimeError, match="already running"):
            _acquire_job_execution_lease(tmp_path, "job-one")
    finally:
        _release_job_execution_lease(lease)

    assert not (tmp_path / ".production-execution.lock").exists()


def test_job_execution_lease_recovers_a_stale_process_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / ".production-execution.lock"
    lock_path.write_text(
        json.dumps({"job_id": "job-one", "pid": 2_147_483_647, "token": "stale"}),
        encoding="utf-8",
    )

    lease = _acquire_job_execution_lease(tmp_path, "job-one")
    try:
        assert lease["pid"] > 0
        assert lease["token"] != "stale"
    finally:
        _release_job_execution_lease(lease)


def test_resume_video_job_reconstructs_context_and_finishes_same_job(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    artifact_dir = tmp_path / "production" / "job-resume-service"
    artifact_dir.mkdir(parents=True)
    narration = artifact_dir / "narration.wav"
    narration.write_bytes(b"reusable narration")
    now = utc_now_iso()
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO topic_queue (
                id, account_id, title, description, status, total_score, created_at, updated_at
            ) VALUES ('topic-resume-service', 'acct-1', 'A finance lesson', '', 'in_production', 90, ?, ?)
            """,
            (now, now),
        )
        db.execute(
            """
            INSERT INTO pipeline_jobs (
                id, account_id, topic_id, status, current_step, artifact_dir,
                hooks, script, audio_timestamps, asset_plan, review_status, created_at, updated_at
            ) VALUES (?, 'acct-1', 'topic-resume-service', 'queued', 'video', ?, ?, ?, ?, ?, 'resume_queued', ?, ?)
            """,
            (
                "job-resume-service",
                str(artifact_dir),
                json.dumps({"selected": {"text": "A strong hook"}}),
                json.dumps({"segments": [{"id": "s01", "text": "Narration"}]}),
                json.dumps({"output_file": str(narration)}),
                json.dumps({"scenes": [{"scene_id": 1, "visual_prompt": "A budget chart"}]}),
                now,
                now,
            ),
        )
        db.commit()

    async def fake_guard(self, context, step_name, runner, progress_base):
        payloads = {
            "video": {"status": "ready", "video_raw_path": str(artifact_dir / "video_raw.mp4")},
            "assembly": {"subtitle_file": str(artifact_dir / "captions.srt")},
            "thumbnail": {"thumbnail_path": str(artifact_dir / "thumbnail.jpg")},
            "qa": {"auto_uploadable": True, "passed": True},
            "upload_package": {"status": "ready"},
        }
        artifact_path = artifact_dir / f"{step_name}.json"
        artifact_path.write_text(json.dumps(payloads[step_name]), encoding="utf-8")
        return StepResult(
            artifact_name=step_name,
            artifact_path=artifact_path,
            message=f"{step_name} complete",
            payload=payloads[step_name],
        )

    monkeypatch.setattr(ProductionOrchestrator, "_run_step_with_runtime_guard", fake_guard)
    monkeypatch.setattr("atnos.production.job_resume.persist_context_manifest", lambda *args, **kwargs: None)

    result = asyncio.run(resume_video_job(settings, "job-resume-service"))

    assert result["id"] == "job-resume-service"
    assert result["status"] == "qa_passed"
    with connect(settings) as db:
        job = db.execute(
            """
            SELECT status, current_step, review_status, error, video_manifest, qa_report
            FROM pipeline_jobs WHERE id = 'job-resume-service'
            """
        ).fetchone()
    assert job["status"] == "qa_passed"
    assert job["current_step"] == "upload_package"
    assert job["review_status"] == "resume_completed"
    assert job["error"] is None
    assert json.loads(job["video_manifest"])["status"] == "ready"
    assert json.loads(job["qa_report"])["auto_uploadable"] is True
