from __future__ import annotations

import asyncio
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

from atnos.intelligence.storage import connect, init_intelligence_db, utc_now_iso  # noqa: E402
from atnos.production.qa_gate import REQUIRED_QA_CHECK_NAMES  # noqa: E402
from test_support import create_test_app  # noqa: E402


def test_distribution_plan_exports_public_quality_job_but_blocks_unimplemented_platform_uploads(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_distribution_job(settings, tmp_path, job_id="prod-dist-1")

    with TestClient(app) as client:
        response = client.get("/api/production/jobs/prod-dist-1/distribution-plan")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "private_upload_ready"
    assert payload["export_ready"] is True
    assert payload["public_quality_ready"] is True
    platforms = {item["platform"]: item for item in payload["platforms"]}

    youtube = platforms["youtube_shorts"]
    assert youtube["status"] == "api_private_ready"
    assert youtube["upload_ready"] is True
    assert youtube["private_upload_ready"] is True
    assert youtube["public_upload_ready"] is False
    assert "/api/production/jobs/prod-dist-1/upload-private" == youtube["endpoints"]["private_upload"]

    tiktok = platforms["tiktok"]
    instagram = platforms["instagram_reels"]
    for platform in (tiktok, instagram):
        assert platform["status"] == "manual_upload_required"
        assert platform["export_ready"] is True
        assert platform["upload_ready"] is False
        assert platform["connector_status"] == "planned"
        assert platform["requires_manual_upload"] is True
        assert any("connector_not_configured" in blocker for blocker in platform["blockers"])
        assert Path(platform["artifact_paths"]["video_file_path"]).exists()


def test_distribution_plan_blocks_qa_failed_fallback_render_for_every_platform(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_distribution_job(
        settings,
        tmp_path,
        job_id="prod-dist-bad",
        status="qa_failed",
        qa_report={
            "overall": "failed",
            "auto_uploadable": False,
            "blocking_failures": ["final_mp4_placeholder_fallback"],
        },
        voice_quality_tier="local_neural_fallback",
        video_manifest={
            "backend": {"mode": "placeholder"},
            "fallback_used": True,
            "clips": [{"backend": "motion_graphic"}],
        },
    )

    with TestClient(app) as client:
        response = client.get("/api/production/jobs/prod-dist-bad/distribution-plan")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "blocked"
    assert payload["export_ready"] is False
    assert payload["public_quality_ready"] is False
    assert "qa_not_passed" in payload["artifact_blockers"]
    assert "premium_voice_required_for_public" in payload["public_quality_blockers"]
    assert "premium_visual_backend_required_for_public" in payload["public_quality_blockers"]
    for platform in payload["platforms"]:
        assert platform["export_ready"] is False
        assert platform["upload_ready"] is False
        assert platform["public_upload_ready"] is False
        assert "qa_not_passed" in platform["blockers"]


def test_distribution_plan_returns_404_for_unknown_job(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    asyncio.run(init_intelligence_db(app.state.settings))

    with TestClient(app) as client:
        response = client.get("/api/production/jobs/missing/distribution-plan")

    assert response.status_code == 404


def test_distribution_package_writes_platform_manifests_for_ready_job(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_distribution_job(settings, tmp_path, job_id="prod-package-1")

    with TestClient(app) as client:
        response = client.post("/api/production/jobs/prod-package-1/distribution-package", json={})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "created"
    assert payload["created_count"] == 3
    assert payload["blocked_count"] == 0
    packages = {item["platform"]: item for item in payload["packages"]}
    assert set(packages) == {"youtube_shorts", "tiktok", "instagram_reels"}
    for package in packages.values():
        manifest_path = Path(package["manifest_path"])
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["requires_owner_approval"] is True
        assert Path(manifest["video_file_path"]).exists()
        assert Path(package["caption_path"]).read_text(encoding="utf-8").strip()
        assert Path(package["readme_path"]).exists()
    assert packages["tiktok"]["status"] == "manual_upload_package_ready"
    assert packages["instagram_reels"]["status"] == "manual_upload_package_ready"
    assert packages["youtube_shorts"]["status"] == "private_upload_ready"


def test_distribution_package_does_not_write_packages_for_qa_failed_job(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_distribution_job(
        settings,
        tmp_path,
        job_id="prod-package-bad",
        status="qa_failed",
        qa_report={
            "overall": "failed",
            "auto_uploadable": False,
            "blocking_failures": ["final_mp4_placeholder_fallback"],
        },
        voice_quality_tier="local_neural_fallback",
        video_manifest={
            "backend": {"mode": "placeholder"},
            "fallback_used": True,
            "clips": [{"backend": "motion_graphic"}],
        },
    )

    with TestClient(app) as client:
        response = client.post("/api/production/jobs/prod-package-bad/distribution-package", json={})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "blocked"
    assert payload["created_count"] == 0
    assert payload["blocked_count"] == 3
    assert payload["packages"] == []
    assert all("qa_not_passed" in item["blockers"] for item in payload["blocked"])
    package_root = Path(payload["package_root"])
    assert package_root.exists()
    assert not list(package_root.glob("*/release_manifest.json"))


def test_distribution_package_rejects_unknown_platform(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_distribution_job(settings, tmp_path, job_id="prod-package-unknown")

    with TestClient(app) as client:
        response = client.post(
            "/api/production/jobs/prod-package-unknown/distribution-package",
            json={"platforms": ["snapchat"]},
        )

    assert response.status_code == 400


def _seed_distribution_job(
    settings,
    root: Path,
    *,
    job_id: str,
    status: str = "qa_passed",
    qa_report: dict | None = None,
    voice_quality_tier: str = "premium_cloud",
    video_manifest: dict | None = None,
) -> None:
    now = utc_now_iso()
    artifact_dir = root / "data" / "production" / "acct-dist-1" / job_id
    assets_dir = artifact_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    video_path = assets_dir / "final_short.mp4"
    thumbnail_path = assets_dir / "thumbnail.jpg"
    subtitle_path = assets_dir / "captions.srt"
    qa_path = artifact_dir / "qa_report.json"
    package_path = artifact_dir / "youtube_private_upload_package.json"
    video_path.write_bytes(b"fake mp4")
    thumbnail_path.write_bytes(b"fake jpg")
    subtitle_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nTest\n", encoding="utf-8")

    qa = qa_report or _valid_public_quality_qa_report()
    qa_path.write_text(json.dumps(qa, ensure_ascii=False), encoding="utf-8")
    package = {
        "review_id": f"review-{job_id}",
        "channel_account_id": "acct-dist-1",
        "topic_id": "topic-dist-1",
        "video_file_path": str(video_path),
        "thumbnail_file_path": str(thumbnail_path),
        "subtitle_file_path": str(subtitle_path),
        "qa_report_path": str(qa_path),
        "auto_uploadable": bool(qa.get("auto_uploadable")),
        "upload_metadata": {
            "title": "Why money feels impossible until this clicks",
            "description": "Private review package for cross-platform distribution.",
            "tags": ["shorts", "money", "finance"],
            "visibility": "private",
            "language": "en",
            "audio_language": "en",
            "made_for_kids": False,
        },
        "status": "awaiting_human_review",
        "public_upload_allowed": False,
    }
    package_path.write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")

    style_guide = {
        "style_guide": {
            "used": True,
            "status": "obsidian_channel_style",
            "source": "F:/AtnosSystem/AppData/Roaming/Atnos/obsidian/style_guide.md",
        }
    }
    audio_timestamps = {
        "quality_tier": voice_quality_tier,
        "voice_backend_grade": "premium" if voice_quality_tier == "premium_cloud" else "fallback",
        "voice_profile": {"voice": "alloy", "language_code": "en"},
    }
    manifest = video_manifest or _public_quality_video_manifest()
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO topic_queue (
                id, account_id, title, description, status, total_score, created_at, updated_at
            )
            VALUES ('topic-dist-1', 'acct-dist-1', 'Distribution topic', 'Opis', 'in_production', 91, ?, ?)
            """,
            (now, now),
        )
        db.execute(
            """
            INSERT INTO pipeline_jobs (
                id, account_id, topic_id, status, current_step, artifact_dir, qa_report_path,
                upload_package_path, audio_timestamps, hooks, script, asset_plan, video_manifest,
                qa_report, created_at, updated_at
            )
            VALUES (?, 'acct-dist-1', 'topic-dist-1', ?, 'upload_package', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                status,
                str(artifact_dir),
                str(qa_path),
                str(package_path),
                json.dumps(audio_timestamps),
                json.dumps(style_guide),
                json.dumps(style_guide),
                json.dumps(style_guide),
                json.dumps(manifest),
                json.dumps(qa),
                now,
                now,
            ),
        )
        db.commit()


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
                "detail": "Seeded complete QA contract for distribution planner tests.",
            }
        )
    return {
        "overall": "passed",
        "auto_uploadable": True,
        "required_check_count": len(required_checks),
        "required_checks": required_checks,
        "blocking_failures": [],
        "advisory_failures": [],
        "operating_standards": {
            "used": True,
            "status": "ready",
            "project_type": "shorts_channel",
            "source": {"path": "style-guide.md", "exists": True},
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


def _public_quality_video_manifest() -> dict:
    return {
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
            "avg_visual_relevance_score": 0.82,
            "passes_visual_relevance_floor": True,
            "topic_anchor_risk_count": 0,
            "passes_topic_anchor_floor": True,
            "generated_image_text_risk_count": 0,
            "generated_image_visual_risk_count": 0,
            "passes_generated_image_text_floor": True,
            "passes_generated_image_visual_floor": True,
        },
    }
