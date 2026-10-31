from __future__ import annotations

import json
from pathlib import Path
import sys

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from fastapi.testclient import TestClient

from atnos.intelligence.storage import connect, init_intelligence_db, utc_now_iso
from atnos.production.qa_gate import REQUIRED_QA_CHECK_NAMES
from test_support import create_test_app


def _seed_upload_ready_job(
    settings,
    root: Path,
    *,
    job_id: str = "prod-upload-1",
    language: str | None = None,
    audio_language: str | None = None,
) -> Path:
    # These tests isolate private-upload/OAuth behavior. Public promotion
    # quality is covered separately by test_production_quality_scheduler.py.
    settings.shorts_review_require_public_quality = False
    now = utc_now_iso()
    artifact_dir = root / "data" / "production" / "acct-1" / job_id
    asset_dir = artifact_dir / "assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    video_path = artifact_dir / "final_short.mp4"
    thumbnail_path = asset_dir / "thumbnail.png"
    subtitle_path = asset_dir / "captions.srt"
    qa_path = artifact_dir / "qa_report.json"
    package_path = artifact_dir / "youtube_private_upload_package.json"
    video_path.write_bytes(b"fake mp4 for mocked upload")
    thumbnail_path.write_bytes(b"fake png")
    subtitle_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nSiema\n", encoding="utf-8")
    qa_path.write_text(json.dumps(_valid_qa_report(), ensure_ascii=False), encoding="utf-8")
    package = {
        "review_id": f"review-{job_id}",
        "channel_account_id": "acct-1",
        "topic_id": "topic-1",
        "video_file_path": str(video_path),
        "thumbnail_file_path": str(thumbnail_path),
        "subtitle_file_path": str(subtitle_path),
        "qa_report_path": str(qa_path),
        "qa_recommendation": "pass",
        "auto_uploadable": True,
        "upload_metadata": {
            "title": "Testowy short finansowy",
            "description": "Opis testowy",
            "tags": ["shorts", "finanse"],
            "visibility": "private",
            "made_for_kids": False,
        },
        "status": "awaiting_human_review",
        "public_upload_allowed": False,
    }
    if language:
        package["upload_metadata"]["language"] = language
    if audio_language:
        package["upload_metadata"]["audio_language"] = audio_language
    package_path.write_text(json.dumps(package), encoding="utf-8")
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO topic_queue (
                id, account_id, title, description, status, total_score, created_at, updated_at
            )
            VALUES ('topic-1', 'acct-1', 'Test topic', 'Opis', 'in_production', 80, ?, ?)
            """,
            (now, now),
        )
        db.execute(
            """
            INSERT INTO pipeline_jobs (
                id, account_id, topic_id, status, current_step, artifact_dir, qa_report_path,
                upload_package_path, created_at, updated_at
            )
            VALUES (?, 'acct-1', 'topic-1', 'qa_passed', 'upload_package', ?, ?, ?, ?, ?)
            """,
            (job_id, str(artifact_dir), str(qa_path), str(package_path), now, now),
        )
        db.commit()
    return package_path


def _valid_qa_report() -> dict:
    required_checks = []
    for name in REQUIRED_QA_CHECK_NAMES:
        value: object = "test"
        if name == "Visual variety":
            value = {
                "generated_clip_count": 3,
                "placeholder_clip_count": 0,
                "cached_fallback_clip_count": 0,
                "non_generated_clip_count": 0,
                "clip_count": 3,
                "min_real_clips": 3,
                "required_real_clips": 3,
                "actual_visual_variety": {
                    "status": "pass",
                    "mean_hash_distance": 0.31,
                    "mean_top_crop_pixel_diff": 0.18,
                },
            }
        required_checks.append(
            {
                "name": name,
                "status": "pass",
                "value": value,
                "detail": "Seeded complete QA contract for private upload tests.",
            }
        )
    return {
        "overall": "passed",
        "auto_uploadable": True,
        "required_check_count": len(required_checks),
        "required_checks": required_checks,
        "blocking_failures": [],
        "advisory_failures": [],
        "upload_decision": {
            "status": "ready_for_private_upload",
            "auto_uploadable": True,
            "private_upload_allowed": True,
            "required_failures": [],
            "reason": "All required QA checks passed.",
        },
    }


def test_private_upload_returns_connection_required_without_youtube_oauth(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    import asyncio
    import atnos.production.private_upload as private_upload

    asyncio.run(init_intelligence_db(settings))
    _seed_upload_ready_job(settings, tmp_path, language="en", audio_language="en")
    slack_events: list[dict] = []

    monkeypatch.setattr(
        private_upload.SlackWebhookNotifier,
        "notify_pipeline_event",
        lambda self, event: slack_events.append(event) or {"status": "sent"},
    )

    with TestClient(app) as client:
        response = client.post("/api/production/jobs/prod-upload-1/upload-private")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "connection_required"
    assert payload["visibility"] == "private"
    assert payload["oauth"]["ready"] is False
    assert payload["oauth"]["readiness_endpoint"] == "GET /oauth/youtube-studio/status"
    assert "https://www.googleapis.com/auth/youtube.upload" in payload["oauth"]["missing_scopes"]
    assert payload["oauth"]["next_steps"]
    with connect(settings) as db:
        row = db.execute("SELECT status, upload_result FROM pipeline_jobs WHERE id = 'prod-upload-1'").fetchone()
    assert row["status"] == "upload_connection_required"
    stored = json.loads(row["upload_result"])
    assert stored["status"] == "connection_required"
    assert stored["oauth"]["readiness_endpoint"] == "GET /oauth/youtube-studio/status"
    assert [event["status"] for event in slack_events] == ["connection_required"]
    assert slack_events[0]["step"] == "private_upload"


def test_private_upload_blocks_when_authoritative_qa_report_has_failures(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    import asyncio
    import atnos.production.private_upload as private_upload

    asyncio.run(init_intelligence_db(settings))
    _seed_upload_ready_job(settings, tmp_path, language="en", audio_language="en")
    qa_path = tmp_path / "data" / "production" / "acct-1" / "prod-upload-1" / "qa_report.json"
    qa_path.write_text(
        json.dumps(
            {
                "overall": "failed",
                "auto_uploadable": False,
                "blocking_failures": ["Vertical 9:16 format"],
            }
        ),
        encoding="utf-8",
    )

    class FakeYouTube:
        def __init__(self, session, settings):
            raise AssertionError("YouTube upload must not be called when QA blocks the package.")

    monkeypatch.setattr(private_upload, "YouTubeConnectorService", FakeYouTube)

    with TestClient(app) as client:
        response = client.post("/api/production/jobs/prod-upload-1/upload-private")

    assert response.status_code == 409
    assert "QA report does not allow" in response.json()["detail"]


def test_private_upload_blocks_incomplete_qa_report_even_if_auto_uploadable_true(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    import asyncio
    import atnos.production.private_upload as private_upload

    asyncio.run(init_intelligence_db(settings))
    _seed_upload_ready_job(settings, tmp_path, language="en", audio_language="en")
    qa_path = tmp_path / "data" / "production" / "acct-1" / "prod-upload-1" / "qa_report.json"
    qa_path.write_text(json.dumps({"overall": "passed", "auto_uploadable": True}), encoding="utf-8")

    class FakeYouTube:
        def __init__(self, session, settings):
            raise AssertionError("YouTube upload must not be called with incomplete QA report.")

    monkeypatch.setattr(private_upload, "YouTubeConnectorService", FakeYouTube)

    with TestClient(app) as client:
        response = client.post("/api/production/jobs/prod-upload-1/upload-private")

    assert response.status_code == 409
    assert f"expected {len(REQUIRED_QA_CHECK_NAMES)} required quality checks" in response.json()["detail"]


def test_private_upload_blocks_legacy_qa_without_actual_clip_variety_measurement(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    import asyncio
    import atnos.production.private_upload as private_upload

    asyncio.run(init_intelligence_db(settings))
    _seed_upload_ready_job(settings, tmp_path, language="en", audio_language="en")
    qa_path = tmp_path / "data" / "production" / "acct-1" / "prod-upload-1" / "qa_report.json"
    qa_report = _valid_qa_report()
    for check in qa_report["required_checks"]:
        if check["name"] == "Visual variety":
            check["value"].pop("actual_visual_variety", None)
    qa_path.write_text(json.dumps(qa_report), encoding="utf-8")

    class FakeYouTube:
        def __init__(self, session, settings):
            raise AssertionError("YouTube upload must not be called with legacy visual QA.")

    monkeypatch.setattr(private_upload, "YouTubeConnectorService", FakeYouTube)

    with TestClient(app) as client:
        response = client.post("/api/production/jobs/prod-upload-1/upload-private")

    assert response.status_code == 409
    assert "actual rendered clip variety" in response.json()["detail"]


def test_private_upload_blocks_when_upload_decision_disallows_private_upload(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    import asyncio
    import atnos.production.private_upload as private_upload

    asyncio.run(init_intelligence_db(settings))
    _seed_upload_ready_job(settings, tmp_path)
    qa_path = tmp_path / "data" / "production" / "acct-1" / "prod-upload-1" / "qa_report.json"
    qa_report = _valid_qa_report()
    qa_report["upload_decision"] = {
        "status": "blocked",
        "auto_uploadable": False,
        "private_upload_allowed": False,
        "required_failures": ["Ollama QA review"],
        "reason": "Required QA checks failed; private upload is blocked until these checks pass.",
    }
    qa_path.write_text(json.dumps(qa_report), encoding="utf-8")

    class FakeYouTube:
        def __init__(self, session, settings):
            raise AssertionError("YouTube upload must not be called when upload_decision blocks the package.")

    monkeypatch.setattr(private_upload, "YouTubeConnectorService", FakeYouTube)

    with TestClient(app) as client:
        response = client.post("/api/production/jobs/prod-upload-1/upload-private")

    assert response.status_code == 409
    assert "private upload is blocked" in response.json()["detail"]
    assert "Ollama QA review" in response.json()["detail"]


def test_score_from_degraded_data_api_analytics_keeps_limited_feedback() -> None:
    import atnos.production.private_upload as private_upload

    score = private_upload._score_from_analytics(
        {
            "status": "ready_degraded",
            "fallback_source": "youtube_data_api_v3",
            "views": 2400,
            "likes": 180,
            "comments": 12,
            "shares": None,
            "average_view_percentage": None,
            "average_view_duration": None,
            "subscribers_gained": None,
        },
        window_hours=24,
    )

    assert score["status"] == "scored_degraded"
    assert score["degraded"] is True
    assert score["analytics_source"] == "youtube_data_api_v3"
    assert score["views"] == 2400
    assert score["views_score"] > 0
    assert score["engagement_score"] > 0
    assert score["retention_score"] == 0
    assert "Ograniczony feedback" in score["recommendation"]


def test_private_upload_schedules_24_48_72h_followups(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    import asyncio
    import atnos.production.private_upload as private_upload

    asyncio.run(init_intelligence_db(settings))
    _seed_upload_ready_job(settings, tmp_path, language="en", audio_language="en")
    slack_events: list[dict] = []

    class FakeYouTube:
        def __init__(self, session, settings):
            pass

        def get_status(self):
            return _FakeYouTubeStatus()

        def upload_short(self, **kwargs):
            assert kwargs["privacy_status"] == "private"
            assert kwargs["default_language"] == "en"
            assert kwargs["default_audio_language"] == "en"
            assert kwargs["made_for_kids"] is False
            assert kwargs["captions_path"] is not None
            assert kwargs["captions_path"].name == "captions.srt"
            return {"video_id": "yt-123", "privacy_status": "private", "status": {"found": True}}

    monkeypatch.setattr(private_upload, "YouTubeConnectorService", FakeYouTube)
    monkeypatch.setattr(
        private_upload.SlackWebhookNotifier,
        "notify_pipeline_event",
        lambda self, event: slack_events.append(event) or {"status": "sent"},
    )

    with TestClient(app) as client:
        response = client.post("/api/production/jobs/prod-upload-1/upload-private")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "uploaded_private"
    assert payload["youtube_video_id"] == "yt-123"
    assert payload["youtube_url"] == "https://www.youtube.com/watch?v=yt-123"
    assert [item["window_hours"] for item in payload["analytics_followups"]] == [24, 48, 72]
    with connect(settings) as db:
        job = db.execute("SELECT status, youtube_video_id, analytics_followup_plan FROM pipeline_jobs WHERE id = 'prod-upload-1'").fetchone()
        followups = db.execute(
            "SELECT window_hours FROM production_analytics_followups WHERE job_id = 'prod-upload-1' ORDER BY window_hours"
        ).fetchall()
    assert job["status"] == "uploaded_private"
    assert job["youtube_video_id"] == "yt-123"
    assert json.loads(job["analytics_followup_plan"])[0]["window_hours"] == 24
    assert [row["window_hours"] for row in followups] == [24, 48, 72]
    assert [event["status"] for event in slack_events] == ["uploaded_private"]
    assert slack_events[0]["message"] == "https://www.youtube.com/watch?v=yt-123"


def test_private_upload_returns_quota_exceeded_with_retry_after(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    import asyncio
    import atnos.production.private_upload as private_upload

    asyncio.run(init_intelligence_db(settings))
    _seed_upload_ready_job(settings, tmp_path)
    slack_events: list[dict] = []

    class FakeYouTube:
        def __init__(self, session, settings):
            pass

        def get_status(self):
            return _FakeYouTubeStatus()

        def upload_short(self, **kwargs):
            raise private_upload.YouTubeQuotaExceededError("quotaExceeded")

    monkeypatch.setattr(private_upload, "YouTubeConnectorService", FakeYouTube)
    monkeypatch.setattr(
        private_upload.SlackWebhookNotifier,
        "notify_pipeline_event",
        lambda self, event: slack_events.append(event) or {"status": "sent"},
    )

    with TestClient(app) as client:
        response = client.post("/api/production/jobs/prod-upload-1/upload-private")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "quota_exceeded"
    assert payload["retry_after"]
    with connect(settings) as db:
        row = db.execute("SELECT status, upload_result FROM pipeline_jobs WHERE id = 'prod-upload-1'").fetchone()
    assert row["status"] == "private_upload_quota_exceeded"
    assert json.loads(row["upload_result"])["status"] == "quota_exceeded"
    assert [event["status"] for event in slack_events] == ["quota_exceeded"]


def test_analytics_followup_updates_topic_performance_score(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.obsidian_vault_path = tmp_path / "vault"
    settings.obsidian_vault_path.mkdir(parents=True, exist_ok=True)
    settings.obsidian_export_enabled = True
    settings.atnos_write_obsidian_logs = True
    settings.atnos_obsidian_shorts_dir = "Atnos/Shorts"
    import asyncio
    import atnos.production.private_upload as private_upload

    asyncio.run(init_intelligence_db(settings))
    _seed_upload_ready_job(settings, tmp_path)
    job = {"id": "prod-upload-1", "account_id": "acct-1", "topic_id": "topic-1"}
    private_upload.schedule_analytics_followups(settings=settings, job=job, youtube_video_id="yt-123")

    class FakeYouTube:
        def __init__(self, session, settings):
            pass

        def fetch_analytics(self, video_id: str):
            assert video_id == "yt-123"
            return {
                "status": "ready",
                "video_id": video_id,
                "views": 1200,
                "average_view_duration": 35,
                "average_view_percentage": 76,
                "likes": 96,
                "comments": 11,
                "shares": 8,
                "subscribers_gained": 6,
            }

    monkeypatch.setattr(private_upload, "YouTubeConnectorService", FakeYouTube)
    with connect(settings) as db:
        followup_id = db.execute(
            "SELECT id FROM production_analytics_followups WHERE job_id = 'prod-upload-1' AND window_hours = 24"
        ).fetchone()["id"]

    with TestClient(app) as client:
        response = client.post("/api/production/analytics/followup", json={"followup_id": followup_id})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["score_update"]["window_hours"] == 24
    assert payload["score_update"]["performance_score"] > 60
    assert payload["score_update"]["engagement_rate"] > 0
    assert payload["score_update"]["subscriber_score"] > 0
    assert payload["score_update"]["qa_summary"]["overall"] == "passed"
    assert payload["score_update"]["qa_summary"]["upload_decision_status"] == "ready_for_private_upload"
    assert payload["score_update"]["qa_summary"]["private_upload_allowed"] is True
    assert payload["score_update"]["obsidian_learning_note_path"]
    with connect(settings) as db:
        topic = db.execute(
            """
            SELECT status, last_youtube_video_id, performance_score, feedback_adjusted_score, analytics_history_json,
                   last_performance_recommendation
            FROM topic_queue
            WHERE id = 'topic-1'
            """
        ).fetchone()
        followup = db.execute(
            "SELECT status, analytics_json, score_update_json FROM production_analytics_followups WHERE id = ?",
            (followup_id,),
        ).fetchone()
    assert topic["status"] == "done"
    assert topic["last_youtube_video_id"] == "yt-123"
    assert topic["performance_score"] > 60
    assert topic["feedback_adjusted_score"] > 70
    assert topic["feedback_adjusted_score"] != topic["performance_score"]
    assert topic["last_performance_recommendation"]
    history = json.loads(topic["analytics_history_json"])
    assert [item["window_hours"] for item in history] == [24]
    assert history[0]["performance_score"] == topic["performance_score"]
    assert history[0]["qa_summary"]["upload_decision_status"] == "ready_for_private_upload"
    assert followup["status"] == "completed"
    assert json.loads(followup["analytics_json"])["views"] == 1200
    stored_score_update = json.loads(followup["score_update_json"])
    assert stored_score_update["performance_score"] == topic["performance_score"]
    assert stored_score_update["qa_summary"]["private_upload_allowed"] is True
    assert stored_score_update["obsidian_learning_note_path"]
    obsidian_notes = list((settings.obsidian_vault_path / "Atnos" / "Shorts").glob("channel-learning-acct-1-*.md"))
    assert len(obsidian_notes) == 1
    note_text = obsidian_notes[0].read_text(encoding="utf-8")
    assert "Performance score" in note_text
    assert "Użyj tej lekcji" in note_text
    assert "Nie traktuj tych danych jako gwarancji zarobków" in note_text
    assert Path(stored_score_update["obsidian_learning_note_path"]).resolve() == obsidian_notes[0].resolve()


    with connect(settings) as db:
        followup_48_id = db.execute(
            "SELECT id FROM production_analytics_followups WHERE job_id = 'prod-upload-1' AND window_hours = 48"
        ).fetchone()["id"]

    with TestClient(app) as client:
        response = client.post("/api/production/analytics/followup", json={"followup_id": followup_48_id})

    assert response.status_code == 200
    assert response.json()["score_update"]["window_hours"] == 48
    with connect(settings) as db:
        topic = db.execute("SELECT analytics_history_json FROM topic_queue WHERE id = 'topic-1'").fetchone()
    history = json.loads(topic["analytics_history_json"])
    assert [item["window_hours"] for item in history] == [24, 48]


def test_run_due_analytics_followups_processes_due_rows_only(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    import asyncio
    import atnos.production.private_upload as private_upload

    asyncio.run(init_intelligence_db(settings))
    _seed_upload_ready_job(settings, tmp_path)
    slack_events: list[dict] = []
    job = {"id": "prod-upload-1", "account_id": "acct-1", "topic_id": "topic-1"}
    private_upload.schedule_analytics_followups(settings=settings, job=job, youtube_video_id="yt-123")
    with connect(settings) as db:
        db.execute(
            """
            UPDATE production_analytics_followups
            SET due_at = CASE WHEN window_hours = 24 THEN '2000-01-01T00:00:00+00:00' ELSE due_at END
            WHERE job_id = 'prod-upload-1'
            """
        )
        db.commit()

    class FakeYouTube:
        def __init__(self, session, settings):
            pass

        def fetch_analytics(self, video_id: str):
            return {
                "status": "ready",
                "video_id": video_id,
                "views": 700,
                "average_view_duration": 22,
                "average_view_percentage": 62,
                "likes": 21,
                "comments": 4,
                "shares": 2,
                "subscribers_gained": 1,
            }

    monkeypatch.setattr(private_upload, "YouTubeConnectorService", FakeYouTube)
    monkeypatch.setattr(
        private_upload.SlackWebhookNotifier,
        "notify_pipeline_event",
        lambda self, event: slack_events.append(event) or {"status": "sent"},
    )

    with TestClient(app) as client:
        response = client.post("/api/production/analytics/run-due?limit=5")

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    with connect(settings) as db:
        counts = db.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM production_analytics_followups
            GROUP BY status
            """
        ).fetchall()
    status_counts = {row["status"]: row["count"] for row in counts}
    assert status_counts["completed"] == 1
    assert status_counts["scheduled"] == 2
    assert [event["step"] for event in slack_events] == ["analytics"]
    assert [event["status"] for event in slack_events] == ["completed"]
    assert "performance_score" in slack_events[0]["message"]


