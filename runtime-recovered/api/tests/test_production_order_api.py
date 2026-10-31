from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from atnos.api.routes import production as production_routes
from atnos.intelligence.storage import connect
from atnos.production.order_dispatch import order_status
from atnos.production.order_store import REQUIRED_GATE_STAGES, ProductionBlocked, ProductionOrderStore, checkpoint_fingerprints, file_hash
from test_support import create_test_app


def seed_account(settings):
    with connect(settings) as db:
        db.execute(
            "INSERT INTO intelligence_accounts(account_id,niches,region,last_requested_at) "
            "VALUES('yt-wealth-blueprint','[\"finance\"]','US','2026-09-06T00:00:00Z')"
        )
        db.commit()


def test_one_order_api_creates_idempotent_job_and_delegates_to_worker(tmp_path, monkeypatch):
    app = create_test_app(tmp_path)
    launched = []

    def fake_dispatch(self, job_id):
        launched.append(job_id)
        return {**order_status(self.store.get(job_id)), "worker_requested": True}

    monkeypatch.setattr(production_routes.ProductionOrderDispatcher, "dispatch", fake_dispatch)
    with TestClient(app) as client:
        seed_account(app.state.settings)
        payload = {
            "project": "Wealth Blueprint",
            "topic": "Build your first serious savings target",
            "direction": "Talk directly to the viewer and focus on concrete weekly actions.",
            "optional_notes": "No publication.",
        }
        first = client.post("/api/production/orders", json=payload, headers={"Idempotency-Key": "api-order-1"})
        second = client.post("/api/production/orders", json=payload, headers={"Idempotency-Key": "api-order-1"})

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["request"]["project"] == "wealth_blueprint"
    assert first.json()["status"] == "queued"
    assert launched == [first.json()["id"], first.json()["id"]]


def test_order_api_rejects_conflicting_idempotency_key(tmp_path, monkeypatch):
    app = create_test_app(tmp_path)
    monkeypatch.setattr(
        production_routes.ProductionOrderDispatcher,
        "dispatch",
        lambda self, job_id: order_status(self.store.get(job_id)),
    )
    with TestClient(app) as client:
        seed_account(app.state.settings)
        first = client.post("/api/production/orders", json={"topic": "First topic"}, headers={"Idempotency-Key": "same-key"})
        second = client.post("/api/production/orders", json={"topic": "Different topic"}, headers={"Idempotency-Key": "same-key"})

    assert first.status_code == 202
    assert second.status_code == 409


def test_resume_endpoint_reports_blocked_without_starting_new_work(tmp_path, monkeypatch):
    app = create_test_app(tmp_path)
    with TestClient(app) as client:
        seed_account(app.state.settings)
        job_id = ProductionOrderStore(app.state.settings).create(
            production_routes.ProductionOrder(topic="Blocked topic")
        )["id"]

        def fake_dispatch(self, job_id):
            raise ProductionBlocked("attempt limit reached")

        monkeypatch.setattr(production_routes.ProductionOrderDispatcher, "dispatch", fake_dispatch)
        resume = client.post(f"/api/production/orders/{job_id}/resume")

    assert resume.status_code == 409


def _complete_passed_stage(store: ProductionOrderStore, job_id: str, token: str, stage: str):
    path = store.context(job_id).artifact_dir / f"{stage}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"status": "passed", "qa": {"status": "passed"}}
    store.begin(job_id, token, stage, {})
    path.write_text("{}", encoding="utf-8")
    store.complete(job_id, token, stage, payload, [path])
    return path


def _finish_ready_package(store: ProductionOrderStore, topic: str = "Ready package") -> tuple[str, Path]:
    job_id = store.create(production_routes.ProductionOrder(topic=topic))["id"]
    with store.lease(job_id) as token:
        for stage in REQUIRED_GATE_STAGES.values():
            if stage != "package":
                _complete_passed_stage(store, job_id, token, stage)
        root = store.context(job_id).artifact_dir
        video = root / "assets" / "final_short.mp4"
        thumb = root / "covers" / "thumbnail_landscape.png"
        metadata = root / "package" / "metadata.json"
        manifest = root / "package" / "manifest.json"
        for path, content in [(video, b"video"), (thumb, b"thumb")]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        metadata.parent.mkdir(parents=True, exist_ok=True)
        metadata.write_text('{"title":"Ready package","description":"Ready description","tags":["wealth"]}', encoding="utf-8")
        rows = store.get(job_id)["checkpoints"]
        dependency_files = [root / entry["path"] for row in rows for entry in row["files"]]
        files = [
            {"path": path.relative_to(root).as_posix(), "sha256": file_hash(path)}
            for path in [video, thumb, metadata, *dependency_files]
        ]
        package = {
            "status": "passed",
            "qa": {"status": "passed"},
            "deliverables": {"video": str(video), "landscape_thumbnail": str(thumb), "metadata": str(metadata)},
            "files": files,
            "checkpoint_fingerprints": checkpoint_fingerprints(rows),
            "output_file": str(manifest),
        }
        manifest.write_text("{}", encoding="utf-8")
        store.begin(job_id, token, "package", {})
        store.complete(job_id, token, "package", package, [manifest, video, thumb, metadata, *dependency_files])
        store.finish(job_id, token, manifest)
    return job_id, video


def test_order_review_package_and_artifacts_are_served_from_verified_manifest(tmp_path):
    app = create_test_app(tmp_path)
    with TestClient(app) as client:
        seed_account(app.state.settings)
        job_id, _video = _finish_ready_package(ProductionOrderStore(app.state.settings))
        package_response = client.get(f"/api/production/orders/{job_id}/review-package")
        video_response = client.get(f"/api/production/orders/{job_id}/review-artifacts/video")

    assert package_response.status_code == 200
    assert package_response.json()["ready_for_review"] is True
    assert package_response.json()["title"] == "Ready package"
    assert package_response.json()["video_url"].endswith("/review-artifacts/video")
    assert video_response.status_code == 200
    assert video_response.content == b"video"


def test_order_review_artifact_rejects_file_changed_after_manifest(tmp_path):
    app = create_test_app(tmp_path)
    with TestClient(app) as client:
        seed_account(app.state.settings)
        job_id, video = _finish_ready_package(ProductionOrderStore(app.state.settings))
        video.write_bytes(b"tampered")
        response = client.get(f"/api/production/orders/{job_id}/review-artifacts/video")
        review = client.get(f"/api/production/orders/{job_id}/review-package")

    assert response.status_code == 409
    assert review.status_code == 409


def test_queued_order_does_not_present_user_direction_as_publication_description(tmp_path):
    app = create_test_app(tmp_path)
    with TestClient(app) as client:
        seed_account(app.state.settings)
        job_id = ProductionOrderStore(app.state.settings).create(
            production_routes.ProductionOrder(topic="My next Short", direction="Instructions for the writer"))["id"]
        payload = client.get(f"/api/production/orders/{job_id}/review-package").json()
    assert payload["description"] == ""
    assert payload["ready_for_review"] is False
    assert payload["video_ready"] is False


def test_review_rejects_changed_metadata_instead_of_showing_unverified_text(tmp_path):
    app = create_test_app(tmp_path)
    with TestClient(app) as client:
        seed_account(app.state.settings)
        job_id, video = _finish_ready_package(ProductionOrderStore(app.state.settings))
        metadata = video.parent.parent / "package" / "metadata.json"
        metadata.write_text('{"title":"Changed after QA"}', encoding="utf-8")
        response = client.get(f"/api/production/orders/{job_id}/review-package")
    assert response.status_code == 409
