"""Unit + integration: production asset roles and per-scene selection."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from atnos.production.asset_brain.catalog import load_catalog
from atnos.production.asset_brain.models import AssetQuery, AssetRecord
from atnos.production.asset_brain.service import WealthBlueprintAssetBrain
from atnos.production.channels.wealth_blueprint.manifest_adapters import scene_plan_to_project_manifest
from atnos.production.channels.wealth_blueprint.scene_production_assets import (
    WAITING_FOR_ASSETS,
    assert_production_asset,
    manifest_uses_reference_sheet,
    resolve_scene_production_assets,
    stage_production_still,
)


@pytest.fixture()
def brain() -> WealthBlueprintAssetBrain:
    return WealthBlueprintAssetBrain(catalog=load_catalog())


@pytest.mark.parametrize(
    ("quality_status", "reject_reason", "expected_status"),
    [
        ("AWAITING_REVIEW", None, "awaiting_approval"),
        ("NEEDS_REPROCESS", None, "needs_reprocess"),
        ("REJECTED", "ALPHA_QC_FAIL", "rejected"),
    ],
)
def test_acquisition_reports_registered_candidate_status(
    monkeypatch, quality_status: str, reject_reason: str | None, expected_status: str
):
    from atnos.production import order_comfy_routing as routing
    from atnos.production.channels.wealth_blueprint import scene_production_assets as scene_assets

    candidate = SimpleNamespace(
        id="candidate-s02",
        quality_status=quality_status,
        reject_reason=reject_reason,
        pre_gate={"failed": ["alpha_qc"] if quality_status != "AWAITING_REVIEW" else []},
    )
    monkeypatch.setattr(
        scene_assets, "WealthBlueprintAssetBrain",
        lambda settings: SimpleNamespace(get=lambda asset_id: candidate),
    )

    class FakeFulfiller:
        def __init__(self, settings):
            pass

        def fulfill(self, request):
            return SimpleNamespace(
                status="awaiting_approval", usable_in_final_film=False,
                reason="Generated via WB Comfy profile — pending quality/approval policy",
                asset_brain_id="candidate-s02", output_path="E:/candidate.png",
            )

    monkeypatch.setattr(routing, "OrderComfyAssetFulfiller", FakeFulfiller)
    asset, acquisition = scene_assets._try_acquisition(
        object(), {"type": "icon", "text": "New expense absorbs extra income"}, allow_generate=True
    )

    assert asset is None
    assert acquisition is not None
    assert acquisition["status"] == expected_status
    assert acquisition["quality_status"] == quality_status
    if quality_status != "AWAITING_REVIEW":
        assert acquisition["failed_checks"] == ["alpha_qc"]
    if reject_reason:
        assert acquisition["reject_reason"] == reject_reason


def test_reference_sheet_never_production_selectable(brain: WealthBlueprintAssetBrain):
    ref = brain.get("alex_explaining_right_v01")
    assert ref is not None
    assert ref.production_role == "reference_sheet"
    assert ref.is_production_selectable() is False
    result = brain.select_best(
        AssetQuery(type="character", character="alex", pose="explaining", require_approved=True)
    )
    assert result.status == "found"
    assert result.match is not None
    assert result.match.asset.production_role != "reference_sheet"
    assert "channel_references" not in result.match.asset.file_path.replace("\\", "/")


def test_unapproved_or_wrong_channel_rejected(brain: WealthBlueprintAssetBrain):
    wrong = AssetRecord(
        id="other_channel_icon",
        type="icon",
        file_path="placeholders/icon_piggy_bank_v01.png",
        channel="wonder_crew",
        approved=True,
        quality_status="APPROVED",
        production_role="icon",
        transparent_background=True,
    )
    with pytest.raises(ValueError, match="channel mismatch"):
        assert_production_asset(wrong, channel="wealth_blueprint")

    unapproved = AssetRecord(
        id="pending_cutout",
        type="character",
        file_path="placeholders/alex_explaining_right_cutout_v01.png",
        channel="wealth_blueprint",
        character="alex",
        approved=False,
        quality_status="AWAITING_REVIEW",
        production_role="character_cutout",
        transparent_background=True,
    )
    with pytest.raises(ValueError, match="not APPROVED"):
        assert_production_asset(unapproved)

    result = brain.select_best(
        AssetQuery(
            type="character",
            character="alex",
            channel="wonder_crew",
            require_approved=True,
        )
    )
    assert result.status == "missing"


def test_character_alpha_and_proportions_preserved(tmp_path: Path, brain: WealthBlueprintAssetBrain):
    cut = brain.get("alex_front_cutout_v02")
    assert cut is not None
    source = brain.resolve_file(cut)
    assert source.is_file()
    src = Image.open(source)
    assert src.mode == "RGBA"
    src_w, src_h = src.size
    dest = tmp_path / "staged.png"
    meta = stage_production_still(source, dest, preserve_alpha=True, fit="contain")
    out = Image.open(dest)
    assert out.mode == "RGBA"
    assert out.size == (1080, 1920)
    # Corners stay transparent — not flattened to opaque RGB canvas.
    corner = out.getpixel((0, 0))
    assert len(corner) == 4 and corner[3] == 0
    # Uniform contain scale (never independent X/Y stretch to 1080×1920).
    scale = min(1080 / src_w, 1920 / src_h)
    expected_w = max(1, int(round(src_w * scale)))
    expected_h = max(1, int(round(src_h * scale)))
    assert meta["source_size"] == [src_w, src_h]
    assert abs(expected_w / expected_h - src_w / src_h) < 1e-4
    assert expected_w <= 1080 and expected_h <= 1920
    assert meta["preserve_alpha"] is True
    assert meta["fit"] == "contain"


def test_two_scenes_get_correct_distinct_assignments(tmp_path: Path, brain: WealthBlueprintAssetBrain):
    from atnos.core.config import get_settings

    settings = get_settings()
    plan = {
        "scenes": [
            {
                "scene_id": "s01",
                "start_sec": 0.0,
                "end_sec": 3.0,
                "layout_id": "character_left_text_right",
                "visual_focus": "character",
                "on_screen_text": "Save first",
                "use_alex": True,
                "alex_pose": "explaining",
                "asset_needs": [
                    {
                        "type": "character",
                        "character": "alex",
                        "pose": "explaining",
                        "tags": ["finance", "host"],
                        "required": True,
                    }
                ],
            },
            {
                "scene_id": "s02",
                "start_sec": 3.0,
                "end_sec": 6.0,
                "layout_id": "icon_explainer",
                "visual_focus": "icon",
                "on_screen_text": "Piggy habit",
                "use_alex": False,
                "asset_needs": [
                    {"type": "icon", "tags": ["savings"], "required": True}
                ],
            },
            {
                "scene_id": "s03",
                "start_sec": 6.0,
                "end_sec": 9.0,
                "layout_id": "fullscreen_statement",
                "visual_focus": "statement",
                "on_screen_text": "Keep going",
                "use_alex": False,
                "asset_needs": [],
            },
        ]
    }
    result = resolve_scene_production_assets(
        plan=plan,
        settings=settings,
        artifact_dir=tmp_path,
        allow_generate=False,
    )
    assert result.status == "ok", result.waiting
    assert "s01" in result.assignments
    assert "s02" in result.assignments
    a1 = result.assignments["s01"]
    a2 = result.assignments["s02"]
    assert a1.production_role == "character_cutout"
    assert a2.production_role == "icon"
    assert a1.asset_id != a2.asset_id
    assert "channel_references" not in a1.source_path.replace("\\", "/")
    assert Image.open(a1.path).mode == "RGBA"

    manifest = scene_plan_to_project_manifest(
        job_id="role-test",
        topic="Save first",
        script="Save first. Piggy habit. Keep going.",
        plan=plan,
        scene_images={sid: row.path for sid, row in result.assignments.items()},
        scene_asset_meta={
            sid: {
                "asset_id": row.asset_id,
                "production_role": row.production_role,
                "channel": row.channel,
                "version": row.version,
                "preserve_alpha": row.preserve_alpha,
                "fit": row.fit,
                "function": row.function,
            }
            for sid, row in result.assignments.items()
        },
        duration_hint=9.0,
    )
    dumped = json.loads(manifest.model_dump_json())
    assert manifest_uses_reference_sheet(dumped) == []
    image_scenes = [s for s in dumped["scenes"] if s["scene_type"] == "ImageScene"]
    assert len(image_scenes) >= 2
    paths = {s["scene_id"]: s["assets"][0]["path"] for s in image_scenes if s["assets"]}
    assert paths["s01"] != paths["s02"]
    roles = {
        s["scene_id"]: s["assets"][0]["provenance"]["generation_parameters"]["production_role"]
        for s in image_scenes
        if s["assets"]
    }
    assert roles["s01"] == "character_cutout"
    assert roles["s02"] == "icon"


def test_missing_character_marks_waiting(tmp_path: Path, monkeypatch):
    from atnos.core.config import get_settings
    from atnos.production.channels.wealth_blueprint import scene_production_assets as mod

    settings = get_settings()

    def _no_hit(*_a, **_k):
        return None

    monkeypatch.setattr(mod, "_select_library_asset", _no_hit)
    monkeypatch.setattr(mod, "_try_acquisition", lambda *_a, **_k: (None, {"reason": "disabled"}))
    plan = {
        "scenes": [
            {
                "scene_id": "s01",
                "start_sec": 0.0,
                "end_sec": 3.0,
                "layout_id": "character_left_text_right",
                "visual_focus": "character",
                "on_screen_text": "Explain this",
                "use_alex": True,
                "alex_pose": "explaining",
                "asset_needs": [
                    {
                        "type": "character",
                        "character": "alex",
                        "pose": "explaining",
                        "required": True,
                    }
                ],
            }
        ]
    }
    result = resolve_scene_production_assets(
        plan=plan,
        settings=settings,
        artifact_dir=tmp_path,
        allow_generate=False,
    )
    assert result.status == "waiting"
    assert result.waiting
    assert result.waiting[0]["code"] == WAITING_FOR_ASSETS


def test_budget_icon_falls_back_to_approved_savings_library_icon(tmp_path: Path):
    """Niche planner tags like 'budget' must not hard-block when a related approved icon exists."""
    from atnos.core.config import get_settings

    settings = get_settings()
    plan = {
        "scenes": [
            {
                "scene_id": "s01",
                "start_sec": 0.0,
                "end_sec": 3.0,
                "layout_id": "fullscreen_statement",
                "visual_focus": "statement",
                "on_screen_text": "Hook",
                "use_alex": False,
            },
            {
                "scene_id": "s02",
                "start_sec": 3.0,
                "end_sec": 7.0,
                "layout_id": "icon_explainer",
                "visual_focus": "icon",
                "on_screen_text": "Review your budget every few weeks",
                "use_alex": False,
                "icon_tags": ["finance"],
                "asset_needs": [
                    {"type": "icon", "tags": ["budget"], "text": "Review your budget", "required": True}
                ],
            },
            {
                "scene_id": "s03",
                "start_sec": 7.0,
                "end_sec": 10.0,
                "layout_id": "fullscreen_statement",
                "visual_focus": "statement",
                "on_screen_text": "Act",
                "use_alex": False,
            },
        ]
    }
    result = resolve_scene_production_assets(
        plan=plan,
        settings=settings,
        artifact_dir=tmp_path,
        allow_generate=False,
    )
    assert result.status == "ok", result.waiting
    assert "s02" in result.assignments
    assert result.assignments["s02"].production_role == "icon"
    assert result.assignments["s02"].asset_id != "icon_piggy_bank_v01"
    assert "illust_" in result.assignments["s02"].asset_id or "habit" in result.assignments["s02"].asset_id
    assert "placeholders/" not in result.assignments["s02"].source_path.replace("\\", "/")
