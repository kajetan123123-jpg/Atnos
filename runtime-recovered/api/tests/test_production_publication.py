from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
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


def _seed_uploaded_private_job(
    settings,
    *,
    job_id: str = "prod-public-1",
    voice_quality_tier: str = "premium_cloud",
    voice_backend_grade: str = "premium",
) -> None:
    now = utc_now_iso()
    upload_result = {
        "status": "uploaded_private",
        "job_id": job_id,
        "youtube_video_id": "yt-private-1",
        "youtube_url": "https://www.youtube.com/watch?v=yt-private-1",
        "visibility": "private",
    }
    audio_timestamps = {
        "quality_tier": voice_quality_tier,
        "voice_backend_grade": voice_backend_grade,
        "voice_profile": {"voice": "en-US-ChristopherNeural", "language_code": "en"},
    }
    qa_report = _valid_public_quality_qa_report()
    style_guide = {
        "style_guide": {
            "status": "obsidian_channel_style",
            "source": "F:/AtnosSystem/AppData/Roaming/Atnos/obsidian/Atnos/Shorts/acct-public-1/style_guide.md",
            "used": True,
        }
    }
    video_manifest = {
        "backend": "openai_image_motion",
        "clips": [
            {"backend": "openai_image_motion"},
            {"backend": "openai_image_motion"},
            {"backend": "openai_image_motion"},
        ],
        "quality_plan": {
            "generated_clip_count": 3,
            "unique_composition_count": 3,
            "composition_diversity_ratio": 1.0,
            "scene_concreteness_risk_count": 0,
            "passes_scene_concreteness_floor": True,
            "avg_visual_relevance_score": 0.8,
            "passes_visual_relevance_floor": True,
            "topic_anchor_risk_count": 0,
            "passes_topic_anchor_floor": True,
            "generated_image_text_risk_count": 0,
            "generated_image_visual_risk_count": 0,
            "passes_generated_image_text_floor": True,
            "passes_generated_image_visual_floor": True,
        },
    }
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO topic_queue (
                id, account_id, title, description, status, total_score, created_at, updated_at
            )
            VALUES ('topic-public-1', 'acct-public-1', 'Public test topic', 'Opis', 'done', 88, ?, ?)
            """,
            (now, now),
        )
        db.execute(
            """
            INSERT INTO pipeline_jobs (
                id, account_id, topic_id, status, current_step, youtube_video_id, youtube_url,
                upload_result, audio_timestamps, hooks, script, asset_plan,
                video_manifest, qa_report, uploaded_at, created_at, updated_at
            )
            VALUES (?, 'acct-public-1', 'topic-public-1', 'uploaded_private', 'private_upload',
                    'yt-private-1', 'https://www.youtube.com/watch?v=yt-private-1', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                json.dumps(upload_result),
                json.dumps(audio_timestamps),
                json.dumps(style_guide),
                json.dumps(style_guide),
                json.dumps(style_guide),
                json.dumps(video_manifest),
                json.dumps(qa_report),
                now,
                now,
                now,
            ),
        )
        db.commit()


def test_public_promotion_is_blocked_when_public_upload_flag_is_disabled(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    import asyncio
    import atnos.production.publication as publication

    asyncio.run(init_intelligence_db(settings))
    _seed_uploaded_private_job(settings)

    monkeypatch.setattr(
        publication,
        "_build_youtube_connector",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("YouTube must not be called.")),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/production/jobs/prod-public-1/schedule-public",
            json={"approved": True},
        )

    assert response.status_code == 409
    assert "ALLOW_PUBLIC_UPLOAD is disabled" in response.json()["detail"]
    with TestClient(app) as client:
        job_response = client.get("/api/production/jobs/prod-public-1")
    job_payload = job_response.json()
    assert job_payload["ready_for_public_promotion"] is True
    assert "public_upload_disabled" in job_payload["public_promotion_blockers"]


