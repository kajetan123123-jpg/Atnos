"""Regression: weekly-transfer beats must not select geometric placeholders; comparison keeps claims."""

from __future__ import annotations

from pathlib import Path

import pytest

from atnos.core.config import get_settings
from atnos.production.channels.wealth_blueprint.deterministic import classify_focus, plan_scenes_deterministic
from atnos.production.channels.wealth_blueprint.geometric_assets import GEOMETRIC_PLACEHOLDER_ASSET_IDS
from atnos.production.channels.wealth_blueprint.scene_plan_models import NarrationSegment, ScenePlannerInput
from atnos.production.channels.wealth_blueprint.scene_production_assets import resolve_scene_production_assets


def test_weekly_transfer_focus_is_icon_explainer():
    focus, layout = classify_focus("Turn on automatic weekly transfers so saving happens without willpower.")
    assert focus == "icon"
    assert layout == "icon_explainer"


def test_early_late_saver_is_comparison_not_number():
    focus, layout = classify_focus(
        "Here is a worked example: early saver reaches twenty four thousand dollars, "
        "late saver reaches twelve thousand dollars."
    )
    assert focus == "comparison"
    assert layout == "comparison"


def test_resolve_rejects_piggy_for_weekly_transfer(tmp_path: Path):
    settings = get_settings()
    plan = {
        "scenes": [
            {
                "scene_id": "s_transfer",
                "layout_id": "icon_explainer",
                "visual_focus": "icon",
                "on_screen_text": "Turn on automatic weekly transfers",
                "narration_excerpt": "Turn on automatic weekly transfers.",
                "icon_tags": ["transfer", "weekly", "automatic", "savings"],
                "use_alex": False,
                "asset_needs": [
                    {"type": "icon", "tags": ["transfer", "weekly", "automatic", "savings"], "required": True}
                ],
            }
        ]
    }
    result = resolve_scene_production_assets(
        plan=plan, settings=settings, artifact_dir=tmp_path, allow_generate=False
    )
    assert result.status == "ok"
    row = result.assignments["s_transfer"]
    assert row.asset_id not in GEOMETRIC_PLACEHOLDER_ASSET_IDS
    assert "illust" in row.asset_id or "weekly" in row.asset_id


def test_planner_binds_comparison_claims_and_alex_explain():
    segs = [
        NarrationSegment(
            id="sA",
            text="Turn on automatic weekly transfers so money lands in savings every week.",
            start=0.0,
            end=4.0,
        ),
        NarrationSegment(
            id="sB",
            text=(
                "Here is a worked example: early saver reaches twenty four thousand dollars (24000 dollars), "
                "late saver reaches twelve thousand dollars (12000 dollars)."
            ),
            start=4.0,
            end=9.0,
        ),
        NarrationSegment(
            id="sC",
            text="Alex explains the transfer arrow and points to the savings jar.",
            start=9.0,
            end=13.0,
        ),
    ]
    claims = [
        {
            "id": "c_early",
            "operation": "product",
            "operands": [100.0, 240.0],
            "result": 24000.0,
            "unit": "dollars",
            "spoken_result": "twenty four thousand dollars",
            "assumptions": "Worked example monthly contribution over two hundred forty months.",
        },
        {
            "id": "c_late",
            "operation": "product",
            "operands": [100.0, 120.0],
            "result": 12000.0,
            "unit": "dollars",
            "spoken_result": "twelve thousand dollars",
            "assumptions": "Worked example monthly contribution over one hundred twenty months.",
        },
    ]
    plan = plan_scenes_deterministic(
        ScenePlannerInput(
            script=" ".join(s.text for s in segs),
            segments=segs,
            profile={"topic": "Alex walks through weekly transfers", "direction": "host guide"},
            claims=claims,
            segment_claim_map={"sB": ["c_early", "c_late"]},
        )
    )
    by_id = {s.scene_id: s for s in plan.scenes}
    assert by_id["s01"].visual_concept == "weekly_transfer"
    assert by_id["s01"].layout_id == "icon_explainer"
    assert by_id["s02"].visual_focus == "comparison"
    assert by_id["s02"].chart is not None and by_id["s02"].chart.is_quantitative
    assert by_id["s02"].use_alex is False
    assert by_id["s03"].use_alex is True
    assert by_id["s03"].layout_id == "split_screen"
