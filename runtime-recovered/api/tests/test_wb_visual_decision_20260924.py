from atnos.production.channels.wealth_blueprint.deterministic import plan_scenes_deterministic
from atnos.production.channels.wealth_blueprint.scene_plan_models import NarrationSegment, ScenePlannerInput
from atnos.production.channels.wealth_blueprint.validation import validate_scene_plan
from atnos.production.channels.wealth_blueprint.visual_decision import (
    decide_visual,
    storyboard_blocks_render,
)


def test_transfer_and_raise_are_mechanisms_not_the_same_card():
    transfer = decide_visual(
        "Move money automatically from checking to savings every Friday.",
        concept="weekly_transfer",
    )
    raise_habit = decide_visual(
        "Keep part of every raise instead of spending the whole increase.",
        concept="raise_absorption",
    )
    assert transfer["layout_family"] == "MECHANISM"
    assert transfer["dominant_intent"] == "TRANSFER"
    assert raise_habit["dominant_intent"] == "SPLIT"
    assert raise_habit["layout_family"] == "MECHANISM"
    assert transfer["motion"] == "movement"
    assert raise_habit["motion"] == "division"


def test_storyboard_gate_stops_a_film_of_statement_cards():
    blocked = storyboard_blocks_render(
        {
            "scenes": [
                {"scene_id": "s01", "layout_id": "fullscreen_statement"},
                {"scene_id": "s02", "layout_id": "quote_or_fact"},
                {"scene_id": "s03", "layout_id": "three_points"},
            ]
        }
    )
    assert blocked and "storyboard gate" in blocked
    assert storyboard_blocks_render(
        {
            "scenes": [
                {"scene_id": "s01", "layout_id": "fullscreen_statement"},
                {"scene_id": "s02", "layout_id": "icon_explainer"},
                {"scene_id": "s03", "layout_id": "big_number"},
            ]
        }
    ) is None


def test_planner_keeps_transfer_and_raise_as_diagrams():
    segments = [
        NarrationSegment(id="s01", text="Your paycheck can feel bigger.", start=0, end=4),
        NarrationSegment(
            id="s02",
            text="Turn on an automatic weekly transfer so money leaves checking on Friday and lands in savings.",
            start=4,
            end=10,
        ),
        NarrationSegment(
            id="s03",
            text="Keep part of every raise. Spend the rest.",
            start=10,
            end=15,
        ),
    ]
    plan = plan_scenes_deterministic(
        ScenePlannerInput(script=" ".join(s.text for s in segments), segments=segments)
    )
    families = [scene.visual_intent.layout_family for scene in plan.scenes if scene.visual_intent]
    assert "MECHANISM" in families
    assert validate_scene_plan(plan).ok
    assert storyboard_blocks_render(plan.model_dump(mode="json")) is None