def _valid_public_quality_qa_report() -> dict:
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
                "detail": "Seeded full QA contract for publication tests.",
            }
        )
    return {
        "overall": "passed",
        "auto_uploadable": True,
        "required_check_count": len(required_checks),
        "required_checks": required_checks,
        "blocking_failures": [],
        "operating_standards": {
            "used": True,
            "status": "ready",
            "project_type": "shorts_channel",
            "source": {"path": "F:/AtnosSystem/AppData/Roaming/Atnos/data/learning/side_research/report.md", "exists": True},
            "section_keys": ["shorts_quality"],
            "required_count": 4,
            "blocked_until_configured_count": 0,
        },
        "upload_decision": {
            "status": "ready_for_private_upload",
            "auto_uploadable": True,
            "private_upload_allowed": True,
            "required_failures": [],
        },
    }


def test_public_promotion_requires_explicit_request_approval(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.allow_public_upload = True
    import asyncio
    import atnos.production.publication as publication

    asyncio.run(init_intelligence_db(settings))
    _seed_uploaded_private_job(settings)
    monkeypatch.setattr(
        publication,
        "_build_youtube_connector",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("YouTube must not be called.")),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/production/jobs/prod-public-1/schedule-public",
            json={"approved": False},
        )

    assert response.status_code == 409
    assert "Explicit approval is required" in response.json()["detail"]


def test_public_promotion_blocks_fallback_voice_before_youtube_call(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.allow_public_upload = True
    import asyncio
    import atnos.production.publication as publication

    asyncio.run(init_intelligence_db(settings))
    _seed_uploaded_private_job(
        settings,
        voice_quality_tier="cloud_neural_fallback",
        voice_backend_grade="acceptable_neural_fallback",
    )
    monkeypatch.setattr(
        publication,
        "_build_youtube_connector",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("YouTube must not be called.")),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/production/jobs/prod-public-1/schedule-public",
            json={"approved": True},
        )

    assert response.status_code == 409
    assert "premium_voice_required_for_public" in response.json()["detail"]
    with TestClient(app) as client:
        job_response = client.get("/api/production/jobs/prod-public-1")
    job_payload = job_response.json()
    assert "premium_voice_required_for_public" in job_payload["public_promotion_blockers"]


def test_public_promotion_blocks_topic_anchor_visual_risk_before_youtube_call(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.allow_public_upload = True
    import asyncio
    import atnos.production.publication as publication

    asyncio.run(init_intelligence_db(settings))
    _seed_uploaded_private_job(settings)
    visual_manifest = {
        "backend": "openai_image_motion",
        "clips": [
            {"backend": "openai_image_motion"},
            {"backend": "openai_image_motion"},
            {"backend": "openai_image_motion"},
        ],
        "quality_plan": {
            "generated_clip_count": 3,
            "unique_composition_count": 3,
            "composition_diversity_ratio": 1.0,
            "scene_concreteness_risk_count": 0,
            "passes_scene_concreteness_floor": True,
            "avg_visual_relevance_score": 0.7,
            "passes_visual_relevance_floor": False,
            "topic_anchor_risk_count": 1,
            "passes_topic_anchor_floor": False,
            "generated_image_text_risk_count": 0,
            "generated_image_visual_risk_count": 0,
            "passes_generated_image_text_floor": True,
            "passes_generated_image_visual_floor": True,
        },
    }
    with connect(settings) as db:
        db.execute(
            "UPDATE pipeline_jobs SET video_manifest = ? WHERE id = 'prod-public-1'",
            (json.dumps(visual_manifest),),
        )
        db.commit()
    monkeypatch.setattr(
        publication,
        "_build_youtube_connector",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("YouTube must not be called.")),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/production/jobs/prod-public-1/schedule-public",
            json={"approved": True},
        )

    assert response.status_code == 409
    assert "topic_anchor_risk_for_public" in response.json()["detail"]
    with TestClient(app) as client:
        job_response = client.get("/api/production/jobs/prod-public-1")
    job_payload = job_response.json()
    assert "topic_anchor_risk_for_public" in job_payload["public_promotion_blockers"]


