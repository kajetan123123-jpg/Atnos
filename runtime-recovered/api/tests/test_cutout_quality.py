"""Cutout alpha QC + approval policy tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from atnos.production.asset_brain import (
    AssetApprovalError,
    AssetApprovalService,
    AssetCatalog,
    AssetGenerationProvenance,
    AssetRecord,
    WealthBlueprintAssetBrain,
)
from atnos.production.cutout_quality import improve_alpha_mask, run_alpha_qc
from atnos.services.local_cutout_service import remove_background


def _rgba_cutout(path: Path, *, fg_frac: float = 0.4, border_opaque: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    w = h = 512
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = img.load()
    margin = int((1 - fg_frac**0.5) * w / 2)
    for y in range(margin, h - margin):
        for x in range(margin, w - margin):
            px[x, y] = (40, 90, 140, 255)
    if border_opaque:
        for x in range(w):
            px[x, 0] = (250, 250, 250, 255)
            px[x, h - 1] = (250, 250, 250, 255)
    img.save(path)
    return path


def test_alpha_qc_pass_sensible_cutout(tmp_path: Path):
    path = _rgba_cutout(tmp_path / "ok.png", fg_frac=0.35)
    qc = run_alpha_qc(path)
    assert qc.passed is True
    assert qc.verdict == "PASS"


def test_alpha_qc_rejects_almost_empty(tmp_path: Path):
    path = tmp_path / "empty.png"
    Image.new("RGBA", (512, 512), (0, 0, 0, 0)).save(path)
    qc = run_alpha_qc(path)
    assert qc.passed is False
    assert qc.verdict == "REJECTED"


def test_alpha_qc_needs_reprocess_on_giant_bg(tmp_path: Path):
    path = _rgba_cutout(tmp_path / "halo.png", fg_frac=0.5, border_opaque=True)
    # Fill most borders opaque white
    img = Image.open(path).convert("RGBA")
    px = img.load()
    for y in range(512):
        for x in range(40):
            px[x, y] = (255, 255, 255, 255)
            px[511 - x, y] = (255, 255, 255, 255)
    img.save(path)
    qc = run_alpha_qc(path)
    assert qc.passed is False
    assert qc.verdict in {"NEEDS_REPROCESS", "REJECTED"}


def test_approve_blocked_when_alpha_qc_fails(tmp_path: Path):
    lib = tmp_path / "lib"
    lib.mkdir()
    catalog_file = lib / "catalog.json"
    bad_path = tmp_path / "assets" / "bad.png"
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (512, 512), (0, 0, 0, 0)).save(bad_path)

    asset = AssetRecord(
        id="cutout_bad",
        type="character",
        file_path=str(bad_path),
        channel="wealth_blueprint",
        character="alex",
        pose="standing",
        emotion="neutral",
        direction="front",
        transparent_background=True,
        width=512,
        height=512,
        approved=False,
        quality_status="AWAITING_REVIEW",
        source="comfyui_test",
        license_status="generated_pending_review",
        request_fingerprint="b" * 40,
        generation=AssetGenerationProvenance(
            prompt="alex",
            workflow_id="wealth_character_v1",
            seed=1,
            model="sdxl",
            generated_at="2026-09-18T00:00:00Z",
            source_workflow="wealth_character_v1",
            channel="wealth_blueprint",
            approval_state="pending",
            fingerprint="c" * 40,
            reference_files=[str(tmp_path / "anchor.png")],
        ),
    )
    Image.new("RGB", (64, 64), (10, 10, 10)).save(tmp_path / "anchor.png")
    catalog = AssetCatalog(channel="wealth_blueprint", library_id="test", assets=[asset])
    from atnos.production.asset_brain.catalog import save_catalog

    save_catalog(catalog, catalog_file)
    brain = WealthBlueprintAssetBrain(catalog=catalog, catalog_file=catalog_file, library_root=lib)
    svc = AssetApprovalService(brain)
    with pytest.raises(AssetApprovalError):
        svc.approve("cutout_bad")


def test_improved_mask_preserves_size():
    rgb = Image.new("RGB", (128, 128), (30, 80, 120))
    alpha = Image.new("L", (128, 128), 0)
    # center blob
    for y in range(32, 96):
        for x in range(32, 96):
            alpha.putpixel((x, y), 220)
    out = improve_alpha_mask(rgb, alpha, mode="improved")
    assert out.size == (128, 128)


@pytest.mark.skipif(
    not Path(r"E:\Atnos\Models\background-removal\u2net.onnx").is_file(),
    reason="u2net.onnx not installed on E:",
)
def test_remove_background_improved_smoke(tmp_path: Path):
    src = tmp_path / "src.png"
    Image.new("RGB", (320, 320), (245, 240, 230)).save(src)
    # draw subject
    img = Image.open(src)
    from PIL import ImageDraw

    d = ImageDraw.Draw(img)
    d.ellipse((80, 60, 240, 260), fill=(20, 40, 90))
    img.save(src)
    dest = tmp_path / "out.png"
    meta = remove_background(src, dest, postprocess="improved", run_qc=True)
    assert dest.is_file()
    assert meta["postprocess"] == "improved"
    assert "alpha_qc" in meta
