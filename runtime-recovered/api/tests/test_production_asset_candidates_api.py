"""Job-scoped asset review exposes current QC without leaking local file paths."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from atnos.api.routes import production as routes
from atnos.intelligence.storage import connect
from atnos.production.asset_brain.models import AssetCatalog, AssetRecord
from atnos.production.asset_brain.service import WealthBlueprintAssetBrain
from atnos.production.comfy_channels.profiles import generated_root
from atnos.production.order_store import ProductionOrderStore
from test_support import create_test_app


def _seed_account(settings) -> None:
    with connect(settings) as db:
        db.execute(
            "INSERT INTO intelligence_accounts(account_id,niches,region,last_requested_at) "
            "VALUES('yt-wealth-blueprint','[\"finance\"]','US','2026-09-06T00:00:00Z')"
        )
        db.commit()


def _setup(tmp_path: Path, monkeypatch, *, asset_type: str = "icon", character: str | None = None):
    app = create_test_app(tmp_path)
    _seed_account(app.state.settings)
    store = ProductionOrderStore(app.state.settings)
    job_id = store.create(routes.ProductionOrder(topic="Review candidate"))["id"]
    image_path = generated_root(app.state.settings) / "wealth_blueprint" / "test_candidate.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), (31, 87, 150)).save(image_path)
    asset = AssetRecord(
        id="test_candidate",
        type=asset_type,
        character=character,
        file_path=str(image_path),
        channel="wealth_blueprint",
        quality_status="AWAITING_REVIEW",
        pre_gate={"passed": True, "verdict": "TECHNICALLY_VALID", "failed": [], "checks": [
            {"name": "file_integrity", "ok": True, "detail": str(image_path)}
        ]},
    )
    catalog = AssetCatalog(assets=[asset])
    brain = WealthBlueprintAssetBrain(catalog=catalog, library_root=tmp_path / "library")
    monkeypatch.setattr(routes, "WealthBlueprintAssetBrain", lambda settings: brain)
    artifact_dir = store.context(job_id).artifact_dir
    artifact_dir.mkdir(parents=True, exist_ok=True)
    manifest = artifact_dir / "scene_production_assets.json"
    _write_manifest(manifest, image_path)
    return app, store, job_id, image_path, manifest, brain, asset


def _write_manifest(manifest: Path, image_path: Path, *, asset_id: str = "test_candidate") -> None:
    manifest.write_text(json.dumps({
        "status": "waiting",
        "assignments": {},
        "waiting": [{
            "scene_id": "s01",
            "code": "WAITING_FOR_ASSETS",
            "acquisition": {
                "asset_id": asset_id,
                "status": "awaiting_approval",
                "output_path": str(image_path),
            },
        }],
    }), encoding="utf-8")


def test_rejected_checkpoint_candidate_remains_visible_before_final_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr('atnos.integrations.claim_evidence_gate.ensure_media_claim_gate', lambda **_: {'blocked':False})
    from atnos.production.order_store import file_hash
    app, store, job_id, image, manifest, brain, asset = _setup(tmp_path, monkeypatch)
    manifest.unlink()  # Isolated disposable fixture, not a production manifest.
    brain.catalog.assets[0] = asset.model_copy(update={
        'quality_status':'REJECTED', 'reviewed_by':'atnos_automatic_visual_qc:'+'a'*64,
        'reject_reason':'LOW_QUALITY'})
    copy=store.context(job_id).artifact_dir/'candidate-copy.png'
    copy.write_bytes(image.read_bytes())
    with store.lease(job_id) as token:
        stage='assets/image_local/s01/0123456789abcdef'
        store.begin(job_id, token, stage, {'fixture':'recorded native candidate'})
        store.complete(job_id, token, stage, {'asset_id':asset.id, 'output_path':str(image),
                       'quality_status':'GENERATED', 'image_sha256':file_hash(image)}, [copy])
        store.block(job_id, token, 'image_generation', category='execution_failure', message='isolated retry hold')
    before=store.get(job_id)
    with TestClient(app) as client:
        response=client.get(f'/api/production/orders/{job_id}/asset-candidates')
        row=response.json()['candidates'][0]
        preview=client.get(row['image_url'])
    assert response.status_code==200 and row['quality_status']=='REJECTED'
    assert row['can_approve'] is False and row['can_reject'] is False
    assert preview.content==image.read_bytes()
    assert store.get(job_id)==before
    assert not manifest.exists()
    image.write_bytes(b'changed fixture output')
    with TestClient(app) as client:
        assert client.get(f'/api/production/orders/{job_id}/asset-candidates').json()['candidates']==[]


def test_checkpoint_candidate_and_waiting_manifest_do_not_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr('atnos.integrations.claim_evidence_gate.ensure_media_claim_gate', lambda **_: {'blocked':False})
    from atnos.production.order_store import file_hash
    app, store, job_id, image, _, _, asset=_setup(tmp_path, monkeypatch)
    copy=store.context(job_id).artifact_dir/'candidate-copy.png'
    copy.write_bytes(image.read_bytes())
    with store.lease(job_id) as token:
        stage='assets/image_local/s01/0123456789abcdef'
        store.begin(job_id, token, stage, {'fixture':'same manifest candidate'})
        store.complete(job_id, token, stage, {'asset_id':asset.id, 'output_path':str(image),
                       'image_sha256':file_hash(image)}, [copy])
    with TestClient(app) as client:
        assert len(client.get(f'/api/production/orders/{job_id}/asset-candidates').json()['candidates'])==1


def test_image_only_review_manifest_is_visible_and_order_local_png_is_scoped(tmp_path, monkeypatch):
    app, store, job_id, _, manifest, brain, asset = _setup(tmp_path, monkeypatch)
    root = store.context(job_id).artifact_dir
    image = root / 'assets/image_local/flux_trial/scoped/image.png'
    image.parent.mkdir(parents=True)
    Image.new('RGB', (32, 32), (20, 70, 120)).save(image)
    brain.catalog.assets[0] = asset.model_copy(update={'file_path': str(image)})
    latest = root / 'image_api_review.json'
    _write_manifest(latest, image)
    import os
    os.utime(latest, ns=(manifest.stat().st_mtime_ns + 10_000_000, manifest.stat().st_mtime_ns + 10_000_000))
    with TestClient(app) as client:
        rows = client.get(f'/api/production/orders/{job_id}/asset-candidates').json()['candidates']
        response = client.get(rows[0]['image_url'])
    assert rows[0]['can_approve'] is True
    assert response.status_code == 200 and response.content == image.read_bytes()
    outside = store.context(store.create(routes.ProductionOrder(topic='Other candidate'))['id']).artifact_dir / 'assets/image_local/image.png'
    outside.parent.mkdir(parents=True)
    outside.write_bytes(image.read_bytes())
    brain.catalog.assets[0] = asset.model_copy(update={'file_path': str(outside)})
    _write_manifest(latest, outside)
    with TestClient(app) as client:
        rejected = client.get(f'/api/production/orders/{job_id}/asset-candidates').json()['candidates'][0]
    assert rejected['image_url'] is None and rejected['can_approve'] is False


def test_candidate_read_is_job_scoped_and_serves_verified_generated_image(tmp_path, monkeypatch):
    app, store, job_id, image_path, _manifest, _brain, _asset = _setup(tmp_path, monkeypatch)
    other_id = store.create(routes.ProductionOrder(topic="Other order"))["id"]

    with TestClient(app) as client:
        response = client.get(f"/api/production/orders/{job_id}/asset-candidates")
        row = response.json()["candidates"][0]
        image = client.get(row["image_url"])
        wrong_order = client.get(f"/api/production/orders/{other_id}/asset-candidates/test_candidate/image")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert row["scene_id"] == "s01"
    assert row["asset_id"] == "test_candidate"
    assert row["quality_status"] == "AWAITING_REVIEW"
    assert row["acquisition_status"] == "awaiting_approval"
    assert row["qc_verdict"] == "TECHNICALLY_VALID"
    assert row["can_approve"] is True and row["can_reject"] is True
    assert str(image_path) not in json.dumps(response.json())
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content == image_path.read_bytes()
    assert wrong_order.status_code == 404


def test_current_catalog_qc_failure_overrides_old_waiting_manifest(tmp_path, monkeypatch):
    app, _store, job_id, _image_path, _manifest, brain, asset = _setup(tmp_path, monkeypatch)
    rejected = asset.model_copy(update={
        "quality_status": "REJECTED",
        "reject_reason": "ALPHA_QC_FAIL",
        "reviewed_by": None,
        "pre_gate": {"passed": False, "verdict": "REJECTED", "failed": ["alpha_qc"]},
    })
    brain.catalog = AssetCatalog(assets=[rejected])

    with TestClient(app) as client:
        response = client.get(f"/api/production/orders/{job_id}/asset-candidates")

    row = response.json()["candidates"][0]
    assert row["quality_status"] == "REJECTED"
    assert row["qc_verdict"] == "REJECTED"
    assert row["qc_failed"] == ["alpha_qc"]
    assert row["pre_gate"] == {"passed": False, "verdict": "REJECTED", "failed": ["alpha_qc"]}
    assert row["reject_reason"] == "ALPHA_QC_FAIL"
    assert row["reviewed_by"] is None
    assert row["can_approve"] is False and row["can_reject"] is False


def test_named_character_requires_contact_sheet_before_approval(tmp_path, monkeypatch):
    app, _store, job_id, _image_path, _manifest, brain, asset = _setup(
        tmp_path, monkeypatch, asset_type="character", character="alex"
    )
    with TestClient(app) as client:
        before = client.get(f"/api/production/orders/{job_id}/asset-candidates").json()["candidates"][0]
        sheet = generated_root(app.state.settings) / "_review_sheets" / "alex.jpg"
        sheet.parent.mkdir(parents=True, exist_ok=True)
        sheet.write_bytes(b"review sheet")
        brain.catalog = AssetCatalog(assets=[asset.model_copy(update={"contact_sheet_path": str(sheet)})])
        after = client.get(f"/api/production/orders/{job_id}/asset-candidates").json()["candidates"][0]

    assert before["can_approve"] is False
    assert before["can_reject"] is True
    assert "arkusza" in before["approval_blocker"]
    assert after["can_approve"] is True


def test_candidate_image_requires_catalog_path_and_manifest_path_to_match(tmp_path, monkeypatch):
    app, _store, job_id, image_path, manifest, brain, asset = _setup(tmp_path, monkeypatch)
    alternate = image_path.with_name("other.png")
    Image.new("RGB", (32, 32), (150, 87, 31)).save(alternate)
    brain.catalog = AssetCatalog(assets=[asset.model_copy(update={"file_path": str(alternate)})])

    with TestClient(app) as client:
        mismatch = client.get(f"/api/production/orders/{job_id}/asset-candidates").json()["candidates"][0]
        image = client.get(f"/api/production/orders/{job_id}/asset-candidates/test_candidate/image")
        _write_manifest(manifest, alternate)
        outside = tmp_path / "outside.png"
        Image.new("RGB", (32, 32), (1, 2, 3)).save(outside)
        brain.catalog = AssetCatalog(assets=[asset.model_copy(update={"file_path": str(outside)})])
        _write_manifest(manifest, outside)
        escape = client.get(f"/api/production/orders/{job_id}/asset-candidates").json()["candidates"][0]
        escape_image = client.get(f"/api/production/orders/{job_id}/asset-candidates/test_candidate/image")

    assert mismatch["image_url"] is None
    assert mismatch["can_approve"] is False
    assert image.status_code == 404
    assert escape["image_url"] is None
    assert escape_image.status_code == 404


def test_missing_and_invalid_manifest_are_distinguished(tmp_path, monkeypatch):
    app, _store, job_id, _image_path, manifest, _brain, _asset = _setup(tmp_path, monkeypatch)
    manifest.unlink()
    with TestClient(app) as client:
        missing = client.get(f"/api/production/orders/{job_id}/asset-candidates")
        manifest.write_text("{broken", encoding="utf-8")
        invalid = client.get(f"/api/production/orders/{job_id}/asset-candidates")
    assert missing.status_code == 200 and missing.json()["candidates"] == []
    assert invalid.status_code == 409
