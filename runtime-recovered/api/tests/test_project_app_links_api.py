from __future__ import annotations

from pathlib import Path
import sys

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from atnos.repositories.account_credential_repository import AccountCredentialRepository
from atnos.repositories.creator_project_repository import CreatorProjectRepository
from atnos.repositories.project_repository import ProjectRepository
from test_support import create_test_app


def _seed_project_with_type(app, *, name: str, project_type: str) -> int:
    with app.state.database.session_scope() as session:
        project = ProjectRepository(session).create(name)
        CreatorProjectRepository(session).create_project(
            project_id=project.id,
            name=name,
            project_type=project_type,
            status="active",
            persona_type="none",
            disclosure_policy="not_required",
            niche=None,
            target_audience=None,
            monetization_goal=None,
            content_boundaries=None,
            safety_boundaries="No irreversible action without approval.",
            platforms_json=[],
            linked_accounts_json=[],
            linked_integrations_json=[],
            operating_mode="planning_only",
            approval_policy="always_require_approval",
            notes=None,
        )
        return project.id


def _seed_account(app, *, provider: str, integration: str) -> int:
    with app.state.database.session_scope() as session:
        account = AccountCredentialRepository(session).create(
            label=f"{provider} test account",
            provider_key=provider,
            account_identifier=f"{provider}@example.test",
            secret_type="oauth_token_later",
            storage_backend="metadata_only",
            secret_ref=None,
            status="configured",
            risk_level="low",
            allowed_capabilities_json=["read_metadata", "financial_read_later"],
            linked_integration_key=integration,
            linked_project_id=None,
            notes=None,
            last_rotated_at=None,
            last_used_at=None,
        )
        return account.id


def _link_payload(*, integration_key: str, account_id: int, label: str) -> dict[str, object]:
    return {
        "integration_key": integration_key,
        "account_id": account_id,
        "label": label,
        "permission_profile": "read_only",
        "allowed_capabilities": ["read_metadata"],
    }


