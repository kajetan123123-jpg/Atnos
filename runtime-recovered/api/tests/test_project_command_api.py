from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace
import json
from unittest.mock import patch

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.repositories.project_repository import ProjectRepository  # noqa: E402
from atnos.ai_workers.delegation_router import AiDelegationRouter  # noqa: E402
from atnos.services.account_action_broker_service import AccountActionBrokerService  # noqa: E402
from atnos.services.self_coding_draft_service import SelfCodingDraftService  # noqa: E402
from atnos.db.models.project_brain import ProjectBlocker, ProjectEvidence, ProjectFact, ProjectState  # noqa: E402
from atnos.services.project_readiness_service import ProjectReadinessService  # noqa: E402
from atnos.repositories.account_credential_repository import AccountCredentialRepository  # noqa: E402
from atnos.services.project_verification_service import ProjectVerificationResult, ProjectVerificationService  # noqa: E402
from atnos.services.runtime_health_service import RuntimeHealthService  # noqa: E402
from atnos.services.semantic_knowledge_index_service import SemanticKnowledgeIndexService  # noqa: E402
from test_support import create_test_app  # noqa: E402


def _runtime_ready(*_args, **_kwargs):  # noqa: ANN002, ANN003
    return {
        "status": "ready",
        "components": [
            {
                "key": "backend",
                "label": "FastAPI backend",
                "status": "ready",
                "detail": "Mocked live health check.",
                "next_action": "No action required.",
            }
        ],
        "summary": {"ready": 1, "degraded": 0, "missing": 0, "blocked": 0},
    }


def _readiness_ready(*_args, **_kwargs):  # noqa: ANN002, ANN003
    return {
        "generated_at": "2026-07-16T10:00:00Z",
        "projects": [
            {
                "project_id": "software:project-command",
                "name": "Project Command",
                "status": "ready",
                "blockers": [],
                "next_action": "Run the focused verification suite.",
                "operator_decision": {"state": "safe_local_verification"},
            }
        ],
    }


async def _ollama_not_ready(*_args, **_kwargs):  # noqa: ANN002, ANN003
    return SimpleNamespace(ready=False, status="missing", reason="test worker unavailable")


def _readiness_seeded_youtube_channel(*_args, **_kwargs):  # noqa: ANN002, ANN003
    return {
        "generated_at": "2026-07-16T10:00:00Z",
        "projects": [
            {
                "project_id": "youtube:account-c5034bf4@example.invalid",
                "name": "Wealth Blueprint",
                "status": "limited",
                "blockers": ["private_render_missing"],
                "next_action": "Produce one QA-passed private render.",
                "operator_decision": {"state": "private_render_required"},
            }
        ],
    }


def _create_project(app) -> int:
    with app.state.database.session_scope() as session:
        return ProjectRepository(session).create(
            "Project Command",
            description="Evidence-first command surface for Atnos.",
        ).id


def _seed_youtube_binding(app, client: TestClient, project_id: int, account_identifier: str) -> None:
    with app.state.database.session_scope() as session:
        account = AccountCredentialRepository(session).create(
            label="Bound YouTube account",
            provider_key="youtube-studio",
            account_identifier=account_identifier,
            secret_type="oauth_token_reference",
            storage_backend="vault_reference",
            secret_ref="vault://youtube/test-only-reference",
            status="configured",
            risk_level="medium",
            allowed_capabilities_json=["read_metadata"],
            linked_integration_key="youtube",
            linked_project_id=None,
            notes=None,
            last_rotated_at=None,
            last_used_at=None,
        )
        account_id = account.id

    linked = client.post(
        f"/projects/{project_id}/app-links",
        json={
            "integration_key": "youtube",
            "account_id": account_id,
            "label": "Primary YouTube binding",
            "resource_type": "account",
            "environment": "production",
            "permission_profile": "read_only",
            "permission_scopes": ["https://www.googleapis.com/auth/youtube.readonly"],
            "allowed_capabilities": ["read_metadata"],
        },
    )
    assert linked.status_code == 201