def test_analytics_followup_reschedules_when_youtube_data_is_not_ready(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    import asyncio
    import atnos.production.private_upload as private_upload

    asyncio.run(init_intelligence_db(settings))
    _seed_upload_ready_job(settings, tmp_path)
    job = {"id": "prod-upload-1", "account_id": "acct-1", "topic_id": "topic-1"}
    private_upload.schedule_analytics_followups(settings=settings, job=job, youtube_video_id="yt-123")
    with connect(settings) as db:
        db.execute(
            """
            UPDATE production_analytics_followups
            SET due_at = '2000-01-01T00:00:00+00:00'
            WHERE job_id = 'prod-upload-1' AND window_hours = 24
            """
        )
        followup_id = db.execute(
            "SELECT id FROM production_analytics_followups WHERE job_id = 'prod-upload-1' AND window_hours = 24"
        ).fetchone()["id"]
        db.commit()

    class FakeYouTube:
        def __init__(self, session, settings):
            pass

        def fetch_analytics(self, video_id: str):
            return {"status": "not_ready", "video_id": video_id, "message": "analytics still processing"}

    monkeypatch.setattr(private_upload, "YouTubeConnectorService", FakeYouTube)

    with TestClient(app) as client:
        response = client.post("/api/production/analytics/run-due?limit=5")

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["results"][0]["status"] == "scheduled"
    assert payload["results"][0]["score_update"]["status"] == "waiting_for_data"
    with connect(settings) as db:
        followup = db.execute(
            "SELECT status, due_at, analytics_json, score_update_json, completed_at FROM production_analytics_followups WHERE id = ?",
            (followup_id,),
        ).fetchone()
        topic = db.execute(
            "SELECT status, performance_score, analytics_history_json FROM topic_queue WHERE id = 'topic-1'"
        ).fetchone()
    assert followup["status"] == "scheduled"
    assert followup["due_at"] > "2000-01-01T00:00:00+00:00"
    assert followup["completed_at"] is None
    assert json.loads(followup["analytics_json"])["status"] == "not_ready"
    assert json.loads(followup["score_update_json"])["status"] == "waiting_for_data"
    assert topic["status"] == "in_production"
    assert topic["performance_score"] is None
    history = json.loads(topic["analytics_history_json"])
    assert history[0]["analytics_status"] == "not_ready"


def test_due_analytics_followup_recovers_from_unexpected_exception(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    import asyncio
    import atnos.production.private_upload as private_upload

    asyncio.run(init_intelligence_db(settings))
    _seed_upload_ready_job(settings, tmp_path)
    job = {"id": "prod-upload-1", "account_id": "acct-1", "topic_id": "topic-1"}
    private_upload.schedule_analytics_followups(settings=settings, job=job, youtube_video_id="yt-123")
    with connect(settings) as db:
        db.execute(
            """
            UPDATE production_analytics_followups
            SET due_at = '2000-01-01T00:00:00+00:00'
            WHERE job_id = 'prod-upload-1' AND window_hours = 24
            """
        )
        followup_id = db.execute(
            "SELECT id FROM production_analytics_followups WHERE job_id = 'prod-upload-1' AND window_hours = 24"
        ).fetchone()["id"]
        db.commit()

    def fatal_followup(*_args, **_kwargs):
        raise RuntimeError("analytics worker crashed")

    monkeypatch.setattr(private_upload, "run_analytics_followup", fatal_followup)

    with TestClient(app) as client:
        response = client.post("/api/production/analytics/run-due?limit=5")

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["results"][0]["status"] == "scheduled"
    assert payload["results"][0]["rescheduled_after_error"] is True
    assert payload["results"][0]["score_update"]["status"] == "waiting_for_data"
    with connect(settings) as db:
        followup = db.execute(
            "SELECT status, due_at, error, analytics_json, score_update_json, completed_at FROM production_analytics_followups WHERE id = ?",
            (followup_id,),
        ).fetchone()
    assert followup["status"] == "scheduled"
    assert followup["due_at"] > "2000-01-01T00:00:00+00:00"
    assert followup["completed_at"] is None
    assert "analytics worker crashed" in followup["error"]
    assert json.loads(followup["analytics_json"])["status"] == "unavailable"
    assert json.loads(followup["score_update_json"])["status"] == "waiting_for_data"


class _FakeYouTubeStatus:
    ready = True

    def to_dict(self):
        return {
            "connector_key": "youtube-studio",
            "configured": True,
            "connected": True,
            "ready": True,
            "warnings": [],
            "granted_scopes": [
                "openid",
                "email",
                "profile",
                "https://www.googleapis.com/auth/youtube.upload",
                "https://www.googleapis.com/auth/youtube.readonly",
                "https://www.googleapis.com/auth/yt-analytics.readonly",
            ],
        }
