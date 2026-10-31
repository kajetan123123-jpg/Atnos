from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from unittest.mock import patch

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.intelligence.storage import connect, init_intelligence_db, json_dumps
from atnos.services.google_oauth_client_secret_importer import diagnose_google_oauth_client_secret_file
from atnos.services.account_credentials_service import AccountCredentialsService
from atnos.services.shorts_onboarding_service import ShortsOnboardingService
from atnos.services.youtube_connector_service import YouTubeConnectionStatus
from test_support import bootstrap_test_app, create_test_app


def _ready_readiness() -> dict:
    return {
        "items": [
            {"key": "production_modules", "status": "ready", "evidence": {}},
            {"key": "script_generation_contract", "status": "ready", "evidence": {}},
            {"key": "asset_planner_contract", "status": "ready", "evidence": {}},
            {"key": "qa_contract", "status": "ready", "evidence": {}},
            {"key": "ollama", "status": "ready", "evidence": {}},
            {"key": "audio_stack", "status": "ready", "evidence": {}},
            {"key": "ffmpeg", "status": "ready", "evidence": {}},
            {"key": "whisper_srt", "status": "ready", "evidence": {}},
            {"key": "comfyui", "status": "ready", "evidence": {}},
            {"key": "models", "status": "ready", "evidence": {}},
            {"key": "workflow_templates", "status": "ready", "evidence": {}},
            {"key": "analytics_score_loop", "status": "ready", "evidence": {}},
            {
                "key": "obsidian_memory",
                "status": "ready",
                "evidence": {
                    "style_guide_count": 1,
                    "style_guides": ["Atnos/Shorts/acct-1/style_guide.md"],
                },
            },
            {"key": "youtube_oauth", "status": "missing", "evidence": {}},
        ]
    }


