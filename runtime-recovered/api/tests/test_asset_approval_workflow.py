"""Character asset approval workflow tests — never auto-approves real catalog."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from atnos.production.asset_brain import (
    AssetApprovalService,
    AssetCatalog,
    AssetGenerationProvenance,
    AssetQuery,
    AssetRecord,
    WealthBlueprintAssetBrain,
    build_review_contact_sheet,
)
from atnos.production.asset_brain.approval import AssetApprovalError


def _png(path: Path, *, color: tuple[int, int, int] = (40, 90, 140), size=(512, 512), rgba=False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if rgba:
        # Proper cutout fixture: opaque subject + transparent margins (motion-design ready)
        img = Image.new("RGBA", size, (0, 0, 0, 0))
        px = img.load()
        margin = 64
        for y in range(margin, size[1] - margin):
            for x in range(margin, size[0] - margin):
                shade = (color[0] + (x + y) % 40) % 256
                px[x, y] = (shade, color[1], color[2], 255)
    else:
        img = Image.new("RGB", size, color)
        px = img.load()
        for y in range(size[1]):
            for x in range(0, size[0], 8):
                shade = (color[0] + (x + y) % 40) % 256
                px[x, y] = (shade, color[1], color[2])
    img.save(path)
    return path


def _brain(tmp_path: Path) -> WealthBlueprintAssetBrain:
    lib = tmp_path / "lib"
    lib.mkdir()
    catalog_file = lib / "catalog.json"
    brain = WealthBlueprintAssetBrain(
        catalog=AssetCatalog(channel="wealth_blueprint", library_id="test_v1", assets=[]),
        catalog_file=catalog_file,
        library_root=lib,
    )
    return brain


def test_invalid_mutation_cannot_corrupt_saved_catalog(tmp_path):
    from atnos.production.asset_brain.catalog import save_catalog,load_catalog
    from pydantic import ValidationError
    brain=_brain(tmp_path)
    record=_generated_record(tmp_path,asset_id='valid',character='Alex',emotion='neutral')
    brain.catalog.assets.append(record)
    save_catalog(brain.catalog,brain.catalog_file)
    original=brain.catalog_file.read_bytes()
    record.reviewed_by='x'*129
    with pytest.raises(ValidationError):
        save_catalog(brain.catalog,brain.catalog_file)
    assert brain.catalog_file.read_bytes()==original
    assert load_catalog(brain.catalog_file).assets[0].reviewed_by is None
    assert not list(brain.catalog_file.parent.glob('*.tmp'))


def test_machine_review_identity_survives_catalog_roundtrip(tmp_path):
    from atnos.production.asset_brain.catalog import save_catalog,load_catalog
    brain=_brain(tmp_path)
    record=_generated_record(tmp_path,asset_id='valid',character='Alex',emotion='neutral')
    record.reviewed_by='atnos_automatic_visual_qc:'+'a'*64
    brain.catalog.assets.append(record)
    save_catalog(brain.catalog,brain.catalog_file)
    assert load_catalog(brain.catalog_file).assets[0].reviewed_by==record.reviewed_by


def _generated_record(tmp_path: Path, *, asset_id: str, character: str, emotion: str, color=(40, 90, 140)) -> AssetRecord:
    path = _png(tmp_path / "assets" / f"{asset_id}.png", color=color, rgba=True)
    return AssetRecord(
        id=asset_id,
        type="character",
        file_path=str(path),
        channel="wealth_blueprint",
        character=character,
        pose="standing",
        emotion=emotion,
        direction="front",
        transparent_background=True,
        width=512,
        height=512,
        approved=False,
        quality_status="GENERATED",
        source="comfyui_test",
        license_status="generated_pending_review",
        request_fingerprint=("f" * 32) + asset_id[-8:].ljust(8, "0"),
        generation=AssetGenerationProvenance(
            prompt=f"{character} {emotion}",
            workflow_id="wealth_character_v1",
            seed=42,
            model="sd_xl_turbo_1.0_fp16.safetensors",
            generated_at="2026-09-18T00:00:00Z",
            source_workflow="wealth_character_v1",
            channel="wealth_blueprint",
            approval_state="pending",
            fingerprint=("a" * 32) + asset_id[-8:].ljust(8, "0"),
            reference_files=[str(tmp_path / "anchor.png")],
        ),
    )


def test_generated_pregate_awaiting_review_approve_reject_selector(tmp_path: Path):
    brain = _brain(tmp_path)
    _png(tmp_path / "anchor.png")
    svc = AssetApprovalService(brain)

    good = _generated_record(tmp_path, asset_id="test_alex_neutral_001", character="alex", emotion="neutral")
    ingested = svc.ingest_generated(good, run_pregate=True)
    assert ingested.quality_status == "AWAITING_REVIEW"
    assert ingested.approved is False
    assert ingested.pre_gate.get("passed") is True

    # Production selector must not pick awaiting review
    missing = brain.select_best(
        AssetQuery(type="character", character="alex", emotion="neutral", require_approved=True)
    )
    assert missing.status == "missing"

    # Named characters require contact-sheet QC before APPROVED
    with pytest.raises(AssetApprovalError, match="contact-sheet"):
        svc.approve(ingested.id, reviewed_by="fixture")

    sheet = build_review_contact_sheet(
        [ingested], resolve_file=brain.resolve_file, dest=tmp_path / "review" / "alex.jpg"
    )
    stamped = ingested.model_copy(update={"contact_sheet_path": str(sheet)})
    svc._replace(stamped)  # noqa: SLF001

    approved = svc.approve(ingested.id, reviewed_by="fixture")
    assert approved.quality_status == "APPROVED"
    assert approved.approved is True

    found = brain.select_best(
        AssetQuery(type="character", character="alex", emotion="neutral", require_approved=True)
    )
    assert found.status == "found"
    assert found.match is not None
    assert found.match.asset.id == approved.id

    # Reject sibling — must not be selectable
    bad = _generated_record(
        tmp_path,
        asset_id="test_alex_confused_001",
        character="alex",
        emotion="confused",
        color=(200, 30, 30),
    )
    waiting = svc.ingest_generated(bad, run_pregate=True)
    assert waiting.quality_status == "AWAITING_REVIEW"
    rejected = svc.reject(waiting.id, "FACE_DRIFT", notes="fixture reject", reviewed_by="fixture")
    assert rejected.quality_status == "REJECTED"
    assert rejected.approved is False

    still = brain.select_best(
        AssetQuery(type="character", character="alex", emotion="confused", require_approved=True)
    )
    assert still.status == "missing"

    metrics = svc.metrics()
    assert metrics["approved_count"] >= 1
    assert metrics["rejected_count"] >= 1
    assert metrics["reject_reasons"].get("FACE_DRIFT") == 1


def test_pregate_fail_blocks_review_queue(tmp_path: Path):
    brain = _brain(tmp_path)
    path = tmp_path / "tiny.png"
    Image.new("RGB", (8, 8), (0, 0, 0)).save(path)
    record = AssetRecord(
        id="test_tiny_001",
        type="character",
        file_path=str(path),
        channel="wealth_blueprint",
        character="niko",
        emotion="happy",
        width=512,
        height=512,
        quality_status="GENERATED",
        generation=AssetGenerationProvenance(
            prompt="niko",
            workflow_id="wonder_character_reference_v1",
            seed=1,
            model="x",
            generated_at="2026-09-18T00:00:00Z",
            source_workflow="wonder_character_reference_v1",
            channel="wonder_crew",
            fingerprint="b" * 40,
            reference_files=[],
        ),
    )
    svc = AssetApprovalService(brain)
    ingested = svc.ingest_generated(record, run_pregate=True)
    assert ingested.quality_status == "GENERATED"
    assert ingested.pre_gate.get("passed") is False
    assert ingested.id not in {a.id for a in svc.list_awaiting_review()}


def test_contact_sheet_is_separate_artifact(tmp_path: Path):
    brain = _brain(tmp_path)
    _png(tmp_path / "anchor.png")
    svc = AssetApprovalService(brain)
    a = svc.ingest_generated(_generated_record(tmp_path, asset_id="sheet_a", character="mira", emotion="happy"))
    b = svc.ingest_generated(
        _generated_record(tmp_path, asset_id="sheet_b", character="mira", emotion="surprised", color=(20, 160, 90))
    )
    src_a = Path(a.file_path).read_bytes()
    dest = tmp_path / "review" / "mira_sheet.jpg"
    sheet = build_review_contact_sheet([a, b], resolve_file=brain.resolve_file, dest=dest)
    assert sheet.is_file()
    assert Path(a.file_path).read_bytes() == src_a  # production PNG unchanged


def test_legacy_approved_bool_maps_to_quality_status():
    record = AssetRecord(
        id="legacy_ok",
        type="icon",
        file_path="icons/x.png",
        channel="wealth_blueprint",
        approved=True,
    )
    assert record.quality_status == "APPROVED"
    assert record.approved is True


def test_approving_distinct_scene_objects_keeps_both_available(tmp_path: Path):
    brain = _brain(tmp_path)
    svc = AssetApprovalService(brain)
    for scene, color in (("income", (40, 90, 140)), ("goal", (180, 100, 40))):
        record = _generated_record(tmp_path, asset_id=f"object_{scene}", character=scene, emotion="neutral", color=color)
        record = record.model_copy(update={"type": "icon", "character": None, "pose": None,
                                           "emotion": None, "direction": None,
                                           "tags": [f"wb-scene-{scene}"]})
        svc.ingest_generated(record)
        svc.approve(record.id, reviewed_by="fixture")
    for scene in ("income", "goal"):
        assert brain.get(f"object_{scene}").quality_status == "APPROVED"
        selected = brain.select_best(AssetQuery(type="icon", tags_all=[f"wb-scene-{scene}"], require_approved=True))
        assert selected.status == "found"


def test_approving_character_pose_does_not_retire_another_pose(tmp_path: Path):
    brain = _brain(tmp_path)
    svc = AssetApprovalService(brain)
    for pose, color in (("standing", (40, 90, 140)), ("pointing", (180, 100, 40))):
        record = _generated_record(tmp_path, asset_id=f"pose_{pose}", character="mira", emotion="neutral", color=color)
        sheet = build_review_contact_sheet([record], resolve_file=brain.resolve_file,
                                          dest=tmp_path / "review" / f"{pose}.jpg")
        record = record.model_copy(update={"pose": pose, "contact_sheet_path": str(sheet)})
        svc.ingest_generated(record)
        svc.approve(record.id, reviewed_by="fixture")
    assert brain.get("pose_standing").quality_status == "APPROVED"
    assert brain.get("pose_pointing").quality_status == "APPROVED"
