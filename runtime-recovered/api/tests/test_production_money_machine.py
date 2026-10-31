from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime
from pathlib import Path
import sys

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
from atnos.db.models.oauth_connection import OAuthConnection  # noqa: E402
from atnos.production.oauth_resolver import resolve_gmail_oauth_account_id, resolve_youtube_oauth_account_id  # noqa: E402
from atnos.production.qa_gate import REQUIRED_QA_CHECK_NAMES  # noqa: E402
from atnos.production.money_machine import MoneyMachineStatusService, build_money_machine_operator_summary  # noqa: E402
from atnos.services.account_credentials_service import AccountCredentialsService  # noqa: E402
from atnos.services.oauth_provider_registry import YOUTUBE_OPTIONAL_CAPTION_SCOPE  # noqa: E402
from atnos.services.oauth_token_store import OAuthTokenBundle, OAuthTokenStore  # noqa: E402
from test_support import bootstrap_test_app  # noqa: E402
from test_support import create_test_app  # noqa: E402


def _seed_account(settings, *, account_id: str = "acct-1", oauth_account_id: int | None = None) -> None:
    upsert_intelligence_account_config(
        settings,
        account_id=account_id,
        niches=["personal finance"],
        region="US",
        youtube_oauth_account_id=oauth_account_id,
        channel_title="Wealth Blueprint",
        content_profile="wealth_blueprint",
    )


def _seed_trend_signal(settings, *, account_id: str = "acct-1") -> None:
    now = utc_now_iso()
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO yt_signals (
                video_id, account_id, niche, region, title, view_count, like_count,
                comment_count, published_at, channel_id, channel_title, thumbnail_url, collected_at
            )
            VALUES ('vid-1', ?, 'personal finance', 'US', 'Money habit', 100000, 5000, 120, ?, 'chan-1', 'Ref', '', ?)
            ON CONFLICT(video_id) DO UPDATE SET collected_at = excluded.collected_at
            """,
            (account_id, now, now),
        )
        db.commit()


def _seed_pending_topic(settings, *, account_id: str = "acct-1", score: float = 68.0) -> None:
    now = utc_now_iso()
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO topic_queue (
                id, account_id, title, description, status, total_score,
                shorts_potential, clickbait_risk, created_at, updated_at
            )
            VALUES ('topic-pending-1', ?, 'Best budget leak to fix first', 'Pending candidate', 'pending', ?, 1, 0, ?, ?)
            """,
            (account_id, score, now, now),
        )
        db.commit()


