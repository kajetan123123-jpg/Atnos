"""Regression: production path must not select geometric placeholder catalog entries."""

from __future__ import annotations

from pathlib import Path

import pytest

from atnos.production.asset_brain.catalog import load_catalog
from atnos.production.asset_brain.models import AssetQuery
from atnos.production.asset_brain.service import WealthBlueprintAssetBrain
from atnos.production.channels.wealth_blueprint.geometric_assets import (
    GEOMETRIC_PLACEHOLDER_ASSET_IDS,
    is_geometric_placeholder_asset,
)
from atnos.production.channels.wealth_blueprint.scene_production_assets import (
    assert_production_asset,
    resolve_scene_production_assets,
)
from atnos.production.channels.wealth_blueprint.visual_concepts import (
    concept_prefers_icon_explainer,
    infer_visual_concept,
)
from atnos.production.project_manifest import infer_semantic_overlay_kind


@pytest.fixture()
def brain() -> WealthBlueprintAssetBrain:
    return WealthBlueprintAssetBrain(catalog=load_catalog())


def test_known_geometric_placeholders_not_production_selectable(brain: WealthBlueprintAssetBrain):
    for asset_id in sorted(GEOMETRIC_PLACEHOLDER_ASSET_IDS):
        asset = brain.get(asset_id)
        if asset is None:
            continue
        assert is_geometric_placeholder_asset(asset) is True
        assert asset.is_production_selectable() is False
        with pytest.raises(ValueError, match="geometric_placeholder|not APPROVED"):
            assert_production_asset(asset)


def test_explain_scenes_reject_geometric_catalog_and_bind_meaning(tmp_path: Path, brain: WealthBlueprintAssetBrain):
    from atnos.core.config import get_settings

    settings = get_settings()
    plan = {
        "scenes": [
            {
                "scene_id": "s_savings",
                "start_sec": 0.0,
                "end_sec": 3.5,
                "layout_id": "icon_explainer",
                "visual_focus": "icon",
                "on_screen_text": "Choose a separate savings account",
                "narration_excerpt": "Choose a separate savings account.",
                "icon_tags": ["savings", "account", "choose", "finance"],
                "use_alex": False,
                "asset_needs": [
                    {
                        "type": "icon",
                        "tags": ["savings", "account", "choose", "finance", "money"],
                        "text": "Choose a separate savings account",
                        "required": True,
                    }
                ],
            },
            {
                "scene_id": "s_transfer",
                "start_sec": 3.5,
                "end_sec": 7.0,
                "layout_id": "icon_explainer",
                "visual_focus": "icon",
                "on_screen_text": "Turn on automatic weekly transfers",
                "narration_excerpt": "Turn on automatic weekly transfers so saving happens without willpower.",
                "icon_tags": ["transfer", "weekly", "automatic", "finance"],
                "use_alex": False,
                "asset_needs": [
                    {
                        "type": "icon",
                        "tags": ["transfer", "weekly", "automatic", "savings", "finance"],
                        "text": "Turn on automatic weekly transfers",
                        "required": True,
                    }
                ],
            },
            {
                "scene_id": "s_alex_demote",
                "start_sec": 7.0,
                "end_sec": 10.0,
                "layout_id": "character_left_text_right",
                "visual_focus": "character",
                "on_screen_text": "Save first automatically",
                "narration_excerpt": "Alex explains why you should save first automatically.",
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
        ]
    }
    result = resolve_scene_production_assets(
        plan=plan,
        settings=settings,
        artifact_dir=tmp_path,
        allow_generate=False,
    )
    assert result.status == "ok", result.waiting
    for scene_id, row in result.assignments.items():
        assert row.asset_id not in GEOMETRIC_PLACEHOLDER_ASSET_IDS, scene_id
        assert "placeholders/" not in row.source_path.replace("\\", "/")
        assert not is_geometric_placeholder_asset({"id": row.asset_id, "file_path": row.source_path})

    assert "illust_savings" in result.assignments["s_savings"].asset_id or "savings" in result.assignments["s_savings"].asset_id
    assert "transfer" in result.assignments["s_transfer"].asset_id or "illust_weekly" in result.assignments["s_transfer"].asset_id


def test_visual_concept_and_overlay_bind_to_narration_meaning():
    assert infer_visual_concept("Choose a separate savings account.") == "savings_account"
    assert concept_prefers_icon_explainer("savings_account") is True
    assert infer_semantic_overlay_kind("Choose a separate savings account.") == "savings_account"

    assert infer_visual_concept("Turn on automatic weekly transfers.") == "weekly_transfer"
    assert infer_semantic_overlay_kind("Turn on automatic weekly transfers so saving happens without willpower.") == (
        "weekly_transfer"
    )


def test_select_best_icon_skips_rejected_geometric(brain: WealthBlueprintAssetBrain):
    result = brain.select_best(
        AssetQuery(
            type="icon",
            tags_any=["savings", "finance", "money"],
            require_approved=True,
            production_role="icon",
        )
    )
    assert result.status == "found"
    assert result.match is not None
    assert result.match.asset.id not in GEOMETRIC_PLACEHOLDER_ASSET_IDS
    assert result.match.asset.source != "library_placeholder"
