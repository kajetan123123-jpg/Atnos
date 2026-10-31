from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from atnos.api.routes import production as production_routes
from atnos.intelligence.storage import connect
from atnos.production.order_dispatch import order_status
from atnos.production.order_dispatch import ProductionOrderDispatcher
from atnos.production.order_store import ProductionBlocked, ProductionOrderStore
from atnos.production.project_profile import ProductionOrder
from atnos.production.local_image_allowance import effective_local_image_allowance
from atnos.production.wb_image_api import load_image_lane
from test_support import create_test_app


REFERENCE = Path(__file__).resolve().parents[1] / "config" / "brand-kits" / "wealth-blueprint-alex-01" / "visual-bible-board.jpg"


def _seed_account(settings):
    with connect(settings) as db:
        db.execute(
            "INSERT INTO intelligence_accounts(account_id,niches,region,last_requested_at) "
            "VALUES('yt-wealth-blueprint','[\"finance\"]','US','2026-09-06T00:00:00Z')"
        )
        db.commit()


def _selected_order(**overrides):
    values = {
        "topic": "Build a savings habit",
        "delivery_mode": "local_only",
        "image_provider": "comfyui-local",
        "image_reference_id": "wb-original-board",
        "visual_style_id": "A",
    }
    values.update(overrides)
    return ProductionOrder(**values)


def test_image_selection_fields_are_all_or_nothing_and_local_wb_film_only():
    with pytest.raises(ValueError, match="dostawcy, referencji i stylu"):
        ProductionOrder(topic="Test image selection", image_provider="comfyui-local")
    with pytest.raises(ValueError, match="lokalnego filmu Wealth Blueprint"):
        ProductionOrder(topic="Test image selection", image_provider="comfyui-local",
                        image_reference_id="wb-original-board", visual_style_id="A")
    with pytest.raises(ValueError, match="Wariant stylu"):
        ProductionOrder(topic="Test image selection", visual_style_id="A", project="children_stories",
                        production_scope="script_preview", delivery_mode="local_only")


def test_unselected_image_options_preserve_legacy_request_serialization():
    plain = ProductionOrder(topic="Legacy order").model_dump(mode="json")
    assert "image_provider" not in plain
    assert "image_reference_id" not in plain
    selected = _selected_order().model_dump(mode="json")
    assert selected["image_provider"] == "comfyui-local"
    assert selected["image_reference_id"] == "wb-original-board"
    assert selected["visual_style_id"] == "A"


def test_pending_local_image_setup_cannot_be_resumed_or_dispatched_by_monitor(tmp_path, monkeypatch):
    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.shorts_order_autonomy_enabled = True
    _seed_account(settings)
    store = ProductionOrderStore(settings)
    state = store.create(_selected_order(), request_key="pending-local-image-setup")
    assert state["status"] == "blocked"
    assert state["current_step"] == "image_setup"
    assert json.loads(state["error"])["category"] == "local_image_setup_pending"
    assert state["autonomous_recovery"]["enabled"] is True
    assert state["can_resume"] is False
    with pytest.raises(ProductionBlocked):
        ProductionOrderDispatcher(settings).dispatch(state["id"])

    def forbidden_dispatch(*args, **kwargs):
        raise AssertionError("pending image setup must never dispatch")

    monkeypatch.setattr(ProductionOrderDispatcher, "dispatch", forbidden_dispatch)
    from atnos.production.order_autonomy import recover_due

    assert recover_due(settings) == []
    assert store.get(state["id"])["current_step"] == "image_setup"


def test_image_options_do_not_expose_server_path_or_probe_provider_service(tmp_path, monkeypatch):
    app = create_test_app(tmp_path)
    settings = app.state.settings
    install = tmp_path / "comfy-install"
    models = tmp_path / "comfy-models"
    install.mkdir()
    models.mkdir()
    settings.comfyui_install_dir = install
    settings.comfyui_models_dir = models
    monkeypatch.setattr("atnos.production.order_image_setup._REFERENCE_PATH", REFERENCE)

    with TestClient(app) as client:
        response = client.get("/api/production/image-options")
    assert response.status_code == 200
    payload = response.json()
    assert payload["providers"] == [{
        "id": "comfyui-local", "label": "ComfyUI (lokalny, bez opłaty za API)",
        "available": True, "requires_reference": True,
    }]
    assert payload["references"] == [{
        "id": "wb-original-board", "label": "Plansza referencyjna Wealth Blueprint", "available": True,
    }]
    assert {item["id"] for item in payload["styles"]} == {"A", "B", "C", "D"}
    assert str(REFERENCE) not in json.dumps(payload)


