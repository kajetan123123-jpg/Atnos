"""Actual recorded narration, isolated fixtures only; no model or order IO."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from atnos.production.project_manifest import SceneManifest, ProjectManifest,save_project_manifest
from atnos.production.channels.wealth_blueprint.planning_desk_contract import bind_planning_desk_intent
from atnos.production.channels.wealth_blueprint.scene_plan_models import VisualIntent
from atnos.production.channels.wealth_blueprint.scene_relation import parse_scene_relation
from atnos.production.editorial_direction import scene_direction,EDITORIAL_PALETTE
from atnos.production.presenter_variant import bind_editorial_scene_directions,apply_variant
from atnos.production.order_store import ProductionBlocked
from atnos.production.steps.order_delivery import _planning_desk_audit_contract,_remotion_intended_visual

FIXTURES=Path(__file__).parents[2]/'remotion-renderer'/'tests'/'fixtures'

def recorded():
    return [SceneManifest.model_validate(s) for s in json.loads((FIXTURES/'planning-story-directed.json').read_text())]

def manifest():
    return ProjectManifest(manifest_id='isolated',job_id='isolated',project_id='p',account_id='a',topic_id='t',
        topic_title='Recorded planning narration',channel='wealth_blueprint',language_code='en',
        brand_profile_version='test',renderer_version='test',workflow_version='test',voice_version='test',
        status='test',current_step='test',created_at='2026-10-28',updated_at='2026-10-28',scenes=recorded())

def context():
    return NS(artifacts={'project_profile':{'native_illustration_system':'wb_planning_desk_painted_v1'},
        '_renderer_refresh':{'visual_variant':'editorial_motion','scope':'renderer_and_downstream_only',
                             'job_id':'isolated','digest':'a'*64}})

@pytest.mark.parametrize('index,mode',enumerate(['planning_claims','purpose_naming','portion_audit','conditional_availability','goal_review','closing']))
def test_directed_fixtures_match_the_current_factory(index,mode):
    scene=recorded()[index]
    current=scene_direction(scene)
    old=scene.visual_relation['editorial_direction']
    # Version 2 adds attention cues, not an earlier financial consequence.
    assert current['version']==(1 if mode=='closing' else 2)
    assert {k:v for k,v in current.items() if k not in {'version','cue_times','visible_change'}}=={
        k:v for k,v in old.items() if k not in {'version','cue_times','visible_change'}}
    assert all(current['cue_times'][k]==v for k,v in old.get('cue_times',{}).items()) if old.get('cue_times') else True
    assert current['mode']==mode

@pytest.mark.parametrize('index',range(5))
def test_binding_removes_only_the_placeholder_and_preserves_narration(index):
    scene=recorded()[index]
    stage=['identify','separate','review','review','review'][index]
    direction={'identify':'identify (actual need and reserve)','separate':'separate (reserve versus future goal)','review':'review (review both roles)'}[stage]
    relation=parse_scene_relation(scene.narration,visual_context=direction,reserve_context=True)
    before=copy.deepcopy(relation)
    intent=bind_planning_desk_intent(VisualIntent(viewer_should_understand=relation['viewer_should_understand'],relation=relation),scene.narration)
    assert relation==before
    assert not any(o['role']=='expense' for o in intent.relation['objects'])
    assert VisualIntent.model_validate(intent.model_dump()).relation==intent.relation
    if index in (3,4):assert intent.relation['condition'] in scene.narration

def test_real_expense_in_separate_scene_is_not_erased_for_the_editorial_renderer():
    scene=recorded()[1]
    relation=copy.deepcopy(scene.visual_relation)
    relation['objects'].append({'role':'expense','label':'CAR REPAIR'})
    intent=bind_planning_desk_intent(VisualIntent(viewer_should_understand='Real expense',relation=relation),scene.narration)
    assert any(o['label']=='CAR REPAIR' for o in intent.relation['objects'])
    scene.visual_relation=intent.relation
    with pytest.raises(ProductionBlocked,match='not an expense'):scene_direction(scene)

@pytest.mark.parametrize('mutation',['words','overlap','missing-object','number','condition','short'])
def test_no_direction_for_unproven_or_rushed_meaning(mutation):
    s=recorded()[3]
    if mutation=='words':s.caption_words=s.caption_words[:-1]
    if mutation=='overlap':s.caption_words[1].start_sec=s.caption_words[0].start_sec
    if mutation=='missing-object':s.visual_relation['objects']=[]
    if mutation=='number':s.narration+=' $100.'
    if mutation=='condition':s.visual_relation['condition']=None
    if mutation=='short':s.timing.end_sec=s.timing.start_sec+6.8;s.caption_words=[w for w in s.caption_words if w.end_sec<=s.timing.end_sec];s.narration=' '.join(w.text for w in s.caption_words)
    with pytest.raises(ProductionBlocked):scene_direction(s)

def test_availability_effect_waits_for_the_spoken_if_emergency():
    d=scene_direction(recorded()[3])
    assert d['cue_times']['condition']==pytest.approx(5.843)
    assert d['cue_times']['effect']==pytest.approx(6.293)
    assert d['duration_sec']-d['cue_times']['effect']>.6+.8
    assert 0<=d['cue_times']['source_context']<d['cue_times']['claim_context']<d['cue_times']['condition']

def test_registered_presentation_reuses_pure_binding_without_mutation_or_claim_changes():
    original=manifest();before=original.model_dump(mode='json');ex=NS(context=context())
    bound=apply_variant(ex,original)
    assert original.model_dump(mode='json')==before
    assert bound==bind_editorial_scene_directions(original)
    assert [s.narration for s in bound.scenes]==[s.narration for s in original.scenes]
    assert bound.calculation_claims==original.calculation_claims
    assert bound.brand.captionStyle=='plain_phrase'
    assert {k:getattr(bound.brand,k) for k in EDITORIAL_PALETTE}==EDITORIAL_PALETTE
    ex.context.artifacts.pop('_renderer_refresh')
    assert apply_variant(ex,original) is original

@pytest.mark.parametrize('index',range(6))
def test_current_registered_infographic_is_judged_as_its_real_medium_not_painted_hands(index):
    bound=bind_editorial_scene_directions(manifest());bound.brand.illustrationSystem='wb_planning_desk_painted_v1'
    contract=_planning_desk_audit_contract(context(),bound,bound.scenes[index])
    assert contract['presentation_variant']=='editorial_motion'
    assert contract['logo_only']==(index==5)
    assert 'No invented amounts' in contract['materials']
    assert 'PASS' not in contract and 'status' not in contract
    assert contract['speech_timing']['source']=='ProjectManifest.caption_words'
    if index<5:assert 'not cash transfers' in _remotion_intended_visual(bound.scenes[index])

@pytest.mark.parametrize('mutation',['registration','scope','job','digest','palette','cue','narration'])
def test_qa_contract_rejects_stale_or_unregistered_presentation(mutation):
    ctx=context();bound=bind_editorial_scene_directions(manifest());bound.brand.illustrationSystem='wb_planning_desk_painted_v1'
    if mutation=='registration':ctx.artifacts.pop('_renderer_refresh')
    if mutation=='scope':ctx.artifacts['_renderer_refresh']['scope']='full_av'
    if mutation=='job':ctx.artifacts['_renderer_refresh']['job_id']='other'
    if mutation=='digest':ctx.artifacts['_renderer_refresh']['digest']='stale'
    if mutation=='palette':bound.brand.background='#000000'
    if mutation=='cue':bound.scenes[0].visual_relation['editorial_direction']['risk_sec']=0
    if mutation=='narration':bound.scenes[0].narration+=' Invented.'
    with pytest.raises((ValueError,ProductionBlocked)):_planning_desk_audit_contract(ctx,bound,bound.scenes[0])

def test_old_painted_profile_keeps_its_original_quality_contract():
    original=manifest();original.brand.illustrationSystem='wb_planning_desk_painted_v1'
    original.scenes[0].semantic_overlay_kind='reserve_readiness'
    contract=_planning_desk_audit_contract(context(),original,original.scenes[0])
    assert 'painted wall' in contract['materials']
    assert 'presentation_variant' not in contract

@pytest.mark.parametrize('tamper',[False,True])
def test_full_scene_target_path_accepts_a_real_claims_hook_without_inventing_an_expense(tmp_path,tamper):
    from atnos.production.steps.order_delivery import resolve_semantic_scene_targets
    bound=bind_editorial_scene_directions(manifest());bound.brand.illustrationSystem='wb_planning_desk_painted_v1'
    for scene in bound.scenes[:-1]:scene.semantic_overlay_kind='reserve_readiness'
    ctx=context();ctx.artifact_dir=tmp_path
    ctx.artifacts['project_profile']['production']={'visual_rules':[]}
    ctx.artifacts['production_order']={'topic':'Emergency reserve planning'}
    ctx.artifacts['script']={'segments':[{'id':s.scene_id,'text':s.narration,'visual_intent':
        ('identify (actual need and reserve)' if i==0 else 'separate (reserve versus future goal)' if i==1 else 'review (review both roles)' if i<5 else '')}
        for i,s in enumerate(bound.scenes)]}
    if tamper:bound.scenes[0].visual_relation['objects'][1]['label']='OTHER RESERVE'
    plan=tmp_path/'project_manifest.json';save_project_manifest(plan,bound)
    video=tmp_path/'fixture.mp4';video.write_bytes(b'isolated target resolution only, not an AV pass')
    ctx.artifacts['edit']={'renderer':'remotion','manifest_path':str(plan),'output_file':str(video)}
    if tamper:
        with pytest.raises(ValueError,match='object mismatch'):resolve_semantic_scene_targets(ctx)
    else:
        targets=resolve_semantic_scene_targets(ctx)
        assert len(targets)==6
        assert 'Audio-bound qualitative editorial infographic' in targets[0]['contract']
        assert 'painted hands' in targets[0]['audit_instructions']
        assert 'no transfer' not in targets[0]['contract'].lower() # claims are not an invented transfer instruction
        assert 'final annotation is expected' not in targets[0]['audit_instructions']
        assert 'status' not in targets[0]  # resolving targets is not a quality verdict
        conditional=bound.scenes[3];d=scene_direction(conditional)
        samples=targets[3]['sample_frame_indices']
        assert len(samples)==len(set(samples))==6 and samples==sorted(samples)
        relative=[n/30-conditional.timing.start_sec for n in samples]
        assert any(d['cue_times']['condition']<t<d['cue_times']['effect'] for t in relative)
        assert any(t>d['cue_times']['effect']+.5 for t in relative)

def test_registered_medium_resolves_only_the_known_style_rule_and_keeps_safety_rules():
    from atnos.production.steps.order_delivery import _scene_contract
    old='Hand-drawn mature editorial + blueprint language: navy, paper cream, muted teal, sparse warm gold.'
    rules=[old,'No invented numbers.','Hold graphs on consistent axes.']
    kwargs=dict(narration='Actual text',intended_visual='Actual action',scene_type='HookScene',on_screen='Actual thought',visual_rules=rules)
    raw=json.loads(_scene_contract(context(),'s01',**kwargs))
    assert raw['rules']==rules
    resolved=json.loads(_scene_contract(context(),'s01',**kwargs,native_illustration={'presentation_variant':'editorial_motion'}))
    assert resolved['rules'][1:]==rules[1:] and rules[0]==old
    assert 'Registered editorial_motion' in resolved['rules'][0]
    assert old not in resolved['rules']