def test_command_status_returns_live_evidence_and_trace(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    monkeypatch.setattr(RuntimeHealthService, "snapshot", _runtime_ready)
    monkeypatch.setattr(ProjectReadinessService, "build", _readiness_ready)

    with TestClient(app) as client:
        project_id = _create_project(app)
        response = client.post(
            f"/api/project-command/{project_id}/execute",
            json={"content": "Jaki jest aktualny status projektu i pokaż dowody?"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "completed"
        assert payload["intent"] == "status"
        assert payload["trace_status"] == "completed"
        assert payload["verified"] is False
        assert payload["verification_status"] == "partially_verified"
        assert payload["trace_id"] > 0
        assert any(item["key"] == "runtime_live_check" and item["verified"] for item in payload["evidence"])
        assert "### Dowody" in payload["answer"]


def test_overview_persists_project_brain_from_real_sources(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    monkeypatch.setattr(RuntimeHealthService, "snapshot", _runtime_ready)
    monkeypatch.setattr(ProjectReadinessService, "build", _readiness_ready)

    with TestClient(app) as client:
        project_id = _create_project(app)
        response = client.get(f"/api/project-command/{project_id}/overview")

        assert response.status_code == 200
        payload = response.json()
        assert payload["brain"]["state"]["verified"] is True
        assert payload["brain"]["goals"][0]["source"] == "project_metadata"
        assert any(item["key"] == "runtime:backend" for item in payload["brain"]["facts"])
        assert any(item["key"] == "runtime_live_check" for item in payload["brain"]["evidence"])

    with app.state.database.session_scope() as session:
        assert session.scalar(ProjectState.__table__.select().where(ProjectState.project_id == project_id)) is not None
        assert session.scalar(ProjectFact.__table__.select().where(ProjectFact.project_id == project_id)) is not None
        assert session.scalar(ProjectEvidence.__table__.select().where(ProjectEvidence.project_id == project_id)) is not None


def test_project_command_uses_semantic_index_as_primary_knowledge_source(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.app_env = "development"
    monkeypatch.setattr(RuntimeHealthService, "snapshot", _runtime_ready)
    monkeypatch.setattr(ProjectReadinessService, "build", _readiness_ready)
    monkeypatch.setattr(
        SemanticKnowledgeIndexService,
        "lookup",
        lambda *_args, **_kwargs: [
            SimpleNamespace(
                title="Project operating note",
                path="obsidian/project-note.md",
                topic="runtime",
                score=0.91,
                semantic_score=0.88,
                lexical_score=0.22,
                text="The launcher persists a verified heartbeat after restart.",
                source_label="Configured Obsidian vault",
                source_url=None,
                source_updated_at="2026-08-03T12:00:00Z",
                source_type="obsidian_note",
                project_id=None,
                citation="obsidian/project-note.md:4-6",
                line_start=4,
                line_end=6,
                match_reasons=("semantic_similarity",),
            )
        ],
    )

    with TestClient(app) as client:
        project_id = _create_project(app)
        response = client.get(
            f"/api/project-command/{project_id}/overview",
            params={"knowledge_query": "Jak dziala trwaly heartbeat launchera?"},
        )

    assert response.status_code == 200
    knowledge = response.json()["knowledge"]
    assert knowledge["retrieval_mode"] == "semantic_hybrid"
    assert knowledge["semantic_status"] == "ready"
    assert knowledge["semantic_matches"][0]["citation"] == "obsidian/project-note.md:4-6"
    assert knowledge["semantic_matches"][0]["source_type"] == "obsidian_note"
    assert knowledge["semantic_matches"][0]["claim_type"] == "stored_fact"
    assert knowledge["semantic_matches"][0]["verified"] is False


def test_project_command_reports_semantic_failure_and_keeps_lexical_fallback(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.app_env = "development"
    monkeypatch.setattr(RuntimeHealthService, "snapshot", _runtime_ready)
    monkeypatch.setattr(ProjectReadinessService, "build", _readiness_ready)

    def _failed_lookup(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("embedding runtime unavailable")

    monkeypatch.setattr(SemanticKnowledgeIndexService, "lookup", _failed_lookup)

    with TestClient(app) as client:
        project_id = _create_project(app)
        response = client.get(f"/api/project-command/{project_id}/overview")

    assert response.status_code == 200
    knowledge = response.json()["knowledge"]
    assert knowledge["retrieval_mode"] == "lexical_fallback"
    assert knowledge["semantic_status"] == "blocked_technical"
    assert "embedding runtime unavailable" in knowledge["semantic_error"]


def test_new_live_blocker_updates_persisted_project_brain(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    monkeypatch.setattr(ProjectReadinessService, "build", _readiness_ready)

    def _blocked_runtime(*_args, **_kwargs):  # noqa: ANN002, ANN003
        return {
            "status": "blocked",
            "components": [
                {
                    "key": "ollama",
                    "label": "Ollama",
                    "status": "blocked",
                    "detail": "Live endpoint did not respond.",
                    "next_action": "Start Ollama and repeat the health check.",
                }
            ],
            "summary": {"ready": 0, "degraded": 0, "missing": 0, "blocked": 1},
        }

    monkeypatch.setattr(RuntimeHealthService, "snapshot", _blocked_runtime)
    with TestClient(app) as client:
        project_id = _create_project(app)
        response = client.get(f"/api/project-command/{project_id}/overview")

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "blocked"
        assert payload["brain"]["blockers"][0]["source"] == "runtime_health_service"
        assert payload["brain"]["blockers"][0]["verified"] is True

    with app.state.database.session_scope() as session:
        blocker = session.query(ProjectBlocker).filter_by(project_id=project_id).one()
        assert blocker.source_key == "runtime_health_service:ollama"
        assert blocker.status == "open"
        assert blocker.verified is True


def test_unknown_command_returns_insufficient_data_without_llm(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    monkeypatch.setattr(RuntimeHealthService, "snapshot", _runtime_ready)
    monkeypatch.setattr(ProjectReadinessService, "build", _readiness_ready)

    with TestClient(app) as client:
        project_id = _create_project(app)
        response = client.post(
            f"/api/project-command/{project_id}/execute",
            json={"content": "Jaka będzie dokładna wycena projektu za rok?"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "insufficient_data"
        assert payload["verified"] is False
        assert payload["intent"] == "insufficient_data"
        assert payload["trace_status"] == "skipped"
        assert "Brak wystarczających danych do potwierdzenia." in payload["answer"]


def test_blockers_question_with_an_adverb_is_classified_and_rendered_for_an_operator(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    monkeypatch.setattr(RuntimeHealthService, "snapshot", _runtime_ready)
    monkeypatch.setattr(ProjectReadinessService, "build", _readiness_ready)

    with TestClient(app) as client:
        project_id = _create_project(app)
        response = client.post(
            f"/api/project-command/{project_id}/execute",
            json={"content": "Co obecnie blokuje projekt?"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["intent"] == "blockers"
        assert payload["status"] == "completed"
        assert "- Potwierdzone blokery: brak" in payload["answer"]
        assert "\n- Potwierdzone blokery: brak" in payload["answer"]
        assert "{'project_status'" not in payload["answer"]


def test_project_uses_persisted_platform_binding_for_readiness_account(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    monkeypatch.setattr(RuntimeHealthService, "snapshot", _runtime_ready)
    monkeypatch.setattr(ProjectReadinessService, "build", _readiness_seeded_youtube_channel)

    with TestClient(app) as client:
        with app.state.database.session_scope() as session:
            project_id = ProjectRepository(session).create("Atnos YT 01 - Finanse osobiste").id
        _seed_youtube_binding(app, client, project_id, "youtube:account-c5034bf4@example.invalid")

        response = client.get(f"/api/project-command/{project_id}/overview")

        assert response.status_code == 200
        readiness = response.json()["readiness"]
        assert readiness["project_id"] == "youtube:account-c5034bf4@example.invalid"
        assert readiness["binding"] == "persisted_project_platform_binding"
        assert readiness["status"] == "limited"


def test_project_name_does_not_implicitly_bind_a_historical_account(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    monkeypatch.setattr(RuntimeHealthService, "snapshot", _runtime_ready)
    monkeypatch.setattr(ProjectReadinessService, "build", _readiness_seeded_youtube_channel)

    with TestClient(app) as client:
        with app.state.database.session_scope() as session:
            project_id = ProjectRepository(session).create("Atnos YT 01 - Finanse osobiste").id

        response = client.get(f"/api/project-command/{project_id}/overview")

    assert response.status_code == 200
    assert response.json()["readiness"]["status"] == "unknown"


def test_youtube_channel_setup_blocker_precedes_render_readiness(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    monkeypatch.setattr(RuntimeHealthService, "snapshot", _runtime_ready)
    monkeypatch.setattr(ProjectReadinessService, "build", _readiness_seeded_youtube_channel)

    with TestClient(app) as client:
        with app.state.database.session_scope() as session:
            project_id = ProjectRepository(session).create("Atnos YT 01 - Finanse osobiste").id
        _seed_youtube_binding(app, client, project_id, "youtube:account-c5034bf4@example.invalid")

        response = client.get(f"/api/project-command/{project_id}/overview")

        assert response.status_code == 200
        payload = response.json()
        assert payload["next_step"]["source"] == "youtube_channel_identity_service"
        assert payload["next_step"]["reason"] == "Kanał YouTube nie jest gotowy do routingu uploadu"
        assert "Konfigurację kanału" in payload["next_step"]["action"]
        assert payload["blockers"][0]["key"] == "identity_missing"


def test_persisted_channel_identity_replaces_legacy_name_based_readiness_binding(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    monkeypatch.setattr(RuntimeHealthService, "snapshot", _runtime_ready)
    monkeypatch.setattr(ProjectReadinessService, "build", _readiness_seeded_youtube_channel)

    with TestClient(app) as client:
        with app.state.database.session_scope() as session:
            project_id = ProjectRepository(session).create("A custom content project").id

        configured = client.put(
            f"/api/youtube-channel-identities/projects/{project_id}/identity",
            json={
                "account_binding_key": "youtube:account-c5034bf4@example.invalid",
                "youtube_channel_id": "UCPersistedBindingTest1",
                "channel_handle": "@WealthBlueprint",
                "channel_url": "https://www.youtube.com/@WealthBlueprint",
                "language_code": "en-US",
            },
        )
        assert configured.status_code == 200

        response = client.get(f"/api/project-command/{project_id}/overview")

        assert response.status_code == 200
        payload = response.json()
        assert payload["readiness"]["project_id"] == "youtube:account-c5034bf4@example.invalid"
        assert payload["readiness"]["binding"] == "persisted_youtube_channel_identity"
        assert payload["channel_identity"]["identity"]["youtube_channel_id"] == "UCPersistedBindingTest1"


def test_delegation_creates_plan_but_not_false_completion(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    monkeypatch.setattr(RuntimeHealthService, "snapshot", _runtime_ready)
    monkeypatch.setattr(ProjectReadinessService, "build", _readiness_ready)
    monkeypatch.setattr(AiDelegationRouter, "_worker_status", _ollama_not_ready)

    with TestClient(app) as client:
        project_id = _create_project(app)
        response = client.post(
            f"/api/project-command/{project_id}/execute",
            json={"content": "Zleć Ollamie przegląd lokalnej dokumentacji projektu."},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "waiting_for_worker", payload
        assert payload["trace_status"] == "pending"
        delegation = payload["data"]["delegation"]
        assert delegation["status"] == "planned"
        assert delegation["verification_status"] == "planned"
        assert delegation["children"]
        assert "nie jest wynikiem pracy workera" in payload["answer"]


def test_safe_change_creates_work_item_and_engineering_plan(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    monkeypatch.setattr(RuntimeHealthService, "snapshot", _runtime_ready)
    monkeypatch.setattr(ProjectReadinessService, "build", _readiness_ready)
    monkeypatch.setattr(AiDelegationRouter, "_worker_status", _ollama_not_ready)

    with TestClient(app) as client:
        project_id = _create_project(app)
        response = client.post(
            f"/api/project-command/{project_id}/execute",
            json={"content": "Napraw nieblokujący błąd 404 i uruchom testy backendu."},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["intent"] == "safe_change"
        assert payload["status"] == "waiting_for_worker", payload
        assert payload["data"]["work_item"]["status"] == "active"
        assert payload["data"]["delegation"]["status"] == "planned"
        assert "Zmiana kodu nie została jeszcze zastosowana" in payload["answer"]


def test_approved_code_change_creates_rollback_evidence_without_claiming_verification(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    monkeypatch.setattr(RuntimeHealthService, "snapshot", _runtime_ready)
    monkeypatch.setattr(ProjectReadinessService, "build", _readiness_ready)
    project_root = tmp_path / "safe-project"
    project_root.mkdir()
    target = project_root / "main.py"
    target.write_text("print('old')\n", encoding="utf-8")

    with TestClient(app) as client:
        project_id = _create_project(app)
        app.state.settings.self_improvement_allow_self_apply = True
        with app.state.database.session_scope() as session, patch(
            "atnos.services.self_coding_draft_service.WorkflowLLMService.generate_text",
            return_value=SimpleNamespace(
                content=json.dumps(
                    {
                        "summary": "Update output",
                        "files": [{"path": "main.py", "content": "print('new')\n"}],
                    }
                )
            ),
        ):
            preview = SelfCodingDraftService(
                session,
                settings=app.state.settings,
                provider_registry=SimpleNamespace(),
            ).edit_existing_app(
                project_root=str(project_root),
                task_description="Update the safe test file.",
                files_to_modify=["main.py"],
                project_id=project_id,
            )
            AccountActionBrokerService(session, app.state.settings).approve(preview.action_draft_id)

        response = client.post(f"/api/project-command/{project_id}/apply-approved-code/{preview.action_draft_id}")

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "applied_unverified"
        assert payload["data"]["execution"]["rollback_status"] == "applied_unverified"
        assert target.read_text(encoding="utf-8") == "print('new')\n"
        assert any(item["key"] == "rollback_point:latest" for item in payload["evidence"])


def test_verification_runs_allowlisted_recipe_and_records_proof(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    monkeypatch.setattr(RuntimeHealthService, "snapshot", _runtime_ready)
    monkeypatch.setattr(ProjectReadinessService, "build", _readiness_ready)
    monkeypatch.setattr(
        ProjectVerificationService,
        "run",
        lambda *_args, **_kwargs: ProjectVerificationResult(
            recipe="backend_project_command_tests",
            status="verified",
            command=("python", "-m", "pytest", "tests/test_project_command_api.py", "-q"),
            cwd="F:/Apps/Atnos/runtime-recovered/api",
            exit_code=0,
            duration_ms=142,
            summary="Weryfikacja zakonczona powodzeniem.",
            output="5 passed",
            next_step="Dowod testu jest aktualny.",
        ),
    )

    with TestClient(app) as client:
        project_id = _create_project(app)
        response = client.post(
            f"/api/project-command/{project_id}/execute",
            json={"content": "Uruchom testy backendu i pokaz dowody."},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["intent"] == "verification"
        assert payload["status"] == "completed"
        verification = payload["data"]["verification"]
        assert verification["recipe"] == "backend_project_command_tests"
        assert verification["exit_code"] == 0
        assert any(item["key"] == "verification:backend_project_command_tests" for item in payload["evidence"])


def test_failed_verification_never_claims_completion(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    monkeypatch.setattr(RuntimeHealthService, "snapshot", _runtime_ready)
    monkeypatch.setattr(ProjectReadinessService, "build", _readiness_ready)
    monkeypatch.setattr(
        ProjectVerificationService,
        "run",
        lambda *_args, **_kwargs: ProjectVerificationResult(
            recipe="frontend_build",
            status="failed",
            command=("pnpm", "run", "build"),
            cwd="F:/Apps/Atnos/frontend",
            exit_code=1,
            duration_ms=210,
            summary="Weryfikacja wykryla blad.",
            output="TS error",
            next_step="Popraw blad i uruchom recepte ponownie.",
        ),
    )

    with TestClient(app) as client:
        project_id = _create_project(app)
        response = client.post(
            f"/api/project-command/{project_id}/execute",
            json={"content": "Zbuduj frontend."},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["intent"] == "verification"
        assert payload["status"] == "failed"
        assert payload["data"]["verification"]["exit_code"] == 1
        assert "nie oznaczyl zadnej zmiany jako ukonczonej" in payload["answer"]


def test_verification_reports_missing_local_tool_as_blocked_not_server_error(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    monkeypatch.setattr(
        ProjectVerificationService,
        "_pnpm_path",
        staticmethod(lambda: (_ for _ in ()).throw(FileNotFoundError("pnpm"))),
    )

    result = ProjectVerificationService(app.state.settings).run("frontend_build")

    assert result.status == "blocked_technical"
    assert result.exit_code is None
    assert "pnpm" in result.summary


def test_high_risk_request_is_stopped_before_external_action(tmp_path: Path, monkeypatch) -> None:
    app = create_test_app(tmp_path)
    monkeypatch.setattr(RuntimeHealthService, "snapshot", _runtime_ready)
    monkeypatch.setattr(ProjectReadinessService, "build", _readiness_ready)

    with TestClient(app) as client:
        project_id = _create_project(app)
        response = client.post(
            f"/api/project-command/{project_id}/execute",
            json={"content": "Opublikuj film na YouTube i wyślij e-mail do zespołu."},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "waiting_for_user"
        assert payload["requires_confirmation"] is True
        assert payload["intent"] == "high_risk"
        assert "nie wykonano żadnej operacji zewnętrznej" in payload["answer"]
