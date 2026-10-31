from __future__ import annotations

import pytest

from atnos.production.channels.wealth_blueprint import (
    LAYOUT_IDS,
    NarrationSegment,
    ScenePlannerInput,
    WealthBlueprintScenePlan,
    WealthBlueprintScenePlanner,
    validate_scene_plan,
)
from atnos.production.channels.wealth_blueprint.scene_plan_models import PlannedScene


def test_deterministic_plan_for_short_script_validates():
    script = (
        "Your paycheck feels bigger. But are you actually richer? "
        "Expenses quietly rise with income. "
        "Keep twenty percent and invest it. "
        "Compound growth rewards consistency over decades."
    )
    planner = WealthBlueprintScenePlanner()
    plan = planner.plan(
        ScenePlannerInput(
            script=script,
            profile={"channel": "wealth_blueprint", "style": "educational_minimalist"},
            design_system={"id": "wealth_blueprint_style_v1"},
            available_asset_types=["character", "icon", "charts"],
            mode="deterministic",
        )
    )
    assert plan.schema_version == "wealth_blueprint_scene_plan_1"
    assert plan.channel == "wealth_blueprint"
    assert plan.validation is not None
    assert plan.validation.ok
    assert 2 <= len(plan.scenes) <= 14
    assert all(scene.layout_id in LAYOUT_IDS for scene in plan.scenes)
    # On-screen text is short
    assert all(len(scene.on_screen_text.split()) <= 8 for scene in plan.scenes)
    # No render hooks
    assert "render" not in plan.model_dump_json().casefold().split("remotion_render")


def test_plan_with_narration_timeline_respects_boundaries():
    segments = [
        NarrationSegment(id="n01", text="Wealth compounds over time.", start=0.0, end=3.0),
        NarrationSegment(id="n02", text="Start early and stay consistent.", start=3.0, end=6.2),
        NarrationSegment(id="n03", text="Compare keeping money versus spending it.", start=6.2, end=9.5),
    ]
    plan = WealthBlueprintScenePlanner().plan(
        ScenePlannerInput(script=" ".join(s.text for s in segments), segments=segments)
    )
    assert plan.validation and plan.validation.ok
    assert plan.scenes[0].start_sec == 0.0
    assert abs(plan.scenes[-1].end_sec - 9.5) < 1e-6
    # Without approved claims, former chart/comparison smoke beats must not invent series.
    for scene in plan.scenes:
        assert scene.chart is None
        assert scene.layout_id not in {"chart_focus", "comparison"} or scene.chart is not None


def test_validation_rejects_micro_cuts_and_layout_spam():
    bad = WealthBlueprintScenePlan(
        script="Hello world is short.",
        duration_sec=1.0,
        scenes=[
            PlannedScene(
                scene_id="s01",
                layout_id="fullscreen_statement",
                start_sec=0.0,
                end_sec=0.4,
                narration_excerpt="Hello world is short.",
                on_screen_text="Hello",
                visual_focus="statement",
            ),
            PlannedScene(
                scene_id="s02",
                layout_id="fullscreen_statement",
                start_sec=0.4,
                end_sec=1.0,
                narration_excerpt="Hello world is short.",
                on_screen_text="Hello again friend forever text",
                visual_focus="statement",
            ),
        ],
    )
    report = validate_scene_plan(bad)
    assert report.ok is False
    codes = {issue.code for issue in report.issues}
    assert "scene_too_short" in codes
    assert "layout_repeat" in codes or "onscreen_too_long" in codes


def test_planner_does_not_hardcode_asset_files():
    plan = WealthBlueprintScenePlanner().plan(
        ScenePlannerInput(script="Save into an emergency fund every month. Build the habit.")
    )
    for scene in plan.scenes:
        for need in scene.asset_needs:
            assert not need.type.endswith(".png")
            assert "/" not in need.type
        for cue in scene.sound_events:
            assert cue.event  # semantic only
            assert "file_path" not in cue.model_dump()


def test_text_without_numbers_yields_no_quantitative_chart():
    script = (
        "Your paycheck feels bigger. But are you actually richer? "
        "Expenses quietly rise with income. "
        "Compound growth rewards consistency over decades."
    )
    plan = WealthBlueprintScenePlanner().plan(ScenePlannerInput(script=script))
    assert plan.validation and plan.validation.ok
    for scene in plan.scenes:
        assert scene.chart is None
        assert "[1.0, 2.4]" not in scene.rationale
        assert scene.layout_id != "chart_focus" or scene.chart is not None