def test_public_promotion_blocks_top_level_premium_backend_when_clips_are_motion_graphic(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.allow_public_upload = True
    import asyncio
    import atnos.production.publication as publication

    asyncio.run(init_intelligence_db(settings))
    _seed_uploaded_private_job(settings)
    visual_manifest = {
        "backend": {"mode": "openai_image_motion", "models_ready": True},
        "clips": [
            {"backend": "motion_graphic", "status": "generated"},
            {"backend": "motion_graphic", "status": "generated"},
            {"backend": "motion_graphic", "status": "generated"},
        ],
        "quality_plan": {
            "generated_clip_count": 3,
            "unique_composition_count": 3,
            "composition_diversity_ratio": 1.0,
            "scene_concreteness_risk_count": 0,
            "passes_scene_concreteness_floor": True,
            "avg_visual_relevance_score": 0.8,
            "passes_visual_relevance_floor": True,
            "topic_anchor_risk_count": 0,
            "passes_topic_anchor_floor": True,
            "generated_image_text_risk_count": 0,
            "generated_image_visual_risk_count": 0,
            "passes_generated_image_text_floor": True,
            "passes_generated_image_visual_floor": True,
        },
    }
    with connect(settings) as db:
        db.execute(
            "UPDATE pipeline_jobs SET video_manifest = ? WHERE id = 'prod-public-1'",
            (json.dumps(visual_manifest),),
        )
        db.commit()
    monkeypatch.setattr(
        publication,
        "_build_youtube_connector",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("YouTube must not be called.")),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/production/jobs/prod-public-1/schedule-public",
            json={"approved": True},
        )

    assert response.status_code == 409
    assert "premium_visual_backend_required_for_public" in response.json()["detail"]
    with TestClient(app) as client:
        job_response = client.get("/api/production/jobs/prod-public-1")
    job_payload = job_response.json()
    assert "premium_visual_backend_required_for_public" in job_payload["public_promotion_blockers"]
    assert job_payload["public_quality_evidence"]["video_backend"] == "openai_image_motion"
    assert job_payload["public_quality_evidence"]["clip_backends"] == [
        "motion_graphic",
        "motion_graphic",
        "motion_graphic",
    ]
    assert job_payload["public_quality_evidence"]["public_ready_visual_backend"] is False


def test_public_promotion_blocks_legacy_qa_without_actual_visual_variety(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.allow_public_upload = True
    import asyncio
    import atnos.production.publication as publication

    asyncio.run(init_intelligence_db(settings))
    _seed_uploaded_private_job(settings)
    qa_report = _valid_public_quality_qa_report()
    for check in qa_report["required_checks"]:
        if check["name"] == "Visual variety":
            check["value"].pop("actual_visual_variety", None)
    with connect(settings) as db:
        db.execute(
            "UPDATE pipeline_jobs SET qa_report = ? WHERE id = 'prod-public-1'",
            (json.dumps(qa_report),),
        )
        db.commit()
    monkeypatch.setattr(
        publication,
        "_build_youtube_connector",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("YouTube must not be called.")),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/production/jobs/prod-public-1/schedule-public",
            json={"approved": True},
        )

    assert response.status_code == 409
    assert "actual_visual_variety_missing_for_public" in response.json()["detail"]


def test_public_promotion_blocks_missing_style_guide_evidence_before_youtube_call(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.allow_public_upload = True
    import asyncio
    import atnos.production.publication as publication

    asyncio.run(init_intelligence_db(settings))
    _seed_uploaded_private_job(settings)
    with connect(settings) as db:
        db.execute(
            "UPDATE pipeline_jobs SET hooks = NULL, script = NULL, asset_plan = NULL WHERE id = 'prod-public-1'"
        )
        db.commit()
    monkeypatch.setattr(
        publication,
        "_build_youtube_connector",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("YouTube must not be called.")),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/production/jobs/prod-public-1/schedule-public",
            json={"approved": True},
        )

    assert response.status_code == 409
    assert "style_guide_evidence_missing_for_public" in response.json()["detail"]
    with TestClient(app) as client:
        job_response = client.get("/api/production/jobs/prod-public-1")
    job_payload = job_response.json()
    assert "style_guide_evidence_missing_for_public" in job_payload["public_promotion_blockers"]
    assert job_payload["public_quality_evidence"]["style_guide_used"] is False
    assert job_payload["public_quality_evidence"]["operating_standards"]["used"] is True


