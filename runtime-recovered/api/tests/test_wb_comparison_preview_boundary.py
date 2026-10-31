import pytest
from atnos.production.channels.wealth_blueprint.manifest_adapters import scene_plan_to_project_manifest


def plan():
    return {'scenes':[{'scene_id':f's{i}', 'start_sec':i*3, 'end_sec':(i+1)*3,
        'layout_id':'fullscreen_statement','visual_focus':'statement',
        'on_screen_text':'A complete thought','narration_excerpt':'A complete thought.'}
        for i in range(2)]}


def adapt(candidate):
    return scene_plan_to_project_manifest(job_id='fixture',topic='fixture',script='A complete thought.',
        plan=plan(),duration_hint=6,candidate_preview=candidate)


def test_two_scene_review_remains_explicitly_unapproved_candidate():
    manifest=adapt(True)
    assert manifest.review_mode=='candidate_preview'
    assert len(manifest.scenes)==2


def test_production_minimum_is_preserved():
    with pytest.raises(ValueError,match='too short'):
        adapt(False)


def test_short_preview_cannot_skip_an_unsupported_visual_intent():
    from atnos.production.channels.wealth_blueprint.visual_decision import storyboard_blocks_render
    short={'scenes':[{'scene_id':'s1','layout_id':'fullscreen_statement',
        'visual_intent':{'dominant_intent':'COMPARISON','layout_family':'MECHANISM'}}]}
    assert 'VISUAL_INTENT_UNSUPPORTED' in storyboard_blocks_render(short)
