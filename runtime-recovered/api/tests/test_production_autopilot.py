from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
import sys
from typing import Any

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.intelligence.storage import (  # noqa: E402
    connect,
    init_intelligence_db,
    upsert_intelligence_account_config,
    utc_now_iso,
)
from atnos.production.autopilot import ProductionAutopilotService  # noqa: E402
from atnos.production.content_loop import AutonomousContentLoopService  # noqa: E402
from atnos.production.orchestrator import ProductionOrchestrator  # noqa: E402
from test_support import create_test_app  # noqa: E402


def _seed_account(settings, account_id: str = "acct-1") -> None:
    upsert_intelligence_account_config(
        settings,
        account_id=account_id,
        niches=["finance"],
        region="US",
        content_profile="wealth_blueprint",
        automation_mode="auto_private",
    )


def _seed_topic(
    settings,
    *,
    topic_id: str,
    account_id: str = "acct-1",
    score: float = 70.0,
    status: str = "approved",
    shorts_potential: bool = True,
    clickbait_risk: bool = False,
    llm_unavailable: bool = False,
) -> None:
    now = utc_now_iso()
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO topic_queue (
                id, account_id, title, description, status, total_score,
                clickbait_risk, shorts_potential, llm_unavailable, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                topic_id,
                account_id,
                f"Topic {topic_id}",
                "Test topic",
                status,
                score,
                int(clickbait_risk),
                int(shorts_potential),
                int(llm_unavailable),
                now,
                now,
            ),
        )
        db.commit()


def _write_ready_voice_provider_report(settings) -> Path:
    reports_dir = settings.data_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / "voice_provider_doctor_20260606-123000.json"
    report_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "public_voice_ready": True,
                "selected_tts_backend": "openai_tts",
                "providers": {
                    "elevenlabs": {
                        "configured": True,
                        "live_checked": True,
                        "live_ready": False,
                        "status_code": 401,
                        "error": "detected_unusual_activity",
                    },
                    "openai_tts": {
                        "configured": True,
                        "live_checked": True,
                        "live_ready": True,
                        "status_code": None,
                        "error": None,
                        "model": "gpt-4o-mini-tts",
                    },
                    "f5_tts": {
                        "configured": True,
                        "live_checked": True,
                        "live_ready": False,
                        "status_code": None,
                        "error": "All connection attempts failed",
                    },
                },
                "next_action": "monitor_voice_provider",
            }
        ),
        encoding="utf-8",
    )
    return report_path


async def _fake_orchestrator_start(self, *, account_id: str, topic_id: str) -> dict[str, Any]:
    job_id = f"job-{topic_id}"
    now = utc_now_iso()
    with connect(self.settings) as db:
        db.execute(
            """
            INSERT INTO pipeline_jobs (
                id, account_id, topic_id, status, current_step, artifact_dir, qa_report_path,
                upload_package_path, created_at, updated_at
            )
            VALUES (?, ?, ?, 'qa_passed', 'upload_package', '', 'qa.json', 'package.json', ?, ?)
            """,
            (job_id, account_id, topic_id, now, now),
        )
        db.execute("UPDATE topic_queue SET status = 'in_production', updated_at = ? WHERE id = ?", (now, topic_id))
        db.commit()
    return {"id": job_id, "account_id": account_id, "topic_id": topic_id, "status": "qa_passed"}


