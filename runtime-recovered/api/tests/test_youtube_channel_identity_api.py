from __future__ import annotations

from pathlib import Path
import sys

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.db.models.oauth_connection import OAuthConnection  # noqa: E402
from atnos.repositories.project_repository import ProjectRepository  # noqa: E402
from test_support import create_test_app  # noqa: E402


def _create_project(app, name: str = "Wealth Blueprint") -> int:
    with app.state.database.session_scope() as session:
        return ProjectRepository(session).create(name, description="English personal finance Shorts.").id


def _manifest() -> dict[str, object]:
    return {
        "mission": "Help viewers make calmer money decisions.",
        "target_audience": "English-speaking adults building financial habits.",
        "viewer_promise": "One useful, source-backed idea per Short.",
        "primary_topics": ["budgeting", "saving"],
        "forbidden_topics": ["stock picks"],
        "tone": "calm and precise",
        "narration_style": "documentary educator",
        "pace": "one visual change per claim",
        "hook_structure": "concrete consequence first",
        "editing_style": "adult fintech documentary",
        "subtitle_style": "three to five words in safe zone",
        "color_palette": ["charcoal", "emerald"],
        "typography": "Montserrat ExtraBold",
        "music_style": "restrained instrumental",
        "voice_character": "calm expert",
        "duration_range_seconds": [35, 50],
        "thumbnail_rules": "One concrete money object; no fake UI.",
        "title_rules": "Accurate and specific; no bait.",
        "publishing_cadence": "Two per week.",
        "recognizable_elements": ["emerald accent", "source card", "receipt motif"],
    }


def _policy(channel_id: str) -> dict[str, object]:
    return {
        "allowed_channel_ids": [channel_id],
        "allowed_days": ["monday", "thursday"],
        "allowed_hours_local": ["17:00-20:00"],
        "max_publications_per_day": 1,
        "minimum_quality_score": 85,
        "visibility_mode": "private",
        "require_private_first": True,
        "require_quality_gate": True,
        "require_render_validation": True,
        "notify_owner": True,
        "stop_conditions": ["P0 quality finding", "OAuth failure"],
    }


def _identity(channel_id: str = "UCWealthBlueprintTest1") -> dict[str, object]:
    return {
        "account_binding_key": "youtube:atnos@example.com",
        "youtube_channel_id": channel_id,
        "channel_handle": "@WealthBlueprintTest",
        "channel_url": "https://www.youtube.com/@WealthBlueprintTest",
        "display_name": "Wealth Blueprint",
        "owner_label": "Atnos",
        "language_code": "en-US",
        "target_market": "US/global",
        "niche": "Personal finance education",
    }


def _oauth_connection(app) -> int:
    with app.state.database.session_scope() as session:
        connection = OAuthConnection(
            connector_key="youtube-studio",
            display_name="YouTube Studio",
            status="connected",
            scopes_json=["https://www.googleapis.com/auth/youtube.readonly"],
            metadata_json={},
            audit_log_json=[],
        )
        session.add(connection)
        session.flush()
        return connection.id


