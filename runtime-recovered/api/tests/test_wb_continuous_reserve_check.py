"""Recorded duplicate-review failure; no fixture is a quality approval."""
import json
from pathlib import Path
import pytest
from atnos.production.channels.wealth_blueprint.deterministic import _merge_repeated_reserve_checks
from atnos.production.channels.wealth_blueprint.planner import WealthBlueprintScenePlanner
from atnos.production.channels.wealth_blueprint.scene_plan_models import NarrationSegment, ScenePlannerInput
from atnos.production.channels.wealth_blueprint.manifest_adapters import scene_plan_to_project_manifest


def recorded_plan():
    script = json.loads((Path(__file__).parent / 'fixtures/wb_batch_20261013/duplicate-review.json').read_text('utf8'))
    offset, segments = 0., []
    for item in script['segments']:
        end = offset + len(item['text'].split()) / 2.3
        segments.append(NarrationSegment(id=item['id'], text=item['text'], start=offset, end=end,
                         on_screen=item['on_screen'], visual_intent=item['visual_intent']))
        offset = end
    return script, WealthBlueprintScenePlanner().plan(ScenePlannerInput(script=script['text'], segments=segments))


def test_actual_duplicate_check_becomes_one_shot_preserving_complete_speech():
    script, plan = recorded_plan()
    assert plan.validation.ok
    assert len(plan.scenes) == 5
    scene = next(s for s in plan.scenes if s.narration_segment_ids == ['s03', 's04'])
    assert scene.narration_excerpt == ' '.join(s['text'] for s in script['segments'][2:4])
    assert scene.meaning_contract['narration_segment_ids'] == ['s03', 's04']
    assert [sid for s in plan.scenes for sid in s.narration_segment_ids] == [s['id'] for s in script['segments']]
    manifest = scene_plan_to_project_manifest(job_id='isolated-continuous-review', topic=script['title'],
        script=script['text'], plan=plan.model_dump(mode='json'), duration_hint=plan.duration_sec)
    native = next(s for s in manifest.scenes if s.visual_relation and s.visual_relation.get('stage') == 'review')
    assert native.semantic_overlay_kind == 'reserve_readiness'


@pytest.mark.parametrize('difference', ['headline', 'role', 'focus', 'gap'])
def test_distinct_information_and_non_contiguous_shots_are_not_merged(difference):
    _, plan = recorded_plan()
    left = next(s for s in plan.scenes if s.narration_segment_ids == ['s03', 's04'])
    right = left.model_copy(deep=True, update={'start_sec': left.end_sec, 'end_sec': left.end_sec + 5,
                                             'narration_segment_ids': ['different']})
    if difference == 'headline': right.on_screen_text = 'A Different Question'
    if difference == 'role': right.visual_intent.relation['objects'][0]['label'] = 'HOME REPAIR'
    if difference == 'focus': right.visual_intent.relation['access_focus'] = 'terms'
    if difference == 'gap': right.start_sec += .1
    assert len(_merge_repeated_reserve_checks([left, right])) == 2