def test_production_autopilot_starts_highest_score_approved_topic(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.shorts_require_premium_voice_before_render = True
    asyncio.run(init_intelligence_db(settings))
    _seed_account(settings)
    _seed_topic(settings, topic_id="topic-low", score=41)
    _seed_topic(settings, topic_id="topic-high", score=91)
    _write_ready_voice_provider_report(settings)
    calls: list[dict[str, str]] = []

    async def fake_start(self, *, account_id: str, topic_id: str):
        calls.append({"account_id": account_id, "topic_id": topic_id})
        return await _fake_orchestrator_start(self, account_id=account_id, topic_id=topic_id)

    monkeypatch.setattr(ProductionOrchestrator, "start", fake_start)

    result = asyncio.run(ProductionAutopilotService(settings).run_once(max_jobs=1))

    assert result["status"] == "completed"
    assert result["started_count"] == 1
    assert calls == [{"account_id": "acct-1", "topic_id": "topic-high"}]
    assert result["jobs"][0]["job_id"] == "job-topic-high"
    assert result["jobs"][0]["private_upload"] == "human_review_required"


def test_production_autopilot_blocks_when_premium_voice_provider_is_not_ready(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.shorts_require_premium_voice_before_render = True
    asyncio.run(init_intelligence_db(settings))
    _seed_account(settings)
    _seed_topic(settings, topic_id="topic-ready", score=88)

    async def forbidden_start(*_args, **_kwargs):
        raise AssertionError("Autopilot must not start public-ready production without a live premium voice provider.")

    monkeypatch.setattr(ProductionOrchestrator, "start", forbidden_start)

    result = asyncio.run(ProductionAutopilotService(settings).run_once(max_jobs=1))

    assert result["status"] == "blocked"
    assert result["started_count"] == 0
    assert result["errors"][0]["blocker"] == "premium_voice_provider_not_ready"
    assert result["errors"][0]["next_action"] == "run_voice_provider_doctor"
    assert result["voice_provider"]["public_voice_ready"] is False


def test_production_autopilot_returns_busy_when_any_job_is_active(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_account(settings)
    _seed_topic(settings, topic_id="topic-ready", score=80)
    now = utc_now_iso()
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO pipeline_jobs (
                id, account_id, topic_id, status, current_step, artifact_dir, created_at, updated_at
            )
            VALUES ('job-active', 'acct-1', 'topic-other', 'running', 'video', '', ?, ?)
            """,
            (now, now),
        )
        db.commit()

    async def forbidden_start(*_args, **_kwargs):
        raise AssertionError("Autopilot must not start a second active job for the same account.")

    monkeypatch.setattr(ProductionOrchestrator, "start", forbidden_start)

    result = asyncio.run(ProductionAutopilotService(settings).run_once(max_jobs=1))

    assert result["status"] == "busy"
    assert result["started_count"] == 0
    assert result["active_jobs"][0]["id"] == "job-active"


def test_production_autopilot_skips_account_without_content_profile(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    upsert_intelligence_account_config(
        settings,
        account_id="acct-1",
        niches=["finance"],
        region="US",
        content_profile=None,
    )
    _seed_topic(settings, topic_id="topic-ready", score=80)

    async def forbidden_start(*_args, **_kwargs):
        raise AssertionError("Autopilot must not start production for an unprofiled content account.")

    monkeypatch.setattr(ProductionOrchestrator, "start", forbidden_start)

    result = asyncio.run(ProductionAutopilotService(settings).run_once(max_jobs=1))

    assert result["status"] == "idle"
    assert result["started_count"] == 0


def test_production_autopilot_skips_manual_channel(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    upsert_intelligence_account_config(
        settings,
        account_id="acct-manual",
        niches=["finance"],
        region="US",
        content_profile="wealth_blueprint",
        automation_mode="manual",
    )
    _seed_topic(settings, topic_id="topic-manual", account_id="acct-manual", score=88)

    async def forbidden_start(*_args, **_kwargs):
        raise AssertionError("Manual channels must never reach the autonomous production selector.")

    monkeypatch.setattr(ProductionOrchestrator, "start", forbidden_start)

    result = asyncio.run(ProductionAutopilotService(settings).run_once(max_jobs=1))

    assert result["status"] == "idle"
    assert result["started_count"] == 0


def test_content_loop_does_not_generate_or_render_for_manual_channel(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    upsert_intelligence_account_config(
        settings,
        account_id="acct-manual",
        niches=["finance"],
        region="US",
        content_profile="wealth_blueprint",
        automation_mode="manual",
    )

    async def forbidden_start(*_args, **_kwargs):
        raise AssertionError("Manual channels must never start autonomous rendering.")

    monkeypatch.setattr(ProductionOrchestrator, "start", forbidden_start)

    result = asyncio.run(AutonomousContentLoopService(settings).run_once(max_jobs=1, account_id="acct-manual"))

    assert result["status"] == "idle"
    assert result["started_count"] == 0
    assert result["generated_count"] == 0


def test_production_autopilot_endpoint_runs_once(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_account(settings)
    _seed_topic(settings, topic_id="topic-api", score=77)
    _write_ready_voice_provider_report(settings)

    async def fake_start(self, *, account_id: str, topic_id: str):
        return await _fake_orchestrator_start(self, account_id=account_id, topic_id=topic_id)

    monkeypatch.setattr(ProductionOrchestrator, "start", fake_start)

    with TestClient(app) as client:
        response = client.post("/api/production/autopilot/run-once", json={"max_jobs": 1})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["started_count"] == 1
    assert payload["jobs"][0]["topic_id"] == "topic-api"


def test_autonomous_content_loop_auto_approves_strong_pending_topic_and_starts_production(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_account(settings)
    _seed_topic(settings, topic_id="topic-pending-strong", score=86, status="pending")
    _write_ready_voice_provider_report(settings)
    calls: list[str] = []

    async def fake_start(self, *, account_id: str, topic_id: str):
        calls.append(topic_id)
        return await _fake_orchestrator_start(self, account_id=account_id, topic_id=topic_id)

    monkeypatch.setattr(ProductionOrchestrator, "start", fake_start)

    result = asyncio.run(AutonomousContentLoopService(settings).run_once(max_jobs=1, account_id="acct-1"))

    assert result["status"] == "completed"
    assert result["approved_count"] == 1
    assert result["started_count"] == 1
    assert calls == ["topic-pending-strong"]
    with connect(settings) as db:
        topic = db.execute("SELECT status FROM topic_queue WHERE id = 'topic-pending-strong'").fetchone()
    assert topic["status"] == "in_production"


def test_autonomous_content_loop_best_effort_approves_top_viable_topic_when_strict_threshold_is_not_met(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_account(settings)
    _seed_topic(settings, topic_id="topic-best-effort", score=52, status="pending", llm_unavailable=True)
    _write_ready_voice_provider_report(settings)
    calls: list[str] = []

    async def fake_start(self, *, account_id: str, topic_id: str):
        calls.append(topic_id)
        return await _fake_orchestrator_start(self, account_id=account_id, topic_id=topic_id)

    monkeypatch.setattr(ProductionOrchestrator, "start", fake_start)

    result = asyncio.run(AutonomousContentLoopService(settings).run_once(max_jobs=1, account_id="acct-1"))

    assert result["status"] == "completed"
    assert result["approved_count"] == 1
    assert result["accounts"][0]["strict_approved_count"] == 0
    assert result["accounts"][0]["best_effort_approved_count"] == 1
    assert result["accounts"][0]["best_effort_topic"]["id"] == "topic-best-effort"
    assert result["started_count"] == 1
    assert calls == ["topic-best-effort"]
    with connect(settings) as db:
        topic = db.execute("SELECT status, inspired_by FROM topic_queue WHERE id = 'topic-best-effort'").fetchone()
    assert topic["status"] == "in_production"
    assert "best_viable_auto_approved" in topic["inspired_by"]


def test_autonomous_content_loop_best_effort_keeps_subthreshold_topic_pending(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_account(settings)
    _seed_topic(settings, topic_id="topic-too-weak-for-best-effort", score=49, status="pending")
    _write_ready_voice_provider_report(settings)

    async def forbidden_start(*_args, **_kwargs):
        raise AssertionError("Subthreshold best-effort topic must not reach production.")

    monkeypatch.setattr(ProductionOrchestrator, "start", forbidden_start)

    result = asyncio.run(AutonomousContentLoopService(settings).run_once(max_jobs=1, account_id="acct-1"))

    assert result["started_count"] == 0
    assert result["approved_count"] == 0
    assert result["accounts"][0]["best_effort_approved_count"] == 0
    with connect(settings) as db:
        topic = db.execute("SELECT status FROM topic_queue WHERE id = 'topic-too-weak-for-best-effort'").fetchone()
    assert topic["status"] == "pending"


def test_autonomous_content_loop_rejects_low_score_topic_without_starting_production(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_account(settings)
    _seed_topic(settings, topic_id="topic-weak", score=22, status="pending")

    async def forbidden_start(*_args, **_kwargs):
        raise AssertionError("Rejected low-score topic must not reach production.")

    monkeypatch.setattr(ProductionOrchestrator, "start", forbidden_start)

    result = asyncio.run(AutonomousContentLoopService(settings).run_once(max_jobs=1, account_id="acct-1"))

    assert result["started_count"] == 0
    assert result["rejected_count"] == 1
    with connect(settings) as db:
        topic = db.execute("SELECT status FROM topic_queue WHERE id = 'topic-weak'").fetchone()
    assert topic["status"] == "rejected"