def test_identity_requires_canonical_channel_id_before_any_publish_path(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    with TestClient(app) as client:
        project_id = _create_project(app)
        empty = client.get(f"/api/youtube-channel-identities/projects/{project_id}")
        assert empty.status_code == 200
        assert empty.json()["status"] == "not_started"

        response = client.put(
            f"/api/youtube-channel-identities/projects/{project_id}/identity",
            json={**_identity(), "youtube_channel_id": None},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "insufficient_data"
        assert payload["publish_eligibility"]["eligible_by_local_policy"] is False
        assert any(reason["code"] == "channel_id_missing" for reason in payload["publish_eligibility"]["reasons"])


def test_approved_manifest_and_policy_enable_only_local_policy_eligibility(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    channel_id = "UCWealthBlueprintTest1"
    with TestClient(app) as client:
        project_id = _create_project(app)
        configured = client.put(
            f"/api/youtube-channel-identities/projects/{project_id}/identity",
            json=_identity(channel_id),
        )
        assert configured.status_code == 200
        assert configured.json()["status"] == "pending_confirmation"

        confirmation = client.post(
            f"/api/youtube-channel-identities/projects/{project_id}/identity/confirm",
            json={"confirmed_channel_id": channel_id},
        )
        assert confirmation.status_code == 200
        assert confirmation.json()["identity"]["owner_confirmed"] is True

        manifest = client.put(
            f"/api/youtube-channel-identities/projects/{project_id}/manifest",
            json={"manifest": _manifest()},
        )
        assert manifest.status_code == 200
        assert manifest.json()["manifest"]["status"] == "ready_for_approval"

        manifest_approval = client.post(f"/api/youtube-channel-identities/projects/{project_id}/manifest/approve")
        assert manifest_approval.status_code == 200
        assert manifest_approval.json()["manifest"]["status"] == "approved"

        policy = client.put(
            f"/api/youtube-channel-identities/projects/{project_id}/publishing-policy",
            json={"policy": _policy(channel_id)},
        )
        assert policy.status_code == 200
        policy_approval = client.post(f"/api/youtube-channel-identities/projects/{project_id}/publishing-policy/approve")
        assert policy_approval.status_code == 200
        payload = policy_approval.json()
        assert payload["publishing_policy"]["status"] == "approved"
        assert payload["publish_eligibility"]["status"] == "eligible_by_local_policy"
        assert payload["publish_eligibility"]["external_upload_checked"] is False


def test_policy_cannot_be_approved_for_another_channel(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    channel_id = "UCWealthBlueprintTest1"
    with TestClient(app) as client:
        project_id = _create_project(app)
        assert client.put(
            f"/api/youtube-channel-identities/projects/{project_id}/identity",
            json=_identity(channel_id),
        ).status_code == 200
        assert client.post(
            f"/api/youtube-channel-identities/projects/{project_id}/identity/confirm",
            json={"confirmed_channel_id": channel_id},
        ).status_code == 200
        assert client.put(
            f"/api/youtube-channel-identities/projects/{project_id}/manifest",
            json={"manifest": _manifest()},
        ).status_code == 200
        assert client.post(f"/api/youtube-channel-identities/projects/{project_id}/manifest/approve").status_code == 200
        assert client.put(
            f"/api/youtube-channel-identities/projects/{project_id}/publishing-policy",
            json={"policy": _policy("UCOtherChannelTest2")},
        ).status_code == 200

        response = client.post(f"/api/youtube-channel-identities/projects/{project_id}/publishing-policy/approve")
        assert response.status_code == 400
        assert "dokładny youtube_channel_id" in response.json()["detail"]


def test_live_verify_records_observed_channel_and_requires_owner_confirmation(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)

    class FakeYouTube:
        def __init__(self, session, settings, *, account_id=None) -> None:
            assert account_id is not None

        def get_owned_channel(self) -> dict[str, object]:
            return {
                "channel_id": "UCLiveVerifiedTest123",
                "title": "Live verified channel",
                "custom_url": "@LiveVerified",
                "channel_url": "https://www.youtube.com/channel/UCLiveVerifiedTest123",
                "source": "youtube.channels.list(mine=true)",
            }

    monkeypatch.setattr("atnos.services.youtube_channel_identity_service.YouTubeConnectorService", FakeYouTube)
    with TestClient(app) as client:
        project_id = _create_project(app)
        connection_id = _oauth_connection(app)
        assert client.put(
            f"/api/youtube-channel-identities/projects/{project_id}/identity",
            json={**_identity(), "youtube_channel_id": None, "oauth_connection_id": connection_id},
        ).status_code == 200

        response = client.post(f"/api/youtube-channel-identities/projects/{project_id}/identity/live-verify")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "live_verified_pending_confirmation"
        assert payload["identity"]["youtube_channel_id"] == "UCLiveVerifiedTest123"
        assert payload["identity"]["owner_confirmed"] is False
        live_probe = payload["identity"]["verification_evidence"]["live_probe"]
        assert live_probe["external_api_verified"] is True
        assert live_probe["source"] == "youtube.channels.list(mine=true)"


def test_binding_cannot_be_reused_by_a_second_project(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    with TestClient(app) as client:
        first = _create_project(app, "First channel")
        second = _create_project(app, "Second channel")
        assert client.put(
            f"/api/youtube-channel-identities/projects/{first}/identity",
            json=_identity("UCFirstChannelTest1"),
        ).status_code == 200

        response = client.put(
            f"/api/youtube-channel-identities/projects/{second}/identity",
            json=_identity("UCSecondChannelTest"),
        )
        assert response.status_code == 400
        assert "już przypisany" in response.json()["detail"]
