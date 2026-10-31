from atnos.production.channels.wealth_blueprint.deterministic import plan_scenes_deterministic
from atnos.production.channels.wealth_blueprint.scene_plan_models import NarrationSegment, ScenePlannerInput
from atnos.production.channels.wealth_blueprint.shot_language import headline_is_dangling
from atnos.production.channels.wealth_blueprint.validation import validate_scene_plan


def test_shot_language_varies_density_and_keeps_plan_valid():
    segments = [
        NarrationSegment(id="s01", text="A higher paycheck does not automatically build wealth.", start=0, end=4),
        NarrationSegment(id="s02", text="New expenses can absorb the raise before savings.", start=4, end=9),
        NarrationSegment(id="s03", text="Set aside a slice of the raise before you spend it.", start=9, end=14),
    ]
    plan = plan_scenes_deterministic(
        ScenePlannerInput(script=" ".join(s.text for s in segments), segments=segments)
    )
    classes = [scene.shot_class for scene in plan.scenes]
    densities = {scene.visual_density for scene in plan.scenes}
    assert classes[0] == "HERO"
    assert "ACTION_STEP" in classes
    assert len(densities) >= 2
    assert validate_scene_plan(plan).ok
    assert not any(headline_is_dangling(scene.on_screen_text) for scene in plan.scenes)