def test_public_promotion_schedules_private_video_and_persists_result(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.allow_public_upload = True
    import asyncio
    import atnos.production.publication as publication

    asyncio.run(init_intelligence_db(settings))
    _seed_uploaded_private_job(settings)
    slack_events: list[dict] = []
    fake = _FakeYouTubePromotion(status="scheduled_public")
    monkeypatch.setattr(publication, "_build_youtube_connector", lambda *_args, **_kwargs: fake)
    monkeypatch.setattr(
        publication.SlackWebhookNotifier,
        "notify_pipeline_event",
        lambda self, event: slack_events.append(event) or {"status": "sent"},
    )
    publish_at = (datetime.now(timezone.utc) + timedelta(hours=4)).replace(microsecond=0).isoformat()

    with TestClient(app) as client:
        response = client.post(
            "/api/production/jobs/prod-public-1/schedule-public",
            json={"approved": True, "publish_at": publish_at},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "scheduled_public"
    assert payload["publish_at"] == publish_at
    assert fake.calls == [{"video_id": "yt-private-1", "approved": True, "publish_at": publish_at}]
    with connect(settings) as db:
        row = db.execute(
            "SELECT status, current_step, public_publish_result, public_scheduled_at, public_published_at FROM pipeline_jobs WHERE id = 'prod-public-1'"
        ).fetchone()
    assert row["status"] == "scheduled_public"
    assert row["current_step"] == "public_publish"
    assert row["public_scheduled_at"] == publish_at
    assert row["public_published_at"] is None
    stored = json.loads(row["public_publish_result"])
    assert stored["status"] == "scheduled_public"
    assert stored["youtube_url"] == "https://www.youtube.com/watch?v=yt-private-1"
    assert [event["status"] for event in slack_events] == ["scheduled_public"]


def test_public_promotion_can_publish_immediately_after_manual_approval(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.allow_public_upload = True
    import asyncio
    import atnos.production.publication as publication

    asyncio.run(init_intelligence_db(settings))
    _seed_uploaded_private_job(settings)
    fake = _FakeYouTubePromotion(status="published_public")
    monkeypatch.setattr(publication, "_build_youtube_connector", lambda *_args, **_kwargs: fake)
    monkeypatch.setattr(
        publication.SlackWebhookNotifier,
        "notify_pipeline_event",
        lambda self, event: {"status": "sent"},
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/production/jobs/prod-public-1/schedule-public",
            json={"approved": True},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "published_public"
    assert fake.calls == [{"video_id": "yt-private-1", "approved": True, "publish_at": None}]
    with connect(settings) as db:
        row = db.execute(
            "SELECT status, public_publish_result, public_scheduled_at, public_published_at FROM pipeline_jobs WHERE id = 'prod-public-1'"
        ).fetchone()
    assert row["status"] == "published_public"
    assert row["public_scheduled_at"] is None
    assert row["public_published_at"]
    assert json.loads(row["public_publish_result"])["status"] == "published_public"


class _FakeStatus:
    ready = True

    def to_dict(self):
        return {"ready": True, "configured": True, "connected": True, "warnings": [], "granted_scopes": []}


class _FakeYouTubePromotion:
    def __init__(self, *, status: str) -> None:
        self.status = status
        self.calls: list[dict] = []

    def get_status(self):
        return _FakeStatus()

    def promote_private_video(self, *, video_id: str, approved: bool, publish_at: str | None = None):
        self.calls.append({"video_id": video_id, "approved": approved, "publish_at": publish_at})
        return {
            "status": self.status,
            "video_id": video_id,
            "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
            "publish_at": publish_at,
            "privacy_status": "private" if publish_at else "public",
            "quota_diagnostics": {"quota_surface": "youtube_data_api_v3"},
        }