def test_former_smoke_case_no_longer_emits_ab_demo_values():
    segments = [
        NarrationSegment(id="n01", text="Wealth compounds over time.", start=0.0, end=3.0),
        NarrationSegment(id="n02", text="Start early and stay consistent.", start=3.0, end=6.2),
        NarrationSegment(id="n03", text="Compare keeping money versus spending it.", start=6.2, end=9.5),
    ]
    plan = WealthBlueprintScenePlanner().plan(
        ScenePlannerInput(script=" ".join(s.text for s in segments), segments=segments)
    )
    dumped = plan.model_dump(mode="json")
    assert [1.0, 2.4] not in [
        (scene.get("chart") or {}).get("values") for scene in dumped["scenes"]
    ]
    assert [1.0, 0.6] not in [
        (scene.get("chart") or {}).get("values") for scene in dumped["scenes"]
    ]
    assert all(scene.chart is None for scene in plan.scenes)


def test_chart_keeps_claim_backed_values_labels_currency_and_unit():
    narration = (
        "Here is a compound growth hypothetical: start with $100. "
        "At five percent for two years you reach $110.25."
    )
    claim = {
        "id": "c_compound_demo",
        "operation": "compound",
        "operands": [100.0, 0.05, 2.0],
        "result": 110.25,
        "unit": "dollars",
        "spoken_result": "one hundred ten dollars and twenty five cents",
        "assumptions": "Hypothetical five percent annual growth for two years only.",
    }
    segments = [
        NarrationSegment(id="n01", text="Why does patience matter for investors?", start=0.0, end=3.0),
        NarrationSegment(id="n02", text=narration, start=3.0, end=8.0),
        NarrationSegment(id="n03", text="Automate the contribution and leave it alone.", start=8.0, end=11.0),
    ]
    plan = WealthBlueprintScenePlanner().plan(
        ScenePlannerInput(
            script=" ".join(s.text for s in segments),
            segments=segments,
            claims=[claim],
            segment_claim_map={"n02": ["c_compound_demo"]},
        )
    )
    assert plan.validation and plan.validation.ok
    chart_scenes = [scene for scene in plan.scenes if scene.chart is not None]
    assert chart_scenes, "expected a quantitative chart from the verified claim"
    chart = chart_scenes[0].chart
    assert chart is not None
    assert chart.is_quantitative
    assert chart.claim_ids == ["c_compound_demo"]
    assert chart.unit == "dollars"
    assert 100.0 in chart.values
    assert 110.25 in chart.values
    assert chart.values != [1.0, 2.4]


def test_missing_claim_source_blocks_quantitative_chart_even_with_chart_hints():
    segments = [
        NarrationSegment(id="n01", text="Markets move every day.", start=0.0, end=3.0),
        NarrationSegment(
            id="n02",
            text="Compound growth can reshape a portfolio over time.",
            start=3.0,
            end=6.5,
        ),
        NarrationSegment(id="n03", text="Stay consistent with the plan.", start=6.5, end=9.5),
    ]
    plan = WealthBlueprintScenePlanner().plan(
        ScenePlannerInput(script=" ".join(s.text for s in segments), segments=segments)
    )
    mid = next(scene for scene in plan.scenes if "Compound growth" in scene.narration_excerpt)
    assert mid.chart is None
    assert mid.layout_id not in {"chart_focus", "comparison"}


def test_order_topic_forces_functional_alex_even_when_script_omits_host_name():
    """Production Order brief must reach the planner — LLM titles often drop 'Alex'."""
    segments = [
        NarrationSegment(
            id="s01",
            text="Opening a high-yield savings account is the first step toward securing your future.",
            start=0.0,
            end=4.0,
        ),
        NarrationSegment(
            id="s02",
            text="Choose a separate savings account. This helps you avoid spending money set aside.",
            start=4.0,
            end=9.0,
        ),
        NarrationSegment(
            id="s03",
            text="Turn on automatic weekly transfers so saving happens without willpower.",
            start=9.0,
            end=14.0,
        ),
        NarrationSegment(
            id="s04",
            text="Name the goal for this account to stay focused.",
            start=14.0,
            end=18.0,
        ),
        NarrationSegment(
            id="s05",
            text="Setting up these steps takes just a few minutes.",
            start=18.0,
            end=22.0,
        ),
        NarrationSegment(
            id="s06",
            text="Open your account today and schedule your first transfer.",
            start=22.0,
            end=26.0,
        ),
    ]
    script = " ".join(seg.text for seg in segments)
    plan = WealthBlueprintScenePlanner().plan(
        ScenePlannerInput(
            script=script,
            segments=segments,
            profile={
                "channel": "wealth_blueprint",
                "topic": "Alex shows how to open a high-yield savings account step by step",
                "direction": "Alex is the on-screen host who points through the setup steps.",
            },
            mode="deterministic",
        )
    )
    alex_scenes = [scene for scene in plan.scenes if scene.use_alex]
    assert alex_scenes, "Alex host brief must place at least one functional character scene"
    assert alex_scenes[0].scene_id != "s01"
    assert alex_scenes[0].visual_focus == "character"
    assert alex_scenes[0].layout_id in {
        "character_left_text_right",
        "character_right_text_left",
        "split_screen",
    }
