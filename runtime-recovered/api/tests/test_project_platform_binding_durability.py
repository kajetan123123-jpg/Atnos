from __future__ import annotations

import json
from pathlib import Path
import sys

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.repositories.account_credential_repository import AccountCredentialRepository
from atnos.repositories.project_repository import ProjectRepository
from test_support import create_test_app


SECRET_SENTINEL = "vault://youtube/never-return-this-secret-reference"
RESOURCE_ID = "youtube:channel:UC_durable_123"


def _seed_project(app, name: str) -> int:
    with app.state.database.session_scope() as session:
        return ProjectRepository(session).create(name).id


def _seed_youtube_account(app) -> int:
    with app.state.database.session_scope() as session:
        account = AccountCredentialRepository(session).create(
            label="Durable YouTube account",
            provider_key="youtube-studio",
            account_identifier="youtube:account-b2096dbc@example.invalid",
            secret_type="oauth_token_reference",
            storage_backend="vault_reference",
            secret_ref=SECRET_SENTINEL,
            status="configured",
            risk_level="medium",
            allowed_capabilities_json=["read_metadata"],
            linked_integration_key="youtube",
            linked_project_id=None,
            notes=None,
            last_rotated_at=None,
            last_used_at=None,
        )
        return account.id


def _binding_payload(account_id: int, *, allow_shared_account: bool = False) -> dict[str, object]:
    return {
        "integration_key": "youtube",
        "account_id": account_id,
        "label": "Primary channel binding",
        "resource_type": "channel",
        "resource_external_id": RESOURCE_ID,
        "resource_label": "Original channel label",
        "environment": "production",
        "confirmation_policy": "always_confirm",
        "delegation_policy": "project_only",
        "allow_shared_account": allow_shared_account,
        "permission_profile": "read_only",
        "permission_scopes": ["https://www.googleapis.com/auth/youtube.readonly"],
        "allowed_capabilities": ["read_metadata"],
    }


def _assert_response_has_no_credentials(payload: object) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    assert SECRET_SENTINEL not in serialized
    assert "vault://" not in serialized
    for forbidden_key in (
        "secret_ref",
        "token_ref",
        "code_verifier_ref",
        "password",
        "api_key",
        "authorization",
    ):
        assert f'"{forbidden_key}"' not in serialized


def test_binding_persists_after_application_restart(tmp_path: Path) -> None:
    first_app = create_test_app(tmp_path)
    with TestClient(first_app) as client:
        project_id = _seed_project(first_app, "Persistent project")
        account_id = _seed_youtube_account(first_app)
        created = client.post(
            f"/projects/{project_id}/app-links",
            json=_binding_payload(account_id),
        )
        assert created.status_code == 201
        link_id = created.json()["id"]

    restarted_app = create_test_app(tmp_path)
    with TestClient(restarted_app) as restarted_client:
        response = restarted_client.get(f"/projects/{project_id}/app-links/{link_id}")

    assert response.status_code == 200
    persisted = response.json()
    assert persisted["project_id"] == project_id
    assert persisted["account_id"] == account_id
    assert persisted["resource_external_id"] == RESOURCE_ID
    assert persisted["permission_scopes"] == ["https://www.googleapis.com/auth/youtube.readonly"]


def test_shared_account_is_blocked_without_explicit_opt_in(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    with TestClient(app) as client:
        first_project_id = _seed_project(app, "First project")
        second_project_id = _seed_project(app, "Second project")
        account_id = _seed_youtube_account(app)

        first = client.post(
            f"/projects/{first_project_id}/app-links",
            json=_binding_payload(account_id),
        )
        blocked = client.post(
            f"/projects/{second_project_id}/app-links",
            json=_binding_payload(account_id),
        )

    assert first.status_code == 201
    assert blocked.status_code == 422
    assert "set allow_shared_account=true explicitly" in blocked.json()["detail"]


def test_shared_account_is_allowed_with_explicit_opt_in(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    with TestClient(app) as client:
        first_project_id = _seed_project(app, "First shared project")
        second_project_id = _seed_project(app, "Second shared project")
        account_id = _seed_youtube_account(app)

        first = client.post(
            f"/projects/{first_project_id}/app-links",
            json=_binding_payload(account_id),
        )
        shared = client.post(
            f"/projects/{second_project_id}/app-links",
            json=_binding_payload(account_id, allow_shared_account=True),
        )

    assert first.status_code == 201
    assert shared.status_code == 201
    assert shared.json()["allow_shared_account"] is True
    assert shared.json()["account_id"] == account_id


def test_resource_external_id_survives_display_label_changes(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    with TestClient(app) as client:
        project_id = _seed_project(app, "Renamed channel project")
        account_id = _seed_youtube_account(app)
        created = client.post(
            f"/projects/{project_id}/app-links",
            json=_binding_payload(account_id),
        )
        assert created.status_code == 201

        updated = client.patch(
            f"/projects/{project_id}/app-links/{created.json()['id']}",
            json={
                "label": "Renamed binding",
                "resource_label": "Renamed YouTube channel",
            },
        )

    assert updated.status_code == 200
    assert updated.json()["label"] == "Renamed binding"
    assert updated.json()["resource_label"] == "Renamed YouTube channel"
    assert updated.json()["resource_external_id"] == RESOURCE_ID


def test_verify_without_oauth_returns_explicit_status_and_no_secrets(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    with TestClient(app) as client:
        project_id = _seed_project(app, "Unconnected OAuth project")
        account_id = _seed_youtube_account(app)
        created = client.post(
            f"/projects/{project_id}/app-links",
            json=_binding_payload(account_id),
        )
        assert created.status_code == 201
        _assert_response_has_no_credentials(created.json())

        verified = client.post(
            f"/projects/{project_id}/app-links/{created.json()['id']}/verify"
        )

    assert verified.status_code == 200
    payload = verified.json()
    assert payload["connection_status"] == "not_connected"
    assert payload["connection_detail"] == f"No OAuth connection for youtube-studio and account {account_id}."
    assert payload["last_verified_at"] is not None
    assert payload["verification_evidence"]["secret_exposed"] is False
    _assert_response_has_no_credentials(payload)
