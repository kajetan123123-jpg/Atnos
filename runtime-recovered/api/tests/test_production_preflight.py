from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import sys

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.intelligence.storage import connect, init_intelligence_db, utc_now_iso
from test_support import create_test_app


@dataclass(frozen=True, slots=True)
class FakeChecklist:
    ready_for_local_production: bool = True
    ready_for_private_upload: bool = False
    ready_for_revenue_ops: bool = False
    runtime_status: str = "ready"
    runtime_evidence: dict | None = None

    def to_dict(self) -> dict:
        return {
            "ready_for_local_production": self.ready_for_local_production,
            "ready_for_private_upload": self.ready_for_private_upload,
            "ready_for_revenue_ops": self.ready_for_revenue_ops,
            "blocking_keys": [] if self.ready_for_local_production else ["runtime_stack"],
            "minimal_user_inputs": [] if self.ready_for_private_upload else ["Google OAuth client_secret.json"],
            "items": [
                {
                    "key": "runtime_stack",
                    "ready": self.ready_for_local_production,
                    "status": self.runtime_status,
                    "evidence": self.runtime_evidence or {"models": "ready"},
                    "next_steps": [] if self.ready_for_local_production else ["Pobierz modele shorts_full."],
                }
            ],
        }


def _seed_topic(settings, *, status: str = "approved", score: float = 88.0) -> None:
    now = utc_now_iso()
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO topic_queue (
                id, account_id, title, description, status, total_score,
                clickbait_risk, evergreen, shorts_potential, created_at, updated_at
            )
            VALUES ('topic-1', 'acct-1', 'Inflacja zjada oszczednosci', 'Finanse PL', ?, ?, 0, 1, 1, ?, ?)
            """,
            (status, score, now, now),
        )
        db.execute(
            """
            INSERT INTO yt_signals (
                video_id, account_id, niche, region, title, view_count, like_count,
                comment_count, published_at, channel_id, channel_title, thumbnail_url, collected_at
            )
            VALUES ('yt-preflight-1', 'acct-1', 'finanse', 'PL', 'inflacja', 100000, 1000, 40, ?, 'UC1', 'Finance', '', ?)
            """,
            (now, now),
        )
        db.execute(
            """
            INSERT INTO gtrends_signals (
                keyword, timeframe, geo, interest_score, related_rising, related_topics, status, collected_at
            )
            VALUES ('inflacja', 'now 7-d', 'PL', 80, '[]', '[]', 'ready', ?)
            """,
            (now,),
        )
        db.execute(
            """
            INSERT INTO gtrends_signals (
                keyword, timeframe, geo, interest_score, related_rising, related_topics, status, collected_at
            )
            VALUES ('makijaz wakacyjny', 'now 7-d', 'PL', 99, '[]', '[]', 'ready', ?)
            """,
            (now,),
        )
        db.execute(
            """
            INSERT INTO reference_channels (
                channel_id, source_url, channel_title, hook_patterns, avg_duration_sec,
                posting_cadence_per_week, avg_engagement_rate, collected_at, last_refreshed
            )
            VALUES ('UCREF-PREFLIGHT', 'https://youtube.com/@ref', 'Reference',
                    '["nie uwierzysz"]', 55, 4.0, 0.05, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET last_refreshed=excluded.last_refreshed
            """,
            (now, now),
        )
        db.commit()


def test_production_preflight_allows_local_job_without_private_upload_oauth(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_topic(settings)

    async def fake_checklist(self):
        return FakeChecklist()

    monkeypatch.setattr(
        "atnos.services.production_preflight_service.ShortsOnboardingService.build_checklist",
        fake_checklist,
    )

    with TestClient(app) as client:
        response = client.post("/api/production/preflight", json={"account_id": "acct-1", "topic_id": "topic-1"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ready_to_start_local_job"] is True
    assert payload["runtime"]["ready"] is True
    assert payload["runtime"]["blockers"] == []
    assert payload["ready_for_private_upload"] is False
    assert payload["blocking"] == []
    assert "private_upload_not_ready" in payload["warnings"]
    assert payload["topic"]["status"] == "approved"
    assert payload["topic"]["flags"]["shorts_potential"] is True
    assert payload["signal_counts"]["yt_signals"] == 1
    assert payload["signal_counts"]["gtrends_signals"] == 1
    assert payload["signal_counts"]["account_research_terms"] > 0
    assert payload["research"]["ready"] is True
    assert payload["research"]["missing"] == []
    assert payload["research"]["endpoints"]["youtube_setup_guide"] == "GET /oauth/youtube-studio/setup-guide"
    assert payload["research"]["endpoints"]["run_collectors"] == "POST /api/intelligence/run"
    assert "yt_signals" in payload["research"]["credential_purpose"]["youtube_data_api_key"]
    assert "Prywatny upload" in payload["research"]["credential_purpose"]["youtube_oauth"]
    assert payload["onboarding"]["minimal_user_inputs"] == ["Google OAuth client_secret.json"]
    actions = {item["action"]: item for item in payload["external_actions"]["items"]}
    assert actions["youtube_private_upload"]["ready"] is False
    assert "client_secret_diagnostics" in actions["youtube_private_upload"]["evidence"]
    assert actions["youtube_private_upload"]["evidence"]["client_secret_status_endpoint"] == "GET /oauth/youtube-studio/client-secret-status"
    assert actions["youtube_private_upload"]["evidence"]["oauth_readiness_endpoint"] == "GET /oauth/youtube-studio/status"
    assert actions["youtube_private_upload"]["evidence"]["connect_endpoint"] == "POST /oauth/youtube-studio/connect"
    assert actions["youtube_private_upload"]["evidence"]["oauth_start_endpoint"] == "POST /oauth/youtube-studio/start"
    assert actions["youtube_private_upload"]["evidence"]["import_client_secret_endpoint"] == "POST /oauth/youtube-studio/import-client-secret"
    assert payload["youtube_oauth"]["ready"] is False
    assert payload["youtube_oauth"]["evidence"]["oauth_readiness_endpoint"] == "GET /oauth/youtube-studio/status"
    assert isinstance(payload["youtube_oauth"]["evidence"]["client_secret_diagnostics"]["import_ready"], bool)
    assert payload["youtube_oauth"]["evidence"]["connected"] is False
    assert "https://www.googleapis.com/auth/youtube.upload" in payload["youtube_oauth"]["evidence"]["missing_scopes"]
    assert actions["gmail_send"]["ready"] is False
    assert actions["int_mail_smtp_send"]["ready"] is False


def test_production_preflight_reports_public_quality_voice_provider_warning(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_topic(settings)

    async def fake_checklist(self):
        return FakeChecklist()

    monkeypatch.setattr(
        "atnos.services.production_preflight_service.ShortsOnboardingService.build_checklist",
        fake_checklist,
    )

    with TestClient(app) as client:
        response = client.post("/api/production/preflight", json={"account_id": "acct-1", "topic_id": "topic-1"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ready_to_start_local_job"] is True
    assert payload["ready_for_public_quality"] is False
    assert "premium_voice_provider_not_ready" in payload["warnings"]
    assert "visual_backend_not_ready_for_public" in payload["warnings"]
    assert payload["public_quality_gate"]["ready"] is False
    assert set(payload["public_quality_gate"]["blockers"]) >= {
        "premium_voice_provider_not_ready",
        "visual_backend_not_ready_for_public",
    }
    assert payload["voice_provider"]["checked"] is False
    assert payload["voice_provider"]["next_action"] == "run_voice_provider_doctor"
    assert payload["voice_provider"]["public_voice_ready"] is False
    assert payload["visual_backend"]["checked"] is False
    assert payload["visual_backend"]["next_action"] == "run_visual_backend_doctor"
    assert payload["visual_backend"]["public_visual_ready"] is False
    assert payload["style_guide"]["used_for_account"] == "acct-1"


def test_production_preflight_surfaces_runtime_model_blockers(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_topic(settings)

    async def fake_checklist(self):
        return FakeChecklist(
            ready_for_local_production=False,
            runtime_status="needs_action",
            runtime_evidence={
                "ollama": "ready",
                "comfyui": "ready",
                "models": "missing",
                "ffmpeg": "ready",
            },
        )

    monkeypatch.setattr(
        "atnos.services.production_preflight_service.ShortsOnboardingService.build_checklist",
        fake_checklist,
    )

    with TestClient(app) as client:
        response = client.post("/api/production/preflight", json={"account_id": "acct-1", "topic_id": "topic-1"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ready_to_start_local_job"] is False
    assert "local_production_not_ready" in payload["blocking"]
    assert payload["runtime"]["ready"] is False
    assert payload["runtime"]["status"] == "needs_action"
    assert payload["runtime"]["blockers"] == ["models"]
    assert payload["runtime"]["evidence"]["models"] == "missing"
    assert payload["runtime"]["next_steps"][0].startswith("Uruchom POST /api/production/runtime/prepare")
    assert "Ollama, ComfyUI i modele" in payload["runtime"]["next_steps"][0]
    assert "Pobierz modele shorts_full." in payload["runtime"]["next_steps"]


def test_production_preflight_blocks_unapproved_topic_and_active_job(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    _seed_topic(settings, status="pending", score=32)
    now = utc_now_iso()
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO pipeline_jobs (
                id, account_id, topic_id, status, current_step, artifact_dir, created_at, updated_at
            )
            VALUES ('prod-active', 'acct-1', 'topic-1', 'running', 'script', ?, ?, ?)
            """,
            (str(tmp_path / "prod-active"), now, now),
        )
        db.commit()

    async def fake_checklist(self):
        return FakeChecklist()

    monkeypatch.setattr(
        "atnos.services.production_preflight_service.ShortsOnboardingService.build_checklist",
        fake_checklist,
    )

    with TestClient(app) as client:
        response = client.post("/api/production/preflight", json={"account_id": "acct-1", "topic_id": "topic-1"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ready_to_start_local_job"] is False
    assert set(payload["blocking"]) == {"topic_not_approved", "active_job_exists"}
    assert "topic_score_low" in payload["warnings"]
    assert payload["active_job"]["id"] == "prod-active"


def test_production_preflight_blocks_approved_topic_without_real_signals(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    settings = app.state.settings
    asyncio.run(init_intelligence_db(settings))
    now = utc_now_iso()
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO topic_queue (
                id, account_id, title, description, status, total_score,
                clickbait_risk, evergreen, shorts_potential, created_at, updated_at
            )
            VALUES ('topic-nosignals', 'acct-1', 'Ręcznie dodany temat', 'Bez sygnałów',
                    'approved', 90, 0, 1, 1, ?, ?)
            """,
            (now, now),
        )
        db.commit()

    async def fake_checklist(self):
        return FakeChecklist()

    monkeypatch.setattr(
        "atnos.services.production_preflight_service.ShortsOnboardingService.build_checklist",
        fake_checklist,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/production/preflight",
            json={"account_id": "acct-1", "topic_id": "topic-nosignals"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ready_to_start_local_job"] is False
    assert "real_signals_missing" in payload["blocking"]
    assert payload["signal_counts"]["yt_signals"] == 0
    assert payload["signal_counts"]["gtrends_signals"] == 0
    assert payload["research"]["ready"] is False
    assert "yt_signals" in payload["research"]["missing"]
    assert "gtrends_signals" in payload["research"]["missing"]
    assert payload["research"]["endpoints"]["configure_youtube_data_api_key"] == "POST /api/intelligence/youtube-data-api-key"
    assert payload["research"]["endpoints"]["youtube_setup_guide"] == "GET /oauth/youtube-studio/setup-guide"
    assert payload["research"]["endpoints"]["configure_account_intelligence"] == "POST /api/intelligence/accounts"
    assert payload["research"]["endpoints"]["run_collectors"] == "POST /api/intelligence/run"
    assert payload["research"]["endpoints"]["generate_topics"] == "POST /api/topics/generate"
    assert payload["research"]["credential_purpose"]["youtube_data_api_key"].startswith("Publiczne trendy YouTube")
    assert "follow-upy 24/48/72h" in payload["research"]["credential_purpose"]["youtube_oauth"]
    assert any("/oauth/youtube-studio/setup-guide" in step for step in payload["research"]["next_steps"])
    assert any("/api/intelligence/youtube-data-api-key" in step for step in payload["research"]["next_steps"])
    assert any("account_id=acct-1" in step for step in payload["research"]["next_steps"])