def test_project_links_persist_a_matching_platform_account(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    with TestClient(app) as client:
        project_id = _seed_project_with_type(app, name="English Shorts", project_type="shorts_channel")
        account_id = _seed_account(app, provider="youtube-studio", integration="youtube")

        created = client.post(
            f"/projects/{project_id}/app-links",
            json=_link_payload(integration_key="youtube", account_id=account_id, label="Channel OAuth"),
        )

        assert created.status_code == 201
        assert created.json()["account_id"] == account_id
        assert created.json()["integration_key"] == "youtube"

        persisted = client.get(f"/projects/{project_id}/app-links")
        assert persisted.status_code == 200
        assert persisted.json()[0]["account_id"] == account_id
        assert persisted.json()[0]["integration_key"] == "youtube"


def test_market_project_rejects_youtube_and_content_project_rejects_broker(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    with TestClient(app) as client:
        market_project_id = _seed_project_with_type(app, name="Paper Trading", project_type="market_lab")
        content_project_id = _seed_project_with_type(app, name="History Shorts", project_type="shorts_channel")
        youtube_account_id = _seed_account(app, provider="youtube-studio", integration="youtube")
        broker_account_id = _seed_account(app, provider="alpaca", integration="broker-generic")

        youtube_in_market = client.post(
            f"/projects/{market_project_id}/app-links",
            json=_link_payload(integration_key="youtube", account_id=youtube_account_id, label="Wrong channel"),
        )
        assert youtube_in_market.status_code == 422
        assert "Market Lab projects cannot link content integration" in youtube_in_market.json()["detail"]

        broker_in_content = client.post(
            f"/projects/{content_project_id}/app-links",
            json=_link_payload(integration_key="broker-generic", account_id=broker_account_id, label="Wrong broker"),
        )
        assert broker_in_content.status_code == 422
        assert "only to Market Lab projects" in broker_in_content.json()["detail"]


def test_project_link_rejects_account_bound_to_another_integration(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    with TestClient(app) as client:
        project_id = _seed_project_with_type(app, name="Creator", project_type="youtube_channel")
        gmail_account_id = _seed_account(app, provider="gmail", integration="gmail")

        response = client.post(
            f"/projects/{project_id}/app-links",
            json=_link_payload(integration_key="youtube", account_id=gmail_account_id, label="Wrong account"),
        )

        assert response.status_code == 422
        assert "belongs to integration 'gmail', not 'youtube'" in response.json()["detail"]


def test_legacy_platform_binding_migration_is_idempotent_and_never_copies_secrets(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    with TestClient(app) as client:
        with app.state.database.session_scope() as session:
            projects = ProjectRepository(session)
            project = projects.create("Migrated channel")
            accounts = AccountCredentialRepository(session)
            youtube = accounts.create(
                label="YouTube historical account",
                provider_key="youtube-studio",
                account_identifier="youtube:channel-123",
                secret_type="oauth_token_later",
                storage_backend="vault_reference",
                secret_ref="vault://youtube/channel-123",
                status="configured",
                risk_level="medium",
                allowed_capabilities_json=["read_metadata", "read_content", "draft_write", "publish_later"],
                linked_integration_key="youtube",
                linked_project_id=project.id,
                notes=None,
                last_rotated_at=None,
                last_used_at=None,
            )
            unknown = accounts.create(
                label="Legacy account without integration proof",
                provider_key="legacy",
                account_identifier="legacy:unknown",
                secret_type="none",
                storage_backend="metadata_only",
                secret_ref=None,
                status="configured",
                risk_level="low",
                allowed_capabilities_json=["read_metadata"],
                linked_integration_key=None,
                linked_project_id=project.id,
                notes=None,
                last_rotated_at=None,
                last_used_at=None,
            )
            CreatorProjectRepository(session).create_project(
                project_id=project.id,
                name="Migrated channel",
                project_type="shorts_channel",
                status="active",
                persona_type="none",
                disclosure_policy="not_required",
                niche=None,
                target_audience=None,
                monetization_goal=None,
                content_boundaries=None,
                safety_boundaries="No irreversible action without approval.",
                platforms_json=["youtube"],
                # Historical data may keep the canonical relation only on the
                # account record. The migrator must recover that link without
                # relying on a copied JSON list.
                linked_accounts_json=[],
                linked_integrations_json=["youtube"],
                operating_mode="planning_only",
                approval_policy="always_require_approval",
                notes=None,
            )

        dry_run = client.post("/projects/platform-bindings/migrate-legacy", json={"apply": False})
        assert dry_run.status_code == 200
        dry_payload = dry_run.json()
        assert dry_payload["mode"] == "dry_run"
        assert dry_payload["mutates_credentials"] is False
        assert dry_payload["summary"]["planned"] == 1
        assert dry_payload["summary"]["skipped"] == 1
        assert client.get(f"/projects/{project.id}/app-links").json() == []

        applied = client.post("/projects/platform-bindings/migrate-legacy", json={"apply": True})
        assert applied.status_code == 200
        applied_payload = applied.json()
        created = [item for item in applied_payload["items"] if item["status"] == "created"]
        assert len(created) == 1
        assert created[0]["account_id"] == youtube.id
        assert all("vault://" not in str(item) for item in applied_payload["items"])

        persisted = client.get(f"/projects/{project.id}/app-links")
        assert persisted.status_code == 200
        assert len(persisted.json()) == 1
        assert persisted.json()[0]["account_id"] == youtube.id
        assert persisted.json()[0]["integration_key"] == "youtube"

        repeated = client.post("/projects/platform-bindings/migrate-legacy", json={"apply": True})
        assert repeated.status_code == 200
        assert repeated.json()["summary"]["created"] == 0
        assert repeated.json()["summary"]["existing"] == 1


def test_legacy_migration_recovers_a_unique_historical_project_account(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    with TestClient(app) as client:
        with app.state.database.session_scope() as session:
            project = ProjectRepository(session).create("Pre-creator historical channel")
            account = AccountCredentialRepository(session).create(
                label="Historical YouTube account",
                provider_key="youtube-studio",
                account_identifier="youtube:historical-channel",
                secret_type="oauth_token_later",
                storage_backend="vault_reference",
                secret_ref="vault://youtube/historical-channel",
                status="configured",
                risk_level="medium",
                allowed_capabilities_json=["read_metadata", "read_content", "draft_write"],
                linked_integration_key="youtube",
                linked_project_id=project.id,
                notes=None,
                last_rotated_at=None,
                last_used_at=None,
            )

        dry_run = client.post(
            "/projects/platform-bindings/migrate-legacy",
            json={"apply": False, "project_ids": [project.id]},
        )
        assert dry_run.status_code == 200
        assert dry_run.json()["summary"]["historical_projects_considered"] == 1
        planned = [item for item in dry_run.json()["items"] if item["status"] == "planned"]
        assert len(planned) == 1
        assert planned[0]["creator_project_id"] is None
        assert planned[0]["account_id"] == account.id

        applied = client.post(
            "/projects/platform-bindings/migrate-legacy",
            json={"apply": True, "project_ids": [project.id]},
        )
        assert applied.status_code == 200
        created = [item for item in applied.json()["items"] if item["status"] == "created"]
        assert len(created) == 1

        link = client.get(f"/projects/{project.id}/app-links").json()[0]
        assert link["account_id"] == account.id
        assert link["integration_key"] == "youtube"
        # A generic legacy project has no typed channel resource yet, therefore
        # it is deliberately reviewable instead of pretending to be live.
        assert link["status"] == "needs_review"
        assert link["is_default_for_content"] is False


def test_legacy_migration_refuses_ambiguous_historical_accounts(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    with TestClient(app) as client:
        with app.state.database.session_scope() as session:
            project = ProjectRepository(session).create("Ambiguous historical channel")
            accounts = AccountCredentialRepository(session)
            for index in (1, 2):
                accounts.create(
                    label=f"Ambiguous YouTube account {index}",
                    provider_key="youtube-studio",
                    account_identifier=f"youtube:ambiguous-{index}",
                    secret_type="oauth_token_later",
                    storage_backend="vault_reference",
                    secret_ref=f"vault://youtube/ambiguous-{index}",
                    status="configured",
                    risk_level="medium",
                    allowed_capabilities_json=["read_metadata"],
                    linked_integration_key="youtube",
                    linked_project_id=project.id,
                    notes=None,
                    last_rotated_at=None,
                    last_used_at=None,
                )

        response = client.post(
            "/projects/platform-bindings/migrate-legacy",
            json={"apply": True, "project_ids": [project.id]},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["summary"]["created"] == 0
        assert payload["summary"]["skipped"] == 2
        assert {
            item["reason"] for item in payload["items"]
        } == {"ambiguous_multiple_accounts_for_integration"}
        assert client.get(f"/projects/{project.id}/app-links").json() == []


def test_legacy_migration_prefers_one_configured_account_over_metadata_only_duplicate(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    with TestClient(app) as client:
        with app.state.database.session_scope() as session:
            project = ProjectRepository(session).create("Recovered historical channel")
            accounts = AccountCredentialRepository(session)
            metadata_only = accounts.create(
                label="Old metadata account",
                provider_key="youtube-studio",
                account_identifier="youtube:old-metadata",
                secret_type="none",
                storage_backend="metadata_only",
                secret_ref=None,
                status="metadata_only",
                risk_level="low",
                allowed_capabilities_json=["read_metadata"],
                linked_integration_key="youtube",
                linked_project_id=project.id,
                notes=None,
                last_rotated_at=None,
                last_used_at=None,
            )
            configured = accounts.create(
                label="Configured YouTube account",
                provider_key="youtube-studio",
                account_identifier="youtube:configured",
                secret_type="oauth_token_later",
                storage_backend="vault_reference",
                secret_ref="vault://youtube/configured",
                status="configured",
                risk_level="medium",
                allowed_capabilities_json=["read_metadata", "read_content"],
                linked_integration_key="youtube",
                linked_project_id=project.id,
                notes=None,
                last_rotated_at=None,
                last_used_at=None,
            )

        response = client.post(
            "/projects/platform-bindings/migrate-legacy",
            json={"apply": True, "project_ids": [project.id]},
        )
        assert response.status_code == 200
        items = response.json()["items"]
        created = [item for item in items if item["status"] == "created"]
        skipped = [item for item in items if item["status"] == "skipped"]
        assert [item["account_id"] for item in created] == [configured.id]
        assert [item["account_id"] for item in skipped] == [metadata_only.id]
        assert skipped[0]["reason"] == "metadata_only_duplicate_preserved"