def _seed_ready_private_job(settings, root: Path, *, account_id: str = "acct-1") -> None:
    now = utc_now_iso()
    artifact_dir = root / "data" / "production" / account_id / "prod-ready-1"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    qa_path = artifact_dir / "qa_report.json"
    package_path = artifact_dir / "youtube_private_upload_package.json"
    qa_path.write_text(json.dumps(_valid_public_quality_qa_report()), encoding="utf-8")
    audio_timestamps = {
        "quality_tier": "premium_cloud",
        "voice_backend_grade": "premium",
        "voice_profile": {"language_code": "en"},
    }
    video_manifest = {
        "backend": {"mode": "openai_image_motion"},
        "clips": [
            {"status": "generated", "backend": "openai_image_motion"},
            {"status": "generated", "backend": "openai_image_motion"},
            {"status": "generated", "backend": "openai_image_motion"},
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
    style_guide = _style_guide_evidence(account_id)
    package_path.write_text(
        json.dumps(
            {
                "channel_account_id": account_id,
                "topic_id": "topic-1",
                "qa_report_path": str(qa_path),
                "auto_uploadable": True,
                "upload_metadata": {"title": "The Money Habit Keeping You Broke"},
            }
        ),
        encoding="utf-8",
    )
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO topic_queue (
                id, account_id, title, description, status, total_score, created_at, updated_at
            )
            VALUES ('topic-1', ?, 'The Money Habit Keeping You Broke', 'Test', 'in_production', 88, ?, ?)
            """,
            (account_id, now, now),
        )
        db.execute(
            """
            INSERT INTO pipeline_jobs (
                id, account_id, topic_id, status, current_step, artifact_dir,
                qa_report, qa_report_path, upload_package_path, audio_timestamps,
                hooks, script, asset_plan, video_manifest, created_at, updated_at
            )
            VALUES ('prod-ready-1', ?, 'topic-1', 'qa_passed', 'upload_package', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                str(artifact_dir),
                json.dumps(json.loads(qa_path.read_text(encoding="utf-8"))),
                str(qa_path),
                str(package_path),
                json.dumps(audio_timestamps),
                json.dumps(style_guide),
                json.dumps(style_guide),
                json.dumps(style_guide),
                json.dumps(video_manifest),
                now,
                now,
            ),
        )
        db.commit()


def _seed_active_job(settings, *, account_id: str = "acct-1", job_id: str = "prod-active-1") -> None:
    now = utc_now_iso()
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO topic_queue (
                id, account_id, title, description, status, total_score, created_at, updated_at
            )
            VALUES ('topic-active-1', ?, 'Active production topic', 'Test', 'in_production', 77, ?, ?)
            """,
            (account_id, now, now),
        )
        db.execute(
            """
            INSERT INTO pipeline_jobs (
                id, account_id, topic_id, status, current_step, artifact_dir, created_at, updated_at
            )
            VALUES (?, ?, 'topic-active-1', 'running', 'script', '', ?, ?)
            """,
            (job_id, account_id, now, now),
        )
        db.commit()


def _seed_uploaded_private_job(
    settings,
    *,
    account_id: str = "acct-1",
    job_id: str = "prod-public-1",
    upload_warnings: list[str] | None = None,
    voice_quality_tier: str = "premium_cloud",
    voice_backend_grade: str = "premium",
    video_backend: str = "wan",
) -> None:
    now = utc_now_iso()
    upload_result = {
        "status": "uploaded_private",
        "job_id": job_id,
        "youtube_video_id": "yt-private-1",
        "youtube_url": "https://www.youtube.com/watch?v=yt-private-1",
        "visibility": "private",
        "upload": {"warnings": upload_warnings or []},
    }
    audio_timestamps = {
        "quality_tier": voice_quality_tier,
        "voice_backend_grade": voice_backend_grade,
        "voice_profile": {"voice": "en-US-ChristopherNeural", "language_code": "en"},
    }
    qa_report = _valid_public_quality_qa_report()
    style_guide = _style_guide_evidence(account_id)
    video_manifest = {
        "backend": {"mode": video_backend},
        "clips": [
            {"status": "generated", "backend": video_backend},
            {"status": "generated", "backend": video_backend},
            {"status": "generated", "backend": video_backend},
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
            VALUES ('topic-public-1', ?, 'Public-ready topic', 'Test', 'done', 90, ?, ?)
            """,
            (account_id, now, now),
        )
        db.execute(
            """
            INSERT INTO pipeline_jobs (
                id, account_id, topic_id, status, current_step, youtube_video_id, youtube_url,
                upload_result, audio_timestamps, hooks, script, asset_plan,
                video_manifest, qa_report, uploaded_at, created_at, updated_at
            )
            VALUES (?, ?, 'topic-public-1', 'uploaded_private', 'private_upload',
                    'yt-private-1', 'https://www.youtube.com/watch?v=yt-private-1', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                account_id,
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


def _style_guide_evidence(account_id: str) -> dict:
    return {
        "style_guide": {
            "status": "obsidian_channel_style",
            "source": f"F:/AtnosSystem/AppData/Roaming/Atnos/obsidian/Atnos/Shorts/{account_id}/style_guide.md",
            "used": True,
        }
    }


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
                    "mean_hash_distance": 0.32,
                    "mean_top_crop_pixel_diff": 0.19,
                },
            }
        required_checks.append(
            {
                "name": name,
                "status": "pass",
                "value": value,
                "detail": "Seeded full QA contract for money-machine tests.",
            }
        )
    return {
        "overall": "passed",
        "auto_uploadable": True,
        "required_check_count": len(required_checks),
        "required_checks": required_checks,
        "blocking_failures": [],
        "upload_decision": {
            "status": "ready_for_private_upload",
            "auto_uploadable": True,
            "private_upload_allowed": True,
            "required_failures": [],
        },
    }


def _write_voice_provider_report(settings, *, public_voice_ready: bool, next_action: str = "add_openai_billing_or_raise_project_quota") -> Path:
    reports_dir = settings.data_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / "voice_provider_doctor_20260606-120000.json"
    report_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "public_voice_ready": public_voice_ready,
                "selected_tts_backend": "openai_tts",
                "providers": {
                    "elevenlabs": {
                        "configured": True,
                        "live_checked": True,
                        "live_ready": False,
                        "status_code": 401,
                        "error": "detected_unusual_activity",
                        "profile_voices": {"wealth_blueprint": True, "dark_chronicles": True, "sigma_squad": True},
                    },
                    "openai_tts": {
                        "configured": True,
                        "live_checked": True,
                        "live_ready": public_voice_ready,
                        "status_code": None if public_voice_ready else 429,
                        "error": None if public_voice_ready else "insufficient_quota",
                        "model": "gpt-4o-mini-tts",
                        "profile_voices": {"wealth_blueprint": "marin", "dark_chronicles": "onyx", "sigma_squad": "verse"},
                    },
                    "f5_tts": {
                        "configured": True,
                        "live_checked": True,
                        "live_ready": False,
                        "status_code": None,
                        "error": "All connection attempts failed",
                        "base_url": "http://localhost:7860/api/predict",
                    },
                },
                "next_action": next_action,
            }
        ),
        encoding="utf-8",
    )
    return report_path


def test_money_machine_reports_ready_private_upload_and_oauth_blocker(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_account(settings)
    _seed_trend_signal(settings)
    _seed_ready_private_job(settings, tmp_path)

    status = MoneyMachineStatusService(settings).build(include_oauth=False)

    assert status["status"] == "ready_but_oauth_blocked"
    assert status["totals"]["ready_private_upload_jobs"] == 1
    account = status["accounts"][0]
    assert account["ready_private_upload_jobs"][0]["job_id"] == "prod-ready-1"
    assert "youtube_oauth_not_ready_for_private_upload" in account["blockers"]
    assert "human_review_required_before_private_upload" in account["blockers"]
    assert account["next_action"] == "connect_youtube_oauth"


def test_money_machine_reports_active_job_details_for_operator_visibility(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_account(settings)
    _seed_trend_signal(settings)
    _seed_active_job(settings)

    status = MoneyMachineStatusService(settings).build(include_oauth=False)
    summary = build_money_machine_operator_summary(status)

    assert status["status"] == "producing"
    assert status["totals"]["active_jobs"] == 1
    assert status["active_jobs"][0]["job_id"] == "prod-active-1"
    assert status["active_jobs"][0]["current_step"] == "script"
    assert status["active_jobs"][0]["stale_candidate"] is False
    account = status["accounts"][0]
    assert account["active_job_details"][0]["job_id"] == "prod-active-1"
    assert summary["active_jobs"][0]["job_id"] == "prod-active-1"
    assert summary["active_jobs"][0]["current_step"] == "script"
    assert summary["accounts"][0]["active_job_details"][0]["job_id"] == "prod-active-1"


def test_money_machine_prefers_content_loop_when_signals_exist_but_topic_buffer_is_low(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_account(settings)
    _seed_trend_signal(settings)

    status = MoneyMachineStatusService(settings).build(include_oauth=False)

    assert status["status"] == "needs_topic_buffer"
    account = status["accounts"][0]
    assert "topic_buffer_low" in account["blockers"]
    assert account["next_action"] == "run_content_loop_generate_topics"


def test_money_machine_requests_topic_approval_when_buffer_exists_without_approved_topics(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_account(settings)
    _seed_trend_signal(settings)
    _seed_pending_topic(settings, score=69)

    status = MoneyMachineStatusService(settings).build(include_oauth=False)

    assert status["status"] == "needs_topic_approval"
    assert status["totals"]["topic_buffer"] == 1
    assert status["totals"]["approved_topics"] == 0
    account = status["accounts"][0]
    assert "no_approved_topic_or_ready_job" in account["blockers"]
    assert account["next_action"] == "review_or_approve_best_topic"

    summary = build_money_machine_operator_summary(status)
    assert summary["next_operator_action"] == "review_or_approve_best_topic"
    assert summary["accounts"][0]["pending_topics"] == 1
    assert summary["accounts"][0]["approved_topics"] == 0
    assert summary["accounts"][0]["max_pending_score"] == 69
    assert "Topic buffer has candidates, but production waits for at least one approved topic." in summary["notes"]


def test_money_machine_reports_uploaded_private_jobs_as_public_promotion_candidates(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_account(settings)
    _seed_trend_signal(settings)
    _seed_uploaded_private_job(settings)

    status = MoneyMachineStatusService(settings).build(include_oauth=False)

    assert status["status"] == "public_review_blocked"
    assert status["totals"]["public_promotion_jobs"] == 1
    account = status["accounts"][0]
    assert account["public_promotion_jobs"][0]["job_id"] == "prod-public-1"
    assert account["next_action"] == "enable_public_upload_after_review"


def test_money_machine_blocks_public_promotion_when_voice_is_fallback(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.allow_public_upload = True
    asyncio.run(init_intelligence_db(settings))
    _seed_account(settings)
    _seed_trend_signal(settings)
    _seed_uploaded_private_job(
        settings,
        voice_quality_tier="cloud_neural_fallback",
        voice_backend_grade="acceptable_neural_fallback",
    )

    status = MoneyMachineStatusService(settings).build(include_oauth=False)

    account = status["accounts"][0]
    public_job = account["public_promotion_jobs"][0]
    assert public_job["public_quality_blockers"] == ["premium_voice_required_for_public"]
    assert public_job["public_quality_evidence"]["voice_quality_tier"] == "cloud_neural_fallback"
    assert "public_quality_gate_failed" in account["blockers"]
    assert account["next_action"] == "regenerate_with_premium_voice"


def test_money_machine_blocks_public_promotion_when_visual_backend_is_motion_graphic(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.allow_public_upload = True
    asyncio.run(init_intelligence_db(settings))
    _seed_account(settings)
    _seed_trend_signal(settings)
    _seed_uploaded_private_job(settings, video_backend="motion_graphic")

    status = MoneyMachineStatusService(settings).build(include_oauth=False)

    account = status["accounts"][0]
    public_job = account["public_promotion_jobs"][0]
    assert public_job["public_quality_blockers"] == ["premium_visual_backend_required_for_public"]
    assert public_job["public_quality_evidence"]["video_backend"] == "motion_graphic"
    assert public_job["public_quality_evidence"]["public_ready_visual_backend"] is False
    assert "public_quality_gate_failed" in account["blockers"]
    assert account["next_action"] == "regenerate_visuals_with_auto_repair"


def test_money_machine_allows_public_promotion_with_openai_image_motion_backend(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.allow_public_upload = True
    asyncio.run(init_intelligence_db(settings))
    _seed_account(settings)
    _seed_trend_signal(settings)
    _seed_uploaded_private_job(settings, video_backend="openai_image_motion")

    status = MoneyMachineStatusService(settings).build(include_oauth=False)

    account = status["accounts"][0]
    public_job = account["public_promotion_jobs"][0]
    assert public_job["public_quality_blockers"] == []
    assert public_job["public_quality_evidence"]["video_backend"] == "openai_image_motion"
    assert public_job["public_quality_evidence"]["public_ready_visual_backend"] is True
    assert account["next_action"] == "review_and_schedule_publication"


def test_money_machine_reports_global_premium_voice_provider_blocker(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_account(settings)
    _seed_trend_signal(settings)
    report_path = _write_voice_provider_report(settings, public_voice_ready=False)

    status = MoneyMachineStatusService(settings).build(include_oauth=False)

    assert status["voice_provider"]["checked"] is True
    assert status["voice_provider"]["report_path"] == str(report_path)
    assert status["voice_provider"]["public_voice_ready"] is False
    assert status["voice_provider"]["providers"]["openai_tts"]["error"] == "insufficient_quota"
    assert "premium_voice_provider_not_ready" in status["global_blockers"]
    assert status["next_actions"][0] == {
        "scope": "global",
        "action": "add_openai_billing_or_raise_project_quota",
    }


def test_money_machine_clears_global_voice_provider_blocker_when_premium_voice_is_ready(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_account(settings)
    _seed_trend_signal(settings)
    _write_voice_provider_report(settings, public_voice_ready=True, next_action="monitor_voice_provider")

    status = MoneyMachineStatusService(settings).build(include_oauth=False)

    assert status["voice_provider"]["public_voice_ready"] is True
    assert "premium_voice_provider_not_ready" not in status["global_blockers"]
    assert all(item["action"] != "monitor_voice_provider" for item in status["next_actions"])


def test_money_machine_reports_thumbnail_upload_blocked_from_recent_youtube_warning(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_account(settings)
    _seed_trend_signal(settings)
    _seed_uploaded_private_job(
        settings,
        upload_warnings=[
            "Thumbnail upload failed: The authenticated user doesn't have permissions to upload and set custom video thumbnails."
        ],
    )

    status = MoneyMachineStatusService(settings).build(include_oauth=False)

    capability = status["accounts"][0]["upload_capabilities"]["custom_thumbnail"]
    assert capability["ready"] is False
    assert capability["status"] == "blocked_by_youtube_channel_permission"
    assert capability["mode"] == "youtube_default_frame"
    assert "custom video thumbnails" in capability["last_warning"]


def test_money_machine_reports_optional_captions_scope_without_blocking_youtube_ready(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    service = MoneyMachineStatusService(settings)

    capability = service._upload_capabilities(
        "acct-1",
        {
            "checked": True,
            "ready": True,
            "granted_scopes": [
                "openid",
                "https://www.googleapis.com/auth/youtube.upload",
                "https://www.googleapis.com/auth/youtube.readonly",
                "https://www.googleapis.com/auth/yt-analytics.readonly",
            ],
        },
    )
    assert capability["captions_api"]["ready"] is False
    assert capability["captions_api"]["status"] == "missing_optional_scope"
    assert capability["captions_api"]["required_scope"] == YOUTUBE_OPTIONAL_CAPTION_SCOPE
    assert capability["captions_api"]["mode"] == "burned_in_subtitles"


def test_money_machine_status_endpoint(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_account(settings)
    _seed_trend_signal(settings)

    with TestClient(app) as client:
        response = client.get("/api/production/money-machine/status?include_oauth=false")

    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"]["accounts"] == 1
    assert payload["accounts"][0]["account_id"] == "acct-1"
    assert payload["accounts"][0]["signals"]["youtube_trend_videos"] == 1


def test_money_machine_run_next_plans_private_upload_without_executing(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(
        "atnos.production.money_machine.MoneyMachineStatusService.build",
        lambda *_args, **_kwargs: _fake_money_machine_status(),
    )
    monkeypatch.setattr(
        "atnos.production.money_machine.upload_private_from_package",
        lambda **kwargs: calls.append(str(kwargs["job_id"])) or {"status": "uploaded_private"},
    )

    with TestClient(app) as client:
        response = client.post("/api/production/money-machine/run-next", json={"max_uploads": 1})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "planned"
    assert payload["planned_count"] == 1
    assert payload["planned"][0]["job_id"] == "prod-ready-1"
    assert payload["planned"][0]["reason"] == "QA passed, public-quality gates passed, and OAuth is ready."
    assert payload["planned"][0]["auto_uploadable"] is True
    assert payload["planned"][0]["public_quality_ready"] is True
    assert payload["planned"][0]["public_quality_evidence"]["video_backend"] == "openai_image_motion"
    assert payload["planned"][0]["qa_report_path"] == "qa.json"
    assert payload["planned"][0]["artifact_dir"] == "artifacts/prod-ready-1"
    assert calls == []


def test_money_machine_operator_summary_compacts_upload_and_recovery_actions() -> None:
    status = _fake_money_machine_status()
    run_next = {
        "planned": [
            {
                "account_id": "acct-1",
                "job_id": "prod-ready-1",
                "topic_id": "topic-1",
                "topic_title": "Ready topic",
                "action": "upload_private",
                "public_quality_evidence": {"voice_quality_tier": "premium_cloud", "video_backend": "openai_image_motion"},
            }
        ],
        "blocked": [
            {
                "account_id": "acct-1",
                "job_id": "prod-blocked-1",
                "topic_id": "topic-2",
                "topic_title": "Blocked topic",
                "action": "public_quality_blocked",
                "public_quality_blockers": ["premium_visual_backend_required_for_public"],
                "recovery_action": "regenerate_visuals_with_auto_repair",
                "recovery_endpoint": "POST /api/production/jobs/prod-blocked-1/regenerate",
                "public_quality_evidence": {"video_backend": "motion_graphic"},
            }
        ],
    }

    summary = build_money_machine_operator_summary(status, run_next)

    assert summary["next_operator_action"] == "review_and_execute_private_upload"
    assert summary["totals"]["public_ready_private_upload_jobs"] == 1
    assert summary["private_upload_ready"][0]["job_id"] == "prod-ready-1"
    assert summary["recovery_ready"][0]["recovery_action"] == "regenerate_visuals_with_auto_repair"
    assert "Private upload execution still requires approved_private_upload=true." in summary["notes"]


def test_money_machine_summary_endpoint_returns_compact_plan(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(
        "atnos.production.money_machine.MoneyMachineStatusService.build",
        lambda *_args, **_kwargs: _fake_money_machine_status(),
    )
    monkeypatch.setattr(
        "atnos.production.money_machine.upload_private_from_package",
        lambda **kwargs: calls.append(str(kwargs["job_id"])) or {"status": "uploaded_private"},
    )

    with TestClient(app) as client:
        response = client.get("/api/production/money-machine/summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["next_operator_action"] == "review_and_execute_private_upload"
    assert payload["private_upload_ready"][0]["job_id"] == "prod-ready-1"
    assert "money_machine" not in payload
    assert calls == []


def test_money_machine_run_next_blocks_private_upload_when_public_quality_fails(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(
        "atnos.production.money_machine.MoneyMachineStatusService.build",
        lambda *_args, **_kwargs: _fake_money_machine_status(
            ready_job_overrides={
                "public_quality_blockers": ["premium_visual_backend_required_for_public"],
                "public_quality_evidence": {"video_backend": "motion_graphic"},
            }
        ),
    )
    monkeypatch.setattr(
        "atnos.production.money_machine.upload_private_from_package",
        lambda **kwargs: calls.append(str(kwargs["job_id"])) or {"status": "uploaded_private"},
    )

    with TestClient(app) as client:
        response = client.post("/api/production/money-machine/run-next", json={"max_uploads": 1})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "planned"
    assert payload["planned_count"] == 0
    assert payload["blocked"][0]["action"] == "public_quality_blocked"
    assert payload["blocked"][0]["recovery_action"] == "regenerate_visuals_with_auto_repair"
    assert payload["blocked"][0]["recovery_endpoint"] == "POST /api/production/jobs/prod-ready-1/regenerate"
    assert payload["blocked"][0]["public_quality_blockers"] == ["premium_visual_backend_required_for_public"]
    assert calls == []


def test_money_machine_run_next_routes_scene_concreteness_blocker_to_visual_auto_repair(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    monkeypatch.setattr(
        "atnos.production.money_machine.MoneyMachineStatusService.build",
        lambda *_args, **_kwargs: _fake_money_machine_status(
            ready_job_overrides={
                "public_quality_blockers": ["scene_concreteness_risk_for_public"],
                "public_quality_evidence": {
                    "video_backend": "openai_image_motion",
                    "scene_concreteness_risk_count": 1,
                    "passes_scene_concreteness_floor": False,
                },
            }
        ),
    )

    with TestClient(app) as client:
        response = client.post("/api/production/money-machine/run-next", json={"max_uploads": 1})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "planned"
    assert payload["planned_count"] == 0
    blocked = payload["blocked"][0]
    assert blocked["action"] == "public_quality_blocked"
    assert blocked["recovery_action"] == "regenerate_visuals_with_auto_repair"
    assert blocked["recovery_endpoint"] == "POST /api/production/jobs/prod-ready-1/regenerate"
    assert blocked["public_quality_blockers"] == ["scene_concreteness_risk_for_public"]


def test_money_machine_run_next_executes_recovery_regeneration_without_upload(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    _seed_account(app.state.settings)
    _seed_ready_private_job(app.state.settings, tmp_path)
    run_calls: list[str] = []
    upload_calls: list[str] = []
    monkeypatch.setattr(
        "atnos.production.money_machine.MoneyMachineStatusService.build",
        lambda *_args, **_kwargs: _fake_money_machine_status(
            ready_job_overrides={
                "public_quality_blockers": ["scene_concreteness_risk_for_public"],
                "public_quality_evidence": {
                    "video_backend": "openai_image_motion",
                    "scene_concreteness_risk_count": 1,
                    "passes_scene_concreteness_floor": False,
                },
            }
        ),
    )

    async def _fake_run_job(_self, context) -> None:
        run_calls.append(context.job_id)

    monkeypatch.setattr("atnos.production.orchestrator.ProductionOrchestrator.run_job", _fake_run_job)
    monkeypatch.setattr(
        "atnos.production.money_machine.upload_private_from_package",
        lambda **kwargs: upload_calls.append(str(kwargs["job_id"])) or {"status": "uploaded_private"},
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/production/money-machine/run-next",
            json={"max_uploads": 1, "execute": True, "execute_recovery": True},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "recovery_queued"
    assert payload["execute_recovery"] is True
    assert payload["recovery_count"] == 1
    recovery = payload["recovery_results"][0]
    assert recovery["source_job_id"] == "prod-ready-1"
    assert recovery["status"] == "queued"
    assert recovery["job_id"] != "prod-ready-1"
    assert run_calls == [recovery["job_id"]]
    assert upload_calls == []
    with connect(app.state.settings) as db:
        new_job = db.execute(
            "SELECT account_id, topic_id, status FROM pipeline_jobs WHERE id = ?",
            (recovery["job_id"],),
        ).fetchone()
        source = db.execute(
            "SELECT review_status FROM pipeline_jobs WHERE id = 'prod-ready-1'",
        ).fetchone()
    assert dict(new_job) == {"account_id": "acct-1", "topic_id": "topic-1", "status": "queued"}
    assert str(source["review_status"]).startswith(f"regeneration_queued:{recovery['job_id']}:")


def test_money_machine_recovery_respects_cost_guard(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    _seed_account(app.state.settings)
    _seed_ready_private_job(app.state.settings, tmp_path)
    run_calls: list[str] = []

    def _status_with_blocked_budget(*_args, **_kwargs):
        payload = _fake_money_machine_status(
            ready_job_overrides={
                "public_quality_blockers": ["premium_visual_backend_required_for_public"],
            }
        )
        payload["cost_guard"] = {
            "enabled": True,
            "allowed": False,
            "blocked": True,
            "blockers": ["monthly_budget_exceeded"],
            "estimated_action_cost_pln": 4.0,
            "monthly_remaining_pln": 0.0,
        }
        return payload

    monkeypatch.setattr("atnos.production.money_machine.MoneyMachineStatusService.build", _status_with_blocked_budget)

    async def _fake_run_job(_self, context) -> None:
        run_calls.append(context.job_id)

    monkeypatch.setattr("atnos.production.orchestrator.ProductionOrchestrator.run_job", _fake_run_job)

    with TestClient(app) as client:
        response = client.post(
            "/api/production/money-machine/run-next",
            json={"max_uploads": 1, "execute": True, "execute_recovery": True},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "cost_guard_blocked"
    assert payload["execute_recovery"] is False
    assert payload["recovery_count"] == 0
    assert payload["cost_guard"]["blocked"] is True
    assert run_calls == []


def test_money_machine_status_backfills_legacy_visual_quality_plan(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    _seed_account(app.state.settings)
    _seed_ready_private_job(app.state.settings, tmp_path)
    artifact_dir = tmp_path / "data" / "production" / "acct-1" / "prod-ready-1"
    visual_plan = {
        "scenes": [
            {
                "visual_prompt": "A hand moves cash and coins from an open wallet into three ceramic budget bowls on a dark desk.",
                "visual_subject": "hand, cash, coins, wallet, ceramic bowls, desk",
                "motion_style": "slow push-in while cash and coins move between the bowls",
                "headline": "ONE MONEY LEAK",
            },
            {
                "visual_prompt": "An hourglass filled with coins stands between an open wallet and a glass savings jar on a clean desk.",
                "visual_subject": "hourglass, coins, wallet, savings jar, desk",
                "motion_style": "camera push and subtle parallax while coins fall through the hourglass",
                "headline": "THE SMALL FEE",
            },
            {
                "visual_prompt": "A wallet and cash on a desk while a hand sorts bills into simple budget piles.",
                "visual_subject": "wallet, cash, hand, desk",
                "motion_style": "hand sorting cash, slow zoom, paper movement",
                "headline": "FIX IT TODAY",
            },
        ],
        "style_guide": {"profile": "wealth_blueprint"},
    }
    (artifact_dir / "visual_plan.json").write_text(json.dumps(visual_plan), encoding="utf-8")
    legacy_manifest = {
        "backend": {"mode": "openai_image_motion"},
        "clips": [
            {"status": "generated", "backend": "openai_image_motion", "composition_signature": "finance:phone"},
            {"status": "generated", "backend": "openai_image_motion", "composition_signature": "finance:cards"},
            {"status": "generated", "backend": "openai_image_motion", "composition_signature": "finance:wallet"},
        ],
        "quality_plan": {
            "generated_clip_count": 3,
            "unique_composition_count": 3,
            "composition_diversity_ratio": 1.0,
        },
    }
    with connect(app.state.settings) as db:
        db.execute(
            "UPDATE pipeline_jobs SET video_manifest = ? WHERE id = 'prod-ready-1'",
            (json.dumps(legacy_manifest),),
        )
        db.commit()

    with TestClient(app) as client:
        response = client.get("/api/production/money-machine/status", params={"account_id": "acct-1", "include_oauth": "false"})

    assert response.status_code == 200
    job = response.json()["accounts"][0]["ready_private_upload_jobs"][0]
    assert job["public_quality_ready"] is True
    assert job["public_quality_blockers"] == []
    evidence = job["public_quality_evidence"]
    assert evidence["scene_concreteness_risk_count"] == 0
    assert evidence["passes_scene_concreteness_floor"] is True
    with connect(app.state.settings) as db:
        stored = json.loads(db.execute("SELECT video_manifest FROM pipeline_jobs WHERE id = 'prod-ready-1'").fetchone()[0])
    assert stored["quality_plan"]["backfilled"] is True
    assert stored["quality_plan"]["backfill_source"] == "legacy_visual_plan_backfill"


def test_money_machine_run_next_requires_explicit_private_upload_approval(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(
        "atnos.production.money_machine.MoneyMachineStatusService.build",
        lambda *_args, **_kwargs: _fake_money_machine_status(),
    )
    monkeypatch.setattr(
        "atnos.production.money_machine.upload_private_from_package",
        lambda **kwargs: calls.append(str(kwargs["job_id"])) or {"status": "uploaded_private"},
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/production/money-machine/run-next",
            json={"max_uploads": 1, "execute": True, "approved_private_upload": False},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "approval_required"
    assert payload["blocked"][0]["action"] == "review_required"
    assert calls == []


def test_money_machine_run_next_executes_approved_private_upload(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(
        "atnos.production.money_machine.MoneyMachineStatusService.build",
        lambda *_args, **_kwargs: _fake_money_machine_status(),
    )
    monkeypatch.setattr(
        "atnos.production.money_machine.upload_private_from_package",
        lambda **kwargs: calls.append(str(kwargs["job_id"]))
        or {"status": "uploaded_private", "job_id": kwargs["job_id"], "youtube_url": "https://youtu.be/test"},
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/production/money-machine/run-next",
            json={"max_uploads": 1, "execute": True, "approved_private_upload": True},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["executed_count"] == 1
    assert payload["results"][0]["result"]["status"] == "uploaded_private"
    assert calls == ["prod-ready-1"]


def test_money_machine_run_next_limits_uploads_per_account(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    monkeypatch.setattr(
        "atnos.production.money_machine.MoneyMachineStatusService.build",
        lambda *_args, **_kwargs: _fake_multi_account_money_machine_status(),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/production/money-machine/run-next",
            json={"max_uploads": 2, "per_account_limit": 1},
        )

    assert response.status_code == 200
    payload = response.json()
    assert [item["job_id"] for item in payload["planned"]] == ["dark-1", "wealth-1"]


def test_money_machine_run_publication_plans_but_blocks_when_public_upload_disabled(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    monkeypatch.setattr(
        "atnos.production.money_machine.MoneyMachineStatusService.build",
        lambda *_args, **_kwargs: _fake_public_money_machine_status(),
    )

    with TestClient(app) as client:
        response = client.post("/api/production/money-machine/run-publication", json={"max_publications": 1})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "planned"
    assert payload["planned_count"] == 0
    assert payload["blocked"][0]["action"] == "enable_public_upload"


def test_money_machine_run_publication_blocks_quality_gate_before_planning_public(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.allow_public_upload = True
    monkeypatch.setattr(
        "atnos.production.money_machine.MoneyMachineStatusService.build",
        lambda *_args, **_kwargs: _fake_public_money_machine_status(
            public_quality_blockers=["premium_voice_required_for_public"]
        ),
    )

    with TestClient(app) as client:
        response = client.post("/api/production/money-machine/run-publication", json={"max_publications": 1})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "planned"
    assert payload["planned_count"] == 0
    assert payload["blocked"][0]["action"] == "public_quality_blocked"
    assert payload["blocked"][0]["recovery_action"] == "regenerate_with_premium_voice"
    assert payload["blocked"][0]["public_quality_blockers"] == ["premium_voice_required_for_public"]


def test_money_machine_run_publication_requires_explicit_public_approval(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.allow_public_upload = True
    calls: list[str] = []
    monkeypatch.setattr(
        "atnos.production.money_machine.MoneyMachineStatusService.build",
        lambda *_args, **_kwargs: _fake_public_money_machine_status(),
    )
    monkeypatch.setattr(
        "atnos.production.money_machine.promote_private_job_to_public",
        lambda **kwargs: calls.append(str(kwargs["job_id"])) or {"status": "published_public"},
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/production/money-machine/run-publication",
            json={"execute": True, "approved_public_promotion": False},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "approval_required"
    assert payload["blocked"][0]["action"] == "public_review_required"
    assert calls == []


def test_money_machine_run_publication_executes_after_explicit_public_approval(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.allow_public_upload = True
    calls: list[dict] = []
    monkeypatch.setattr(
        "atnos.production.money_machine.MoneyMachineStatusService.build",
        lambda *_args, **_kwargs: _fake_public_money_machine_status(),
    )
    monkeypatch.setattr(
        "atnos.production.money_machine.promote_private_job_to_public",
        lambda **kwargs: calls.append(kwargs)
        or {"status": "scheduled_public", "job_id": kwargs["job_id"], "publish_at": kwargs["publish_at"]},
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/production/money-machine/run-publication",
            json={
                "execute": True,
                "approved_public_promotion": True,
                "publish_at": "2026-06-07T12:00:00+00:00",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["executed_count"] == 1
    assert calls[0]["job_id"] == "prod-public-1"
    assert calls[0]["approved"] is True
    assert calls[0]["publish_at"] == "2026-06-07T12:00:00+00:00"


def test_youtube_oauth_resolver_matches_youtube_email_account_from_id_token(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    try:
        bootstrap_test_app(app)
        settings = app.state.settings
        token_ref = OAuthTokenStore(settings).store_token_bundle(
            OAuthTokenBundle(
                access_token="access-token",
                refresh_token="refresh-token",
                token_type="Bearer",
                expires_in=3600,
                scope="openid https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly https://www.googleapis.com/auth/yt-analytics.readonly",
                id_token=_fake_id_token({"email": "account-c5034bf4@example.invalid"}),
            )
        )
        with app.state.database.session_scope() as session:
            account = AccountCredentialsService(session, settings).create_account(
                label="YouTube atnos",
                provider_key="youtube",
                account_identifier="account-c5034bf4@example.invalid",
                secret_type="oauth_token_later",
                secret_value=None,
                risk_level="medium",
                allowed_capabilities=["confirmed_write_later"],
                linked_integration_key="youtube",
                linked_project_id=None,
                notes=None,
            )
            session.add(
                OAuthConnection(
                    connector_key="youtube-studio",
                    display_name="YouTube Studio",
                    status="connected",
                    scopes_json=[
                        "openid",
                        "https://www.googleapis.com/auth/youtube.upload",
                        "https://www.googleapis.com/auth/youtube.readonly",
                        "https://www.googleapis.com/auth/yt-analytics.readonly",
                    ],
                    account_id=account.id,
                    token_ref=token_ref,
                    code_verifier_ref=None,
                    state_hash=None,
                    metadata_json={},
                    audit_log_json=[],
                )
            )
            session.flush()

            resolved = resolve_youtube_oauth_account_id(settings, "youtube:account-c5034bf4@example.invalid", session=session)

        assert resolved == account.id
    finally:
        app.state.database.dispose()


def test_gmail_oauth_resolver_matches_content_email_account_from_id_token(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    try:
        bootstrap_test_app(app)
        settings = app.state.settings
        token_ref = OAuthTokenStore(settings).store_token_bundle(
            OAuthTokenBundle(
                access_token="gmail-access-token",
                refresh_token="gmail-refresh-token",
                token_type="Bearer",
                expires_in=3600,
                scope=(
                    "openid email profile "
                    "https://www.googleapis.com/auth/gmail.readonly "
                    "https://www.googleapis.com/auth/gmail.compose "
                    "https://www.googleapis.com/auth/gmail.send"
                ),
                id_token=_fake_id_token({"email": "account-d6add2f8@example.invalid"}),
            )
        )
        with app.state.database.session_scope() as session:
            account = AccountCredentialsService(session, settings).create_account(
                label="Gmail atnos2909",
                provider_key="gmail",
                account_identifier="account-d6add2f8@example.invalid",
                secret_type="oauth_token_later",
                secret_value=None,
                risk_level="medium",
                allowed_capabilities=["confirmed_write_later"],
                linked_integration_key="gmail",
                linked_project_id=None,
                notes=None,
            )
            session.add(
                OAuthConnection(
                    connector_key="gmail",
                    display_name="Gmail",
                    status="connected",
                    scopes_json=[
                        "openid",
                        "email",
                        "profile",
                        "https://www.googleapis.com/auth/gmail.readonly",
                        "https://www.googleapis.com/auth/gmail.compose",
                        "https://www.googleapis.com/auth/gmail.send",
                    ],
                    account_id=account.id,
                    token_ref=token_ref,
                    code_verifier_ref=None,
                    state_hash=None,
                    metadata_json={},
                    audit_log_json=[],
                )
            )
            session.flush()

            resolved = resolve_gmail_oauth_account_id(settings, "youtube:account-d6add2f8@example.invalid", session=session)

        assert resolved == account.id
    finally:
        app.state.database.dispose()


def _fake_id_token(payload: dict[str, str]) -> str:
    header = {"alg": "none", "typ": "JWT"}
    return ".".join(
        [
            _b64url_json(header),
            _b64url_json(payload),
            "signature",
        ]
    )


def _b64url_json(payload: dict[str, str]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _fake_money_machine_status(ready_job_overrides: dict | None = None) -> dict:
    ready_job = {
        "job_id": "prod-ready-1",
        "topic_id": "topic-1",
        "topic_title": "The Money Habit Keeping You Broke",
        "upload_package_path": "package.json",
        "qa_report_path": "qa.json",
        "artifact_dir": "artifacts/prod-ready-1",
        "updated_at": "2026-06-06T18:00:00+00:00",
        "auto_uploadable": True,
        "public_quality_ready": True,
        "public_quality_evidence": {
            "voice_quality_tier": "premium_cloud",
            "video_backend": "openai_image_motion",
            "generated_clip_count": 3,
            "generated_image_text_risk_count": 0,
            "generated_image_visual_risk_count": 0,
        },
    }
    if ready_job_overrides:
        ready_job.update(ready_job_overrides)
    return {
        "status": "ready_for_private_review",
        "accounts": [
            {
                "account_id": "youtube:account-c5034bf4@example.invalid",
                "youtube_oauth": {"ready": True, "account_id": 50},
                "ready_private_upload_jobs": [ready_job],
            }
        ],
    }


def _fake_multi_account_money_machine_status() -> dict:
    return {
        "status": "ready_for_private_review",
        "accounts": [
            {
                "account_id": "youtube:account-d6add2f8@example.invalid",
                "youtube_oauth": {"ready": True, "account_id": 48},
                "ready_private_upload_jobs": [
                    {"job_id": "dark-1", "topic_id": "dark-topic-1", "topic_title": "Dark one", "upload_package_path": "a.json"},
                    {"job_id": "dark-2", "topic_id": "dark-topic-2", "topic_title": "Dark two", "upload_package_path": "b.json"},
                ],
            },
            {
                "account_id": "youtube:account-c5034bf4@example.invalid",
                "youtube_oauth": {"ready": True, "account_id": 50},
                "ready_private_upload_jobs": [
                    {
                        "job_id": "wealth-1",
                        "topic_id": "wealth-topic-1",
                        "topic_title": "Wealth one",
                        "upload_package_path": "c.json",
                    }
                ],
            },
        ],
    }


def _fake_public_money_machine_status(public_quality_blockers: list[str] | None = None) -> dict:
    quality_blockers = public_quality_blockers or []
    return {
        "status": "public_review_blocked",
        "accounts": [
            {
                "account_id": "youtube:account-c5034bf4@example.invalid",
                "youtube_oauth": {"ready": True, "account_id": 50},
                "public_promotion_jobs": [
                    {
                        "job_id": "prod-public-1",
                        "topic_id": "topic-public-1",
                        "topic_title": "The Money Habit Keeping You Broke",
                        "youtube_video_id": "yt-private-1",
                        "youtube_url": "https://www.youtube.com/watch?v=yt-private-1",
                        "public_quality_blockers": quality_blockers,
                        "public_quality_evidence": {"voice_quality_tier": "cloud_neural_fallback"} if quality_blockers else {},
                    }
                ],
            }
        ],
    }