def test_shorts_onboarding_endpoint_reports_required_user_inputs(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    asyncio.run(init_intelligence_db(app.state.settings))

    with TestClient(app) as client:
        response = client.get("/api/production/onboarding")

    assert response.status_code == 200
    payload = response.json()
    by_key = {item["key"]: item for item in payload["items"]}

    assert payload["ready_for_local_production"] is False
    assert payload["ready_for_private_upload"] is False
    assert payload["ready_for_revenue_ops"] is False
    assert "google_oauth" in by_key
    assert "youtube_data_api_key" in by_key
    assert "channel_strategy" in by_key
    assert "reference_sources" in by_key
    assert "monetization_plan" in by_key
    assert by_key["google_oauth"]["status"] == "missing"
    assert by_key["google_oauth"]["evidence"]["password_login_supported"] is False
    assert by_key["google_oauth"]["evidence"]["required_authorization_method"] == "Google OAuth consent with YouTube scopes"
    assert by_key["google_oauth"]["evidence"]["required_google_credential_type"] == (
        "OAuth 2.0 Client ID: Desktop app / installed application JSON"
    )
    assert by_key["google_oauth"]["evidence"]["google_cloud_credentials_url"] == (
        "https://console.cloud.google.com/apis/credentials"
    )
    assert by_key["google_oauth"]["evidence"]["youtube_data_api_url"] == (
        "https://console.cloud.google.com/apis/library/youtube.googleapis.com"
    )
    assert by_key["google_oauth"]["evidence"]["youtube_analytics_api_url"] == (
        "https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com"
    )
    assert by_key["youtube_data_api_key"]["status"] == "missing"
    assert by_key["youtube_data_api_key"]["evidence"]["yt_signals"] == 0
    assert by_key["youtube_data_api_key"]["evidence"]["account_seed_endpoint"] == "POST /accounts"
    assert "intelligence_niches" in by_key["youtube_data_api_key"]["evidence"]["account_seed_fields"]
    assert by_key["youtube_data_api_key"]["evidence"]["autonomous_config_endpoint"] == "POST /api/intelligence/accounts"
    assert by_key["youtube_data_api_key"]["evidence"]["manual_collection_endpoint"] == "POST /api/intelligence/run"
    assert by_key["publication_policy"]["status"] == "ready"
    assert any("client_secret.json" in item for item in payload["minimal_user_inputs"])
    assert any("Login i haslo" in item for item in payload["minimal_user_inputs"])
    assert any("YOUTUBE_DATA_API_KEY" in item for item in payload["minimal_user_inputs"])
    assert any("Model zarabiania" in item for item in payload["minimal_user_inputs"])


def test_shorts_onboarding_marks_local_production_ready_with_signals_and_style(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    bootstrap_test_app(app)
    settings = app.state.settings
    settings.youtube_data_api_key = "test-youtube-data-key"
    settings.obsidian_vault_path = tmp_path / "vault"
    settings.obsidian_vault_path.mkdir()
    settings.obsidian_export_enabled = True
    settings.atnos_write_obsidian_logs = True
    style_path = settings.obsidian_vault_path / "Atnos" / "Shorts" / "acct-1" / "style_guide.md"
    style_path.parent.mkdir(parents=True)
    style_path.write_text("Premium finance, no invented earnings.", encoding="utf-8")
    asyncio.run(init_intelligence_db(settings))
    now = datetime.now(UTC).isoformat()
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO yt_signals (
                video_id, account_id, niche, region, title, view_count, like_count,
                comment_count, published_at, channel_id, channel_title, thumbnail_url, collected_at
            )
            VALUES (
                'yt-1', 'acct-1', 'finanse osobiste', 'PL', 'inflacja oszczednosci',
                100000, 1000, 50, ?, 'UC1', 'Finance PL', 'https://img.test/1.jpg', ?
            )
            """,
            (now, now),
        )
        db.execute(
            """
            INSERT INTO gtrends_signals (
                keyword, timeframe, geo, interest_score, related_rising, related_topics, status, collected_at
            )
            VALUES ('inflacja oszczednosci', 'now 7-d', 'PL', 82, ?, '[]', 'ready', ?)
            """,
            (json_dumps(["lokaty", "konto oszczednosciowe"]), now),
        )
        db.execute(
            """
            INSERT INTO competitor_channels (
                account_id, channel_id, source_url, channel_title, avg_views_7d,
                posting_frequency_per_week, top_format, collected_at, last_refreshed
            )
            VALUES ('acct-1', 'UCCOMP', 'https://youtube.com/@competitor', 'Competitor', 50000, 5, 'shorts', ?, ?)
            """,
            (now, now),
        )
        db.commit()

    fake_youtube = YouTubeConnectionStatus(
        connector_key="youtube-studio",
        display_name="YouTube Studio",
        configured=False,
        connected=False,
        ready=False,
        warnings=(),
        account_id=None,
    )

    with patch(
        "atnos.services.shorts_onboarding_service.YouTubeConnectorService.get_status",
        return_value=fake_youtube,
    ), patch(
        "atnos.services.shorts_onboarding_service.ShortProductionReadinessService.check",
        return_value=_ready_readiness(),
    ):
        with app.state.database.session_scope() as session:
            AccountCredentialsService(session, settings).create_account(
                label="YT Finanse PL",
                provider_key="youtube-studio",
                account_identifier="acct-1",
                secret_type="oauth_token_later",
                secret_value=None,
                risk_level="medium",
                allowed_capabilities=["read_metadata", "publish_later"],
                linked_integration_key=None,
                linked_project_id=None,
                notes=None,
            )

            checklist = asyncio.run(ShortsOnboardingService(session, settings).build_checklist())

    payload = checklist.to_dict()
    by_key = {item["key"]: item for item in payload["items"]}
    assert payload["ready_for_local_production"] is True
    assert payload["ready_for_private_upload"] is False
    assert by_key["youtube_data_api_key"]["status"] == "ready"
    assert by_key["channel_strategy"]["status"] == "ready"
    assert by_key["reference_sources"]["status"] == "ready"
    assert by_key["google_oauth"]["status"] == "missing"
    assert by_key["youtube_private_upload"]["status"] == "missing"
    assert "youtube_private_upload" in payload["blocking_keys"]


def test_shorts_onboarding_blocks_local_production_when_models_are_missing(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    bootstrap_test_app(app)
    settings = app.state.settings
    settings.youtube_data_api_key = "test-youtube-data-key"
    settings.obsidian_vault_path = tmp_path / "vault"
    settings.obsidian_vault_path.mkdir()
    settings.obsidian_export_enabled = True
    style_path = settings.obsidian_vault_path / "Atnos" / "Shorts" / "acct-1" / "style_guide.md"
    style_path.parent.mkdir(parents=True)
    style_path.write_text("Premium finance, no invented earnings.", encoding="utf-8")
    asyncio.run(init_intelligence_db(settings))
    now = datetime.now(UTC).isoformat()
    with connect(settings) as db:
        db.execute(
            """
            INSERT INTO yt_signals (
                video_id, account_id, niche, region, title, view_count, like_count,
                comment_count, published_at, channel_id, channel_title, thumbnail_url, collected_at
            )
            VALUES ('yt-models', 'acct-1', 'finanse', 'PL', 'inflacja',
                    100000, 1000, 50, ?, 'UC1', 'Finance PL', '', ?)
            """,
            (now, now),
        )
        db.execute(
            """
            INSERT INTO gtrends_signals (
                keyword, timeframe, geo, interest_score, related_rising, related_topics, status, collected_at
            )
            VALUES ('inflacja', 'now 7-d', 'PL', 82, '[]', '[]', 'ready', ?)
            """,
            (now,),
        )
        db.execute(
            """
            INSERT INTO competitor_channels (
                account_id, channel_id, source_url, channel_title, avg_views_7d,
                posting_frequency_per_week, top_format, collected_at, last_refreshed
            )
            VALUES ('acct-1', 'UCCOMP', 'https://youtube.com/@competitor', 'Competitor', 50000, 5, 'shorts', ?, ?)
            """,
            (now, now),
        )
        db.commit()

    readiness = _ready_readiness()
    for item in readiness["items"]:
        if item["key"] == "models":
            item["status"] = "missing"

    fake_youtube = YouTubeConnectionStatus(
        connector_key="youtube-studio",
        display_name="YouTube Studio",
        configured=False,
        connected=False,
        ready=False,
        warnings=(),
        account_id=None,
    )
    with patch(
        "atnos.services.shorts_onboarding_service.YouTubeConnectorService.get_status",
        return_value=fake_youtube,
    ), patch(
        "atnos.services.shorts_onboarding_service.ShortProductionReadinessService.check",
        return_value=readiness,
    ):
        with app.state.database.session_scope() as session:
            AccountCredentialsService(session, settings).create_account(
                label="YT Finanse PL",
                provider_key="youtube-studio",
                account_identifier="acct-1",
                secret_type="oauth_token_later",
                secret_value=None,
                risk_level="medium",
                allowed_capabilities=["read_metadata"],
                linked_integration_key=None,
                linked_project_id=None,
                notes=None,
            )
            checklist = asyncio.run(ShortsOnboardingService(session, settings).build_checklist())

    payload = checklist.to_dict()
    runtime = next(item for item in payload["items"] if item["key"] == "runtime_stack")
    assert payload["ready_for_local_production"] is False
    assert "runtime_stack" in payload["blocking_keys"]
    assert runtime["evidence"]["models"] == "missing"


def test_shorts_onboarding_accepts_learning_note_as_channel_strategy_memory(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    service = ShortsOnboardingService(session=None, settings=app.state.settings)  # type: ignore[arg-type]

    item = service._channel_strategy_item(
        {
            "evidence": {
                "style_guide_count": 0,
                "style_guides": [],
                "learning_note_count": 1,
                "learning_notes": ["Atnos/Shorts/channel-learning-acct-1.md"],
            }
        },
        {"youtube-studio": 1},
    )

    assert item.status == "ready"
    assert item.evidence["style_guide_count"] == 0
    assert item.evidence["learning_note_count"] == 1
    assert item.evidence["learning_notes"] == ["Atnos/Shorts/channel-learning-acct-1.md"]


def test_shorts_onboarding_maps_youtube_oauth_to_private_upload_ready(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    asyncio.run(init_intelligence_db(app.state.settings))
    fake_youtube = YouTubeConnectionStatus(
        connector_key="youtube-studio",
        display_name="YouTube Studio",
        configured=True,
        connected=True,
        ready=True,
        warnings=(),
        account_id=7,
        channel_title="Atnos Finance",
        granted_scopes=(
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.readonly",
            "https://www.googleapis.com/auth/yt-analytics.readonly",
        ),
    )

    with patch(
        "atnos.services.shorts_onboarding_service.YouTubeConnectorService.get_status",
        return_value=fake_youtube,
    ), patch(
        "atnos.services.shorts_onboarding_service.ShortProductionReadinessService.check",
        return_value=_ready_readiness(),
    ):
        with TestClient(app) as client:
            response = client.get("/api/production/onboarding")

    assert response.status_code == 200
    payload = response.json()
    by_key = {item["key"]: item for item in payload["items"]}
    assert by_key["google_oauth"]["status"] == "ready"
    assert by_key["youtube_private_upload"]["status"] == "ready"
    assert by_key["youtube_private_upload"]["evidence"]["channel_title"] == "Atnos Finance"


def test_google_oauth_diagnostics_flags_documentation_example_client_secret(tmp_path: Path) -> None:
    client_secret_path = tmp_path / "client_secret.json"
    client_secret_path.write_text(
        json.dumps(
            {
                "web": {
                    "client_id": "asdfjasdljfasdkjf",
                    "client_secret": "1912308409123890",
                    "redirect_uris": ["https://www.example.com/oauth2callback"],
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://accounts.google.com/o/oauth2/token",
                }
            }
        ),
        encoding="utf-8",
    )

    diagnostics = diagnose_google_oauth_client_secret_file(client_secret_path)

    assert diagnostics.import_ready is False
    assert diagnostics.likely_placeholder is True
    assert any("sample" in warning or "documentation" in warning for warning in diagnostics.warnings)


def test_shorts_onboarding_does_not_accept_placeholder_client_secret_as_ready(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    asyncio.run(init_intelligence_db(app.state.settings))
    fake_youtube = YouTubeConnectionStatus(
        connector_key="youtube-studio",
        display_name="YouTube Studio",
        configured=False,
        connected=False,
        ready=False,
        warnings=(),
        account_id=None,
    )
    readiness = _ready_readiness()
    readiness["items"][-1] = {
        "key": "youtube_oauth",
        "status": "missing",
        "evidence": {
            "client_secret_file": str(tmp_path / "client_secret.json"),
            "client_secret_diagnostics": {
                "exists": True,
                "valid_json": True,
                "has_client_id": True,
                "has_client_secret": True,
                "import_ready": False,
                "likely_placeholder": True,
            },
        },
    }

    with patch(
        "atnos.services.shorts_onboarding_service.YouTubeConnectorService.get_status",
        return_value=fake_youtube,
    ), patch(
        "atnos.services.shorts_onboarding_service.ShortProductionReadinessService.check",
        return_value=readiness,
    ):
        with TestClient(app) as client:
            response = client.get("/api/production/onboarding")

    assert response.status_code == 200
    google_item = next(item for item in response.json()["items"] if item["key"] == "google_oauth")
    assert google_item["status"] == "missing"
    assert google_item["evidence"]["client_secret_import_ready"] is False
    assert google_item["evidence"]["client_secret_likely_placeholder"] is True
    assert google_item["evidence"]["client_secret_status_endpoint"] == "GET /oauth/youtube-studio/client-secret-status"
    assert google_item["evidence"]["connect_endpoint"] == "POST /oauth/youtube-studio/connect"