def test_explicit_image_order_registers_lane_once_before_idempotent_dispatch(tmp_path, monkeypatch):
    app = create_test_app(tmp_path)
    settings = app.state.settings
    settings.shorts_order_autonomy_enabled = True
    install = tmp_path / "comfy-install"
    models = tmp_path / "comfy-models"
    install.mkdir()
    models.mkdir()
    settings.comfyui_install_dir = install
    settings.comfyui_models_dir = models
    monkeypatch.setattr("atnos.production.order_image_setup._REFERENCE_PATH", REFERENCE)
    dispatched = []
    monkeypatch.setattr(
        production_routes.ProductionOrderDispatcher, "dispatch",
        lambda self, job_id: (dispatched.append(job_id) or {
            **order_status(self.store.get(job_id)), "worker_requested": True,
        }),
    )
    with TestClient(app) as client:
        _seed_account(settings)
        payload = {
            "topic": "Build a savings habit",
            "delivery_mode": "local_only",
            "image_provider": "comfyui-local",
            "image_reference_id": "wb-original-board",
            "visual_style_id": "A",
        }
        first = client.post("/api/production/orders", json=payload, headers={"Idempotency-Key": "wb-image-1"})
        assert first.status_code == 202, first.text
        job_id = first.json()["id"]
        lane = load_image_lane(settings, job_id)
        assert lane["provider"] == "comfyui-local"
        assert Path(lane["reference_path"]).resolve() == REFERENCE.resolve()
        assert first.json()["request"]["image_provider"] == "comfyui-local"
        assert first.json()["request"]["image_reference_id"] == "wb-original-board"
        assert first.json()["request"]["visual_style_id"] == "A"
        receipt = ProductionOrderStore(settings).context(job_id).artifact_dir / "image_local_authorization.json"
        receipt_bytes = receipt.read_bytes()
        first_state = ProductionOrderStore(settings).get(job_id)
        assert lane["max_requests"] == 20
        assert effective_local_image_allowance(settings, job_id, lane) == 40
        second = client.post("/api/production/orders", json=payload, headers={"Idempotency-Key": "wb-image-1"})

    assert second.status_code == 202, second.text
    assert second.json()["id"] == job_id
    assert dispatched == [job_id, job_id]
    assert receipt.read_bytes() == receipt_bytes
    with connect(settings) as db:
        rows = db.execute("SELECT job_id FROM production_image_lane_authorizations WHERE job_id=?", (job_id,)).fetchall()
        allowance_rows = db.execute(
            "SELECT sequence,payload_json FROM production_local_image_allowance_events WHERE job_id=?", (job_id,)
        ).fetchall()
    assert len(rows) == 1
    assert len(allowance_rows) == 1
    allowance = json.loads(allowance_rows[0]["payload_json"])
    assert allowance["maximum"] == 40
    assert allowance["original_maximum"] == 20
    state_after = ProductionOrderStore(settings).get(job_id)
    assert state_after["provider_calls"] == first_state["provider_calls"]
    assert state_after["reserved_cost_units"] == first_state["reserved_cost_units"]
    state = ProductionOrderStore(settings).get(job_id)
    assert state["request"]["image_provider"] == "comfyui-local"
    assert state["status"] == "queued"
    assert state["current_step"] == "queued"
    assert state["can_resume"] is True
    setup_events = [event for event in state["history"] if event["stage"] == "image_setup" and event["event"] == "completed"]
    assert len(setup_events) == 1


def test_invalid_reference_is_rejected_before_order_creation(tmp_path, monkeypatch):
    app = create_test_app(tmp_path)
    settings = app.state.settings
    install = tmp_path / "comfy-install"
    models = tmp_path / "comfy-models"
    install.mkdir()
    models.mkdir()
    settings.comfyui_install_dir = install
    settings.comfyui_models_dir = models
    missing = tmp_path / "missing-reference.jpg"
    monkeypatch.setattr("atnos.production.order_image_setup._REFERENCE_PATH", missing)
    with TestClient(app) as client:
        _seed_account(settings)
        response = client.post("/api/production/orders", json={
            "topic": "Build a savings habit", "delivery_mode": "local_only",
            "image_provider": "comfyui-local", "image_reference_id": "wb-original-board",
            "visual_style_id": "A",
        }, headers={"Idempotency-Key": "wb-image-missing-ref"})
    assert response.status_code == 400
    with connect(settings) as db:
        assert db.execute("SELECT COUNT(*) FROM pipeline_job_orders").fetchone()[0] == 0
