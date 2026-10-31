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

from test_support import create_test_app  # noqa: E402


def test_creator_cockpit_reports_missing_default_project(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/api/creator-cockpit/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "not_configured"
    assert payload["project"] is None
    assert payload["capabilities"]["can_initialize"] is True
    assert payload["capabilities"]["can_publish_external"] is False
    assert "synthetic_creator_project_missing" in payload["blockers"]
    assert payload["safety"]["explicit_content"] == "blocked"
    assert payload["safety"]["card_data_storage"] == "forbidden"


def test_creator_cockpit_ensure_default_creates_safe_project(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        response = client.post("/api/creator-cockpit/ensure-default")
        status_response = client.get("/api/creator-cockpit/status")

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "limited"
    assert payload["project"]["name"] == "Synthetic Model Lab"
    assert payload["project"]["project_type"] == "synthetic_model"
    assert payload["project"]["persona_type"] == "synthetic_original_persona"
    assert payload["project"]["disclosure_policy"] == "platform_dependent"
    assert "onlyfans_manual_review" in payload["project"]["platforms"]
    assert payload["capabilities"]["can_generate_private_drafts"] is True
    assert payload["capabilities"]["can_publish_external"] is False
    assert payload["capabilities"]["can_run_paid_platform"] is False
    assert "external_publish_blocked" in payload["blockers"]
    assert status_response.json()["project"]["id"] == payload["project"]["id"]


def test_creator_cockpit_seed_work_items_builds_asset_ledger(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        client.post("/api/creator-cockpit/ensure-default")
        response = client.post("/api/creator-cockpit/seed-work-items")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "seeded"
    cockpit = payload["cockpit"]
    assert cockpit["counts"]["work_items"] > 0
    assert cockpit["counts"]["asset_ledger"] > 0
    assert all(item["disclosure_required"] is True for item in cockpit["asset_ledger"])
    assert all(item["license_status"] == "internal_draft" for item in cockpit["asset_ledger"])


def test_creator_cockpit_external_publish_is_blocked(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        client.post("/api/creator-cockpit/ensure-default")
        response = client.post("/api/creator-cockpit/external-publish", json={"platform": "onlyfans"})

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["status"] == "blocked"
    assert detail["external_publish_enabled"] is False
    assert detail["paid_platform_actions_enabled"] is False
    assert detail["safety"]["explicit_content"] == "blocked"


def test_project_readiness_synthetic_project_allows_private_drafts_after_safe_init(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        client.post("/api/creator-cockpit/ensure-default")
        payload = client.get("/api/projects/readiness").json()

    synthetic = next(project for project in payload["projects"] if project["project_type"] == "synthetic_creator")
    assert synthetic["status"] == "limited"
    assert synthetic["capabilities"]["can_research"] is True
    assert synthetic["capabilities"]["can_generate_private"] is True
    assert synthetic["capabilities"]["can_publish_public"] is False
    assert synthetic["capabilities"]["can_trade_or_purchase"] is False
    assert synthetic["evidence"]["core_safety_ready"] is True
    assert synthetic["evidence"]["fully_connected"] is False
    tooling = {item["key"]: item for item in synthetic["tooling_plan"]["must_have"]}
    assert tooling["asset_ledger"]["status"] == "limited"
    assert tooling["synthetic_disclosure"]["status"] == "ready"
