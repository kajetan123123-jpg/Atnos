"""Regressions: explicit editorial intent, quiet captions and controlled voice trials."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from atnos.production.channels.wealth_blueprint import manifest_adapters as adapters
from atnos.production.channels.wealth_blueprint.design_system import brand_tokens_for_manifest
from atnos.production.channels.wealth_blueprint.voice_audition import audition_variants


def _plan(concept, mode):
    return {'scenes': [
        {'scene_id': f's{i+1:02}', 'start_sec': i*3., 'end_sec': (i+1)*3.,
         'layout_id': 'fullscreen_statement', 'visual_focus': 'statement',
         'on_screen_text': 'Keep part of your raise',
         'narration_excerpt': 'Keep part of every raise for your future.',
         **({'visual_concept': concept} if concept else {}),
         **({'visual_intent': {'scene_mode': mode}} if mode else {})}
        for i in range(3)]}


def _manifest(plan):
    return adapters.scene_plan_to_project_manifest(job_id='intent-regression', topic='Raise',
        script='Keep part of every raise for your future.', plan=plan, duration_hint=9.)


@pytest.mark.parametrize('mode', ['statement', 'illustration', 'character'])
def test_editorial_intent_does_not_become_keyword_diagram(mode):
    manifest = _manifest(_plan('raise_absorption', mode))
    assert all(s.semantic_overlay_kind is None for s in manifest.scenes)
    assert manifest.brand.captionStyle == 'plain_phrase'


def test_explicit_generic_concept_is_not_reinferred_from_narration():
    assert all(s.semantic_overlay_kind is None for s in _manifest(_plan('generic_finance', 'diagram')).scenes)


def test_actual_mechanism_and_legacy_unspecified_plan_keep_supported_diagram():
    for plan in [_plan('raise_absorption', 'diagram'), _plan(None, None)]:
        assert all(s.semantic_overlay_kind == 'raise_absorption' for s in _manifest(plan).scenes)


def test_profile_and_both_adapter_paths_keep_quiet_captions(monkeypatch):
    from atnos.production.channels.wealth_blueprint import design_system
    assert brand_tokens_for_manifest()['captionStyle'] == 'plain_phrase'
    # Even the emergency fallback must not restore karaoke.
    monkeypatch.setattr(design_system, 'brand_tokens_for_manifest', lambda: (_ for _ in ()).throw(ValueError('bad profile')))
    assert _manifest(_plan(None, None)).brand.captionStyle == 'plain_phrase'
    manifest = adapters.order_artifacts_to_project_manifest(job_id='legacy', topic_title='Raise',
        script={'segments': [{'id':'s01', 'text':'Keep part of your raise.'}]},
        voice={'timestamps':[{'segment_id':'s01','start_sec':0.,'end_sec':3.}], 'duration_seconds':3.},
        visuals={'scenes':[{'layout_id':'fullscreen_statement'}]})
    assert manifest.brand.captionStyle == 'plain_phrase'


def test_eric_comparison_changes_only_stability_and_never_mutates_default():
    base={'voice_id':'existing-default','model':'eleven_multilingual_v2','speed':1.12,'stability':.5}
    saved=deepcopy(base)
    variants=audition_variants(base, 'eric-intonation')
    left,right=[config for _,config in variants]
    assert {key for key in left if left[key] != right[key]} == {'stability'}
    assert left['voice_id'] == right['voice_id'] == 'cjVigY5qzO86Huf0OWal'
    assert left['speed'] == right['speed'] == 1.
    assert base == saved
    with pytest.raises(ValueError):
        audition_variants(base, 'unsupported')


def test_eric_model_trial_uses_same_settings_as_saved_baseline():
    base={'model':'eleven_multilingual_v2'}
    baseline=audition_variants(base,'voices')[1][1]
    candidates=audition_variants(base,'eric-model')
    assert len(candidates)==1  # No paid regeneration of the existing baseline.
    trial=candidates[0][1]
    assert {key for key in trial if trial[key]!=baseline[key]}=={'model'}
    assert trial['model']=='eleven_v3'


def test_fragment_uses_real_brief_claims_and_rebases_cues(monkeypatch,tmp_path):
    from atnos.production.channels.wealth_blueprint import preview
    segments=[{'id':f's{i+1:02}','text':'Move money into savings.','on_screen':'Save first','claim_ids':[]} for i in range(6)]
    (tmp_path/'script.json').write_text(json.dumps({'segments':segments,'claims':[]}))
    for segment in segments:
        folder=tmp_path/'audio'/segment['id'];folder.mkdir(parents=True)
        (folder/'voice.json').write_text(json.dumps({'duration_seconds':3.}))
    request={'project':'wealth_blueprint','topic':'Friday transfer','direction':'A transfer, not Pilot A.','optional_notes':'No host required.'}
    monkeypatch.setattr(preview,'_source_order',lambda path,settings:{'request':request})
    captured=[]
    class FakePlanner:
        def __init__(self,settings): pass
        def plan(self,inp):
            captured.append(inp)
            scenes=[]
            for i,segment in enumerate(segments):
                data={'scene_id':segment['id'],'narration_segment_ids':[segment['id']],
                    'start_sec':i*3.,'end_sec':(i+1)*3.,
                    'animations':[{'at_sec':i*3.+.1}],'sound_events':[{'at_sec':i*3.+.2}]}
                scenes.append(SimpleNamespace(scene_id=segment['id'],narration_segment_ids=[segment['id']],
                    model_dump=lambda mode,data=data:deepcopy(data)))
            return SimpleNamespace(scenes=scenes)
    monkeypatch.setattr(preview,'WealthBlueprintScenePlanner',FakePlanner)
    _,_,plan,start=preview.preview_plan(tmp_path,['s03','s04','s05'],settings=object())
    assert captured[0].profile['direction']==request['direction']
    assert captured[0].profile['optional_notes']==request['optional_notes']
    assert start==6.
    assert plan['scenes'][0]['animations'][0]['at_sec']==pytest.approx(.1)
    assert plan['scenes'][0]['sound_events'][0]['at_sec']==pytest.approx(.2)
    assert plan['scenes'][-1]['source_scene_index']==4
    assert plan['scenes'][-1]['source_scene_count']==6


def test_fragment_middle_statement_does_not_turn_into_closing():
    plan=_plan('generic_finance','statement')
    for i,scene in enumerate(plan['scenes']):
        scene.update(source_scene_index=i+1,source_scene_count=6)
    assert _manifest(plan).scenes[-1].scene_type.value=='HookScene'


def test_shot_variety_preserves_actions_and_duplicate_state_still_fails():
    from atnos.production.channels.wealth_blueprint.scene_plan_models import WealthBlueprintScenePlan
    from atnos.production.channels.wealth_blueprint.shot_language import apply_shot_language
    from atnos.production.channels.wealth_blueprint.validation import validate_scene_plan
    scenes=_plan('weekly_transfer','diagram')['scenes']
    for i,scene in enumerate(scenes):
        scene.update(layout_id='icon_explainer',visual_focus='icon',on_screen_text='Save first')
        scene['visual_intent'].update(viewer_should_understand='How savings work',
            visible_change=['Income enters the account','Money moves into savings','The savings stay separate'][i])
    plan=WealthBlueprintScenePlan(script='Set aside money for the future.',duration_sec=9.,scenes=scenes)
    result=apply_shot_language(plan)
    assert [s.layout_id for s in result.scenes]==['icon_explainer']*3
    assert [s.visual_intent.visible_change for s in result.scenes]==[s.visual_intent.visible_change for s in plan.scenes]
    assert not any(i.code=='layout_repeat' for i in validate_scene_plan(result).issues)
    repeated=result.model_copy(deep=True)
    for scene in repeated.scenes:
        scene.visual_intent.visible_change='Money moves into savings'
    assert any(i.code=='layout_repeat' and i.severity=='error' for i in validate_scene_plan(repeated).issues)


def test_preview_rejects_all_cards_before_audio_or_render(monkeypatch,tmp_path):
    from atnos.production.channels.wealth_blueprint import preview
    plan=_plan('generic_finance','statement')
    monkeypatch.setattr(preview,'preview_plan',lambda *a,**kw:({}, {}, plan, 0.))
    monkeypatch.setattr(preview,'assemble_audio',lambda *a,**kw:pytest.fail('Audio should not start'))
    # Preview paths must stay on E: (Atnos policy); keep storyboard gate before audio.
    root = Path(r'E:\Atnos\App\tmp') / f'wb_preview_gate_{tmp_path.name}'
    source = root / 'source'
    output = root / 'output'
    source.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError,match='storyboard gate'):
        preview.render_review_fragment(source, output, settings=object())
    assert (output/'preview-blocked.json').is_file()
