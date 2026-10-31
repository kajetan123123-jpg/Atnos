from __future__ import annotations

import asyncio
import base64
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import pytest

from atnos.intelligence.storage import connect, init_intelligence_db, json_loads, utc_now_iso
from atnos.production.private_upload import ProductionUploadError, upload_private_from_package
from atnos.production.qa_gate import REQUIRED_QA_CHECK_NAMES
from atnos.production.quality_scheduler import ProductionQualityScheduler, QUALITY_REVIEW_BLOCKED
from atnos.production.review_dispatcher import ProductionReviewDispatcher
from atnos.production.visual_semantic_audit import (
    _enforce_wealth_blueprint_medium,
    _prompt,
    audit_visual_contract_compliance,
    audit_visual_semantics,
    compare_visual_candidates,
    normalize_semantic_audit,
)
from test_support import create_test_app


def test_quality_scheduler_blocks_public_quality_failures_before_review(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_qa_passed_job(settings, tmp_path, job_id="prod-quality-blocked")

    result = ProductionQualityScheduler(settings).audit_due_jobs(limit=1)

    assert result["status"] == "completed"
    assert result["checked_count"] == 1
    assert result["blocked_count"] == 1
    assert result["jobs"][0]["status"] == "blocked"
    assert "premium_visual_backend_required_for_public" in result["jobs"][0]["blockers"]

    with connect(settings) as db:
        row = db.execute(
            "SELECT review_status, quality_review_status, quality_review_result FROM pipeline_jobs WHERE id = ?",
            ("prod-quality-blocked",),
        ).fetchone()
    assert row is not None
    assert row["review_status"] == QUALITY_REVIEW_BLOCKED
    assert row["quality_review_status"] == QUALITY_REVIEW_BLOCKED
    persisted = json_loads(row["quality_review_result"], {})
    assert persisted["status"] == "blocked"


def test_image_scene_semantic_prompt_does_not_require_temporal_progression() -> None:
    prompt = _prompt(
        profile_key="wealth_blueprint",
        topic_title="Small daily purchases drain savings",
        artifact_kind="image_scene_candidate",
    )

    assert "one static scene candidate" in prompt
    assert "Do not require temporal progression" in prompt
    assert "required_visual_evidence_present" in prompt
    assert "visible_required_items" in prompt
    assert "missing_required_items" in prompt
    assert "duplicated_primary_subject" in prompt
    assert "final storyboard audit" in prompt
    assert "The generated illustration must remain text-free" in prompt
    assert '"visual_medium"' in prompt
    assert "no photorealism" in prompt


def test_wealth_blueprint_image_candidate_requires_hand_drawn_medium() -> None:
    photographic = _enforce_wealth_blueprint_medium(
        {"status": "pass", "passed": True, "blockers": []},
        {"visual_medium": "photograph"},
    )
    assert photographic["status"] == "fail"
    assert "wealth_blueprint_illustration_medium_required" in photographic["blockers"]
    illustrated = _enforce_wealth_blueprint_medium(
        {"status": "pass", "passed": True, "blockers": []},
        {"visual_medium": "hand_drawn_illustration"},
    )
    assert illustrated["passed"] is True


def test_storyboard_prompt_does_not_invent_people_cards_or_banknotes() -> None:
    prompt = _prompt(
        profile_key="wealth_blueprint",
        topic_title="The One Money Rule Most Beginners Skip",
        artifact_kind="storyboard_contact_sheet",
    )

    assert "composition_type=contact_sheet is expected" in prompt
    assert "Do not require people, hands, bank cards, banknotes" in prompt
    assert "at least four of six tiles" in prompt
    assert "Repeated brand chrome" in prompt
    assert "missing_required_items must be []" in prompt
    assert "splitting income, through spending accumulation" in prompt


def test_review_dispatcher_does_not_send_review_mail_for_quality_blocked_job(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_qa_passed_job(settings, tmp_path, job_id="prod-review-blocked")
    events: list[dict] = []

    result = asyncio.run(
        ProductionReviewDispatcher(
            settings,
            session_scope=app.state.database.session_scope,
            event_sink=events.append,
        ).handle_job("prod-review-blocked")
    )

    assert result["status"] == QUALITY_REVIEW_BLOCKED
    assert result["quality_review"]["status"] == "blocked"
    assert any(event.get("status") == QUALITY_REVIEW_BLOCKED for event in events)

    with connect(settings) as db:
        row = db.execute(
            "SELECT review_notified_at, review_notification_path FROM pipeline_jobs WHERE id = ?",
            ("prod-review-blocked",),
        ).fetchone()
    assert row is not None
    assert row["review_notified_at"] is None
    assert row["review_notification_path"] is None


def test_private_upload_is_blocked_by_quality_review_before_youtube_call(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_qa_passed_job(settings, tmp_path, job_id="prod-upload-blocked")
    ProductionQualityScheduler(settings).audit_job("prod-upload-blocked")

    with app.state.database.session_scope() as session:
        with pytest.raises(ProductionUploadError, match="Production quality review blocks private upload"):
            upload_private_from_package(settings=settings, session=session, job_id="prod-upload-blocked")


def test_quality_scheduler_persists_semantic_visual_blockers(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_qa_passed_job(settings, tmp_path, job_id="prod-semantic-blocked")

    def fake_semantic_audit(_settings, _image_path, *, profile_key, topic_title, artifact_kind):
        if artifact_kind == "thumbnail":
            return {
                "status": "fail",
                "passed": False,
                "artifact_kind": artifact_kind,
                "path": str(_image_path),
                "model": "fake-vision",
                "scores": {"topic_relevance_score": 2.0},
                "blockers": ["topic_relevance_score_below_floor", "semantic_childish"],
                "summary": f"{profile_key}:{topic_title}",
            }
        return {"status": "skipped", "passed": True, "artifact_kind": artifact_kind, "blockers": []}

    monkeypatch.setattr("atnos.production.quality_scheduler.audit_visual_semantics", fake_semantic_audit)

    result = ProductionQualityScheduler(settings).audit_job("prod-semantic-blocked")

    assert result["status"] == "blocked"
    assert "semantic_visual:topic_relevance_score_below_floor" in result["blockers"]
    assert result["thumbnail_semantic_audit"]["model"] == "fake-vision"
    with connect(settings) as db:
        row = db.execute(
            "SELECT quality_review_result FROM pipeline_jobs WHERE id = ?",
            ("prod-semantic-blocked",),
        ).fetchone()
    persisted = json_loads(row["quality_review_result"], {})
    assert persisted["thumbnail_semantic_audit"]["blockers"] == [
        "topic_relevance_score_below_floor",
        "semantic_childish",
    ]


def test_semantic_visual_audit_normalizes_low_scores_and_flags(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    payload = {
        "topic_relevance_score": 8,
        "maturity_score": 3,
        "premium_style_score": 4,
        "clarity_score": 9,
        "flags": {
            "childish": True,
            "generic_blur": False,
            "off_topic": False,
            "repetitive": True,
            "synthetic_ai_artifacts": True,
            "visual_narrative_missing": True,
        },
        "summary": "Too playful for the finance channel.",
    }

    result = normalize_semantic_audit(
        payload,
        settings=settings,
        path=tmp_path / "thumb.png",
        artifact_kind="thumbnail",
        model="fake-vision",
    )

    assert result["status"] == "fail"
    assert "maturity_score_below_floor" in result["blockers"]
    assert "premium_style_score_below_floor" in result["blockers"]
    assert "semantic_childish" in result["blockers"]
    assert "semantic_repetitive" in result["blockers"]
    assert "semantic_synthetic_ai_artifacts" in result["blockers"]
    assert "semantic_visual_narrative_missing" in result["blockers"]


def test_semantic_visual_audit_ignores_positive_blocker_phrases(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    payload = {
        "topic_relevance_score": 10,
        "maturity_score": 10,
        "premium_style_score": 9,
        "clarity_score": 9,
        "flags": {},
        "blockers": [
            "All frames depict concrete historical actions related to the topic",
            "All frames contain concrete money-related objects",
            "No repetition in scene composition or props",
            "No repetitive compositions or off-topic elements",
            "Text overlays form a coherent narrative",
        ],
        "summary": "Coherent historical storyboard.",
    }

    result = normalize_semantic_audit(
        payload,
        settings=settings,
        path=tmp_path / "storyboard.jpg",
        artifact_kind="storyboard",
        model="fake-vision",
    )

    assert result["status"] == "pass"
    assert result["passed"] is True
    assert result["blockers"] == []


def test_storyboard_audit_treats_repeated_brand_palette_as_continuity(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    payload = {
        "topic_relevance_score": 9,
        "maturity_score": 9,
        "premium_style_score": 9,
        "clarity_score": 8,
        "duplicated_primary_subject": False,
        "flags": {"repetitive": True},
        "blockers": ["Repetitive use of green finance color palette across tiles"],
        "summary": "Distinct finance beats share one controlled brand palette.",
    }

    result = normalize_semantic_audit(
        payload,
        settings=app.state.settings,
        path=tmp_path / "storyboard.jpg",
        artifact_kind="storyboard_contact_sheet",
        model="fake-vision",
    )

    assert result["status"] == "pass"
    assert result["blockers"] == []


def test_storyboard_audit_still_blocks_repeated_primary_subjects(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    payload = {
        "topic_relevance_score": 9,
        "maturity_score": 9,
        "premium_style_score": 9,
        "clarity_score": 8,
        "duplicated_primary_subject": False,
        "flags": {"repetitive": True},
        "blockers": ["The same calculator is the primary subject in five of six tiles"],
        "summary": "The storyboard repeats one object instead of progressing.",
    }

    result = normalize_semantic_audit(
        payload,
        settings=app.state.settings,
        path=tmp_path / "storyboard.jpg",
        artifact_kind="storyboard_contact_sheet",
        model="fake-vision",
    )

    assert result["status"] == "fail"
    assert "semantic_repetitive" in result["blockers"]


def test_storyboard_audit_treats_repeated_source_values_as_educational_continuity(
    tmp_path: Path,
) -> None:
    app = create_test_app(tmp_path)
    payload = {
        "topic_relevance_score": 8,
        "maturity_score": 9,
        "premium_style_score": 9,
        "clarity_score": 7,
        "duplicated_primary_subject": False,
        "flags": {"repetitive": True},
        "blockers": ["Repetitive use of $100/$110.25 props across tiles"],
        "summary": "Distinct educational scenes repeat sourced values for continuity.",
    }

    result = normalize_semantic_audit(
        payload,
        settings=app.state.settings,
        path=tmp_path / "storyboard.jpg",
        artifact_kind="storyboard_contact_sheet",
        model="fake-vision",
    )

    assert result["status"] == "pass"
    assert result["blockers"] == []


def test_storyboard_audit_does_not_excuse_repeated_physical_props_with_values(
    tmp_path: Path,
) -> None:
    app = create_test_app(tmp_path)
    payload = {
        "topic_relevance_score": 8,
        "maturity_score": 9,
        "premium_style_score": 9,
        "clarity_score": 8,
        "duplicated_primary_subject": False,
        "flags": {"repetitive": True},
        "blockers": ["The same $100 wallet prop and camera composition repeat in five tiles"],
        "summary": "The storyboard repeats one physical composition.",
    }

    result = normalize_semantic_audit(
        payload,
        settings=app.state.settings,
        path=tmp_path / "storyboard.jpg",
        artifact_kind="storyboard_contact_sheet",
        model="fake-vision",
    )

    assert result["status"] == "fail"
    assert "semantic_repetitive" in result["blockers"]


def test_image_candidate_semantic_audit_rejects_missing_required_items(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    payload = {
        "topic_relevance_score": 9,
        "maturity_score": 9,
        "premium_style_score": 9,
        "clarity_score": 9,
        "object_integrity_score": 9,
        "required_visual_evidence_present": True,
        "visible_required_items": ["wallet"],
        "missing_required_items": ["three separate coin trails"],
        "composition_type": "single_scene",
        "duplicated_primary_subject": False,
        "malformed_objects": [],
        "flags": {},
        "blockers": [],
    }

    result = normalize_semantic_audit(
        payload,
        settings=settings,
        path=tmp_path / "candidate.png",
        artifact_kind="image_scene_candidate",
        model="fake-vision",
    )

    assert result["status"] == "fail"
    assert "semantic_required_items_missing" in result["blockers"]


def test_image_candidate_semantic_audit_rejects_split_panel_and_duplicate_subject(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    payload = {
        "topic_relevance_score": 9,
        "maturity_score": 9,
        "premium_style_score": 9,
        "clarity_score": 9,
        "object_integrity_score": 9,
        "required_visual_evidence_present": True,
        "visible_required_items": ["wallet", "coin trail"],
        "missing_required_items": [],
        "composition_type": "split_panel",
        "duplicated_primary_subject": True,
        "malformed_objects": [],
        "flags": {},
        "blockers": [],
    }

    result = normalize_semantic_audit(
        payload,
        settings=settings,
        path=tmp_path / "candidate.png",
        artifact_kind="image_scene_candidate",
        model="fake-vision",
    )

    assert result["status"] == "fail"
    assert "semantic_collage_or_split_panel" in result["blockers"]
    assert "duplicated_primary_subject_detected" in result["blockers"]


def test_semantic_visual_audit_uses_compact_json_vision_payload(tmp_path: Path, monkeypatch) -> None:
    pillow = pytest.importorskip("PIL.Image")
    image_path = tmp_path / "large-thumbnail.png"
    pillow.new("RGB", (2200, 1600), color=(72, 80, 96)).save(image_path)

    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.app_env = "development"
    settings.shorts_semantic_visual_audit_enabled = True
    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        @staticmethod
        def json() -> dict[str, object]:
            return {
                "message": {
                    "content": json.dumps(
                        {
                            "topic_relevance_score": 9,
                            "maturity_score": 9,
                            "premium_style_score": 8,
                            "clarity_score": 8,
                            "flags": {},
                            "blockers": [],
                            "summary": "Concrete finance scene.",
                        }
                    )
                }
            }

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def post(self, _url: str, *, json: dict[str, object]) -> FakeResponse:
            captured.update(json)
            return FakeResponse()

    monkeypatch.setattr("atnos.production.visual_semantic_audit.httpx.Client", FakeClient)

    result = audit_visual_semantics(
        settings,
        image_path,
        profile_key="wealth_blueprint",
        topic_title="Why emergency savings matter",
        artifact_kind="thumbnail",
    )

    encoded = captured["messages"][0]["images"][0]
    with pillow.open(BytesIO(base64.b64decode(encoded))) as prepared:
        assert max(prepared.size) <= 1024
    assert captured["format"] == "json"
    assert captured["think"] is False
    assert captured["options"] == {"num_ctx": 4096, "temperature": 0.1}
    assert result["passed"] is True
    assert result["audit_input"]["encoding"] == "jpeg"
    assert result["audit_input"]["audit_size_bytes"] < result["audit_input"]["source_size_bytes"]
    assert result["audit_input"]["source_sha256"] == hashlib.sha256(image_path.read_bytes()).hexdigest()


def test_semantic_visual_audit_accepts_json_from_ollama_thinking_field(tmp_path: Path, monkeypatch) -> None:
    pillow = pytest.importorskip("PIL.Image")
    image_path = tmp_path / "candidate.png"
    pillow.new("RGB", (512, 896), color=(72, 80, 96)).save(image_path)
    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.app_env = "development"
    settings.shorts_semantic_visual_audit_enabled = True

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        @staticmethod
        def json() -> dict[str, object]:
            return {
                "message": {
                    "content": "",
                    "thinking": json.dumps(
                        {
                            "topic_relevance_score": 9,
                            "maturity_score": 9,
                            "premium_style_score": 9,
                            "clarity_score": 9,
                            "object_integrity_score": 9,
                            "visual_medium": "hand_drawn_illustration",
                            "required_visual_evidence_present": True,
                            "visible_required_items": ["wallet"],
                            "missing_required_items": [],
                            "composition_type": "single_scene",
                            "duplicated_primary_subject": False,
                            "malformed_objects": [],
                            "flags": {},
                            "blockers": [],
                            "summary": "Concrete finance scene.",
                        }
                    ),
                }
            }

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def post(self, _url: str, *, json: dict[str, object]) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr("atnos.production.visual_semantic_audit.httpx.Client", FakeClient)

    result = audit_visual_semantics(
        settings,
        image_path,
        profile_key="wealth_blueprint",
        topic_title="Emergency fund",
        artifact_kind="image_scene_candidate",
    )

    assert result["passed"] is True
    assert result["response_field"] == "thinking"


def test_compare_visual_candidates_returns_bounded_ranking(tmp_path: Path, monkeypatch) -> None:
    pillow = pytest.importorskip("PIL.Image")
    paths = [tmp_path / f"candidate-{index}.png" for index in range(1, 4)]
    for index, path in enumerate(paths, start=1):
        pillow.new("RGB", (128, 192), color=(40 * index, 50, 60)).save(path)
    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.app_env = "development"
    settings.shorts_semantic_visual_audit_enabled = True
    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        @staticmethod
        def json() -> dict[str, object]:
            return {
                "message": {
                    "content": json.dumps(
                        {
                            "ranked_candidate_indexes": [3, 1, 99, 3],
                            "rejected_candidate_indexes": [2, 42],
                            "defects_by_candidate": {"2": ["coin fused into leather"]},
                            "selection_confidence": 9,
                            "summary": "Candidate three is the most realistic.",
                        }
                    )
                }
            }

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def post(self, _url: str, *, json: dict[str, object]) -> FakeResponse:
            captured.update(json)
            return FakeResponse()

    monkeypatch.setattr("atnos.production.visual_semantic_audit.httpx.Client", FakeClient)

    result = compare_visual_candidates(settings, paths, scene_contract="A realistic wallet and loose coins.")

    assert result["status"] == "pass"
    assert result["ranked_candidate_indexes"] == [3, 1, 2]
    assert result["rejected_candidate_indexes"] == [2]
    assert len(captured["messages"][0]["images"]) == 1
    with pillow.open(BytesIO(base64.b64decode(captured["messages"][0]["images"][0]))) as contact_sheet:
        assert contact_sheet.width > contact_sheet.height
    assert captured["options"] == {"num_ctx": 4096, "temperature": 0.0}


def test_literal_visual_contract_audit_reads_bounded_json_from_thinking(tmp_path: Path, monkeypatch) -> None:
    pillow = pytest.importorskip("PIL.Image")
    image_path = tmp_path / "candidate.png"
    pillow.new("RGB", (128, 192), color=(180, 160, 120)).save(image_path)
    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.app_env = "development"
    settings.shorts_semantic_visual_audit_enabled = True
    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        @staticmethod
        def json() -> dict[str, object]:
            return {
                "message": {
                    "content": "",
                    "thinking": json.dumps(
                        {
                            "contract_satisfied": False,
                            "visible_violations": ["one coin is distinctly gold"],
                            "confidence": 9,
                        }
                    ),
                }
            }

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def post(self, _url: str, *, json: dict[str, object]) -> FakeResponse:
            captured.update(json)
            return FakeResponse()

    monkeypatch.setattr("atnos.production.visual_semantic_audit.httpx.Client", FakeClient)

    result = audit_visual_contract_compliance(
        settings,
        image_path,
        required_visual_contract="ordinary silver coins only; no gold coin",
    )

    assert result["status"] == "fail"
    assert result["contract_satisfied"] is False
    assert result["violations"] == ["one coin is distinctly gold"]
    assert result["response_field"] == "thinking"
    assert captured["options"] == {"num_ctx": 4096, "num_predict": 512, "temperature": 0.0}
    assert captured["format"]["properties"]["contract_satisfied"] == {"type": "boolean"}

def _seed_qa_passed_job(settings, root: Path, *, job_id: str) -> None:
    now = utc_now_iso()
    artifact_dir = root / job_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    qa_report = _valid_qa_report()
    qa_report_path = artifact_dir / "qa_report.json"
    qa_report_path.write_text(json.dumps(qa_report), encoding="utf-8")
    video_path = artifact_dir / "final_short.mp4"
    video_path.write_bytes(b"fake-video")
    thumbnail_path = artifact_dir / "thumbnail.png"
    thumbnail_path.write_bytes(b"fake-thumbnail" * 5000)
    upload_package = {
        "video_file_path": str(video_path),
        "thumbnail_file_path": str(thumbnail_path),
        "qa_report_path": str(qa_report_path),
        "auto_uploadable": True,
        "public_quality_ready": False,
        "public_quality_blockers": ["premium_visual_backend_required_for_public"],
        "public_quality_next_action": "configure_premium_visual_backend_and_regenerate",
        "upload_metadata": {
            "title": "Test short",
            "description": "Test",
            "tags": ["shorts"],
            "visibility": "private",
        },
    }
    upload_package_path = artifact_dir / "youtube_private_upload_package.json"
    upload_package_path.write_text(json.dumps(upload_package), encoding="utf-8")
    style_guide = {"style_guide": {"used": True, "status": "test", "source": "test-style-guide.md"}}
    video_manifest = {
        "backend": "motion_graphic",
        "clips": [{"backend": "motion_graphic"}, {"backend": "motion_graphic"}, {"backend": "motion_graphic"}],
        "quality_plan": {
            "generated_clip_count": 3,
            "unique_composition_count": 3,
            "composition_diversity_ratio": 1.0,
            "actual_visual_variety": {"status": "pass"},
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
            VALUES (?, 'acct-quality', 'Quality test topic', 'Opis', 'done', 90, ?, ?)
            """,
            (f"topic-{job_id}", now, now),
        )
        db.execute(
            """
            INSERT INTO pipeline_jobs (
                id, account_id, topic_id, status, current_step, artifact_dir, qa_report_path,
                upload_package_path, audio_timestamps, hooks, script, asset_plan, video_manifest,
                qa_report, thumbnail_path, created_at, updated_at
            )
            VALUES (?, 'acct-quality', ?, 'qa_passed', 'qa', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                f"topic-{job_id}",
                str(artifact_dir),
                str(qa_report_path),
                str(upload_package_path),
                json.dumps(
                    {
                        "quality_tier": "premium_cloud",
                        "voice_backend_grade": "premium",
                        "voice_profile": {"language_code": "en"},
                    }
                ),
                json.dumps(style_guide),
                json.dumps(style_guide),
                json.dumps(style_guide),
                json.dumps(video_manifest),
                json.dumps(qa_report),
                str(thumbnail_path),
                now,
                now,
            ),
        )
        db.commit()


def _valid_qa_report() -> dict:
    required_checks = []
    for name in REQUIRED_QA_CHECK_NAMES:
        value: object = "test"
        if name == "Visual variety":
            value = {"actual_visual_variety": {"status": "pass"}}
        required_checks.append(
            {
                "name": name,
                "status": "pass",
                "value": value,
                "detail": "Seeded QA check.",
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
