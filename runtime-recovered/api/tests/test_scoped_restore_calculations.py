import copy
from pathlib import Path
from types import SimpleNamespace
import pytest
from atnos.production.calculation_plan import CalculationPlan, compile_plan
from atnos.production.order_store import OrderConflict
from atnos.production.scoped_script_revision import ScriptFeedback, restored_calculations, validate_restored_claims, bind_restored_calculation_ids
from atnos.production.steps.order_script import NumericClaim
from atnos.production.steps.common import write_json
from test_production_order_store import store, create
from test_scoped_script_revision import original_order,feedback
from test_production_order_script import script


def test_cached_accepted_revision_validates_editorial_fields_without_rejecting_qa_envelope(script,tmp_path,monkeypatch):
    import asyncio
    from atnos.production import scoped_script_revision as module
    restored=compile_plan(plan(),'dollars')['claims']
    script.claims=[NumericClaim.model_validate(restored['c1'])]
    script.segments[4].claim_ids=['c1']
    payload={**script.model_dump(mode='json'),'text':'Archived narration','word_count':100,
             'qa':{'status':'passed'},'provenance':{'archived':True}}
    feedback={'mode':'revise_existing','feedback_hash':'a'*64,'original_script_hash':'b'*64,
              'feedback':{'restore_calculation_ids':['c1']}}
    state={'checkpoints':[{'stage':'script_scoped/'+('a'*12)+'/accepted','status':'done',
                           'files_json':'[]','output':payload}]}
    monkeypatch.setattr(module,'load_feedback',lambda *_:feedback)
    monkeypatch.setattr(module,'_attach_guidance',lambda *_:None)
    monkeypatch.setattr(module,'restored_calculations',lambda *_:restored)
    class ReachedNormalActivation(Exception):pass
    async def run(stage,*args,**kwargs):
        assert stage=='script'
        raise ReachedNormalActivation()
    executor=SimpleNamespace(job_id='isolated-fixture',context=SimpleNamespace(artifact_dir=tmp_path,artifacts={}),
        store=SimpleNamespace(settings=None,get=lambda _:state,_valid_files=lambda *_:True),run=run)
    with pytest.raises(ReachedNormalActivation):asyncio.run(module.produce_requested(executor))


def test_ordinary_numeric_canonical_cannot_enter_qualitative_feedback_without_prior_scope(store,original_order,feedback):
    from atnos.production.scoped_script_revision import register_feedback
    from atnos.production.project_profile import content_hash
    job,payload=original_order
    payload=copy.deepcopy(payload)
    payload['claims']=list(compile_plan(plan(),'dollars')['claims'].values())
    payload['segments'][-2]['claim_ids']=['c1']
    payload['qa']['script_hash']=content_hash({k:payload[k] for k in ('title','description','segments','claims')})
    path=store.context(job).artifact_dir/'script.json'
    with store.lease(job) as token:
        store.begin(job,token,'script',{'fixture':'numeric canonical without restored scope'})
        write_json(path,payload);store.complete(job,token,'script',payload,[path])
    before=path.read_bytes()
    with pytest.raises(OrderConflict,match='immutable brief'):
        register_feedback(store.settings,job,feedback,approved=True)
    assert path.read_bytes()==before


def test_restored_scene_decoding_keeps_exact_cta_and_claim_in_its_own_branch():
    from atnos.production.scoped_script_revision import bind_restored_scene_schema
    from atnos.production.steps.order_script import EditorialScript,generation_schema
    schema=generation_schema(EditorialScript)
    restored=compile_plan(plan(),'dollars')['claims']
    request={'direction':'Discuss Alpha (A), Beta (B), and Gamma (G). Exact CTA: Follow Wealth Blueprint. Build wealth on purpose.'}
    bound=bind_restored_scene_schema(schema,{'target_segments':[6,6]},request,restored)
    assert bound['$defs']['RestoredScene5']['properties']['claim_ids']['const']==['c1']
    assert bound['$defs']['RestoredScene6']['properties']['text']['const']=='Follow Wealth Blueprint. Build wealth on purpose.'
    assert bound['$defs']['RestoredScene2']['properties']['on_screen']['const']=='Alpha: Research check'
    assert 'const' not in schema['$defs']['ScriptSegment']['properties']['text']
    assert bind_restored_scene_schema(schema,{'target_segments':[6,6]},request,{}) is schema


def test_targeted_decoding_keeps_unaffected_scene_narration_literal(script):
    from atnos.production.scoped_script_revision import bind_restored_scene_schema
    from atnos.production.steps.order_script import EditorialScript,generation_schema
    restored=compile_plan(plan(),'dollars')['claims']
    bound=bind_restored_scene_schema(generation_schema(EditorialScript),
        {'target_segments':[6,6],'issues':[{'code':'naturalness','segment_ids':['s01']}]},{},restored,script)
    assert bound['$defs']['RestoredScene2']['properties']['text']['const']==script.segments[1].text
    assert 'const' not in bound['$defs']['RestoredScene1']['properties']['text']

def plan():
    return CalculationPlan.model_validate({'rationale':'One explicit hypothetical compound example.',
        'examples':[{'id':'c1','kind':'lump_sum','purpose':'Illustrate a conditional tenfold outcome, not forecast it.',
                     'currency':'dollars','principal':100,'annual_rate':.26,'years':10,'spoken_rounding':100}]})

def seed(store, *, corrupt=False):
    job=create(store)['id']
    receipt=compile_plan(plan(),'dollars')
    if corrupt:receipt['claims']['c1']['result']=999999
    with store.lease(job) as token:
        for stage,payload in [('script_calculation_plan',plan().model_dump(mode='json')),('script_calculations',receipt)]:
            assert store.begin(job,token,stage,{}) is None
            item=write_json(store.context(job).artifact_dir/(stage+'.json'),payload)
            store.complete(job,token,stage,payload,[item.artifact_path])
    return job,receipt

def test_restores_exact_completed_compiler_claim_without_modifying_state(store):
    job,receipt=seed(store)
    before=store.get(job)
    assert restored_calculations(store,job,['c1'])==receipt['claims']
    after=store.get(job)
    assert before['provider_calls']==after['provider_calls']
    assert before['checkpoints']==after['checkpoints']

def test_rejects_unknown_or_changed_calculation(store):
    job,_=seed(store)
    with pytest.raises(OrderConflict,match='unknown or differs'):restored_calculations(store,job,['c2'])
    job,_=seed(store,corrupt=True)
    with pytest.raises(OrderConflict,match='unknown or differs'):restored_calculations(store,job,['c1'])

def test_rejects_missing_calculation_evidence(store):
    job,_=seed(store)
    (store.context(job).artifact_dir/'script_calculations.json').unlink()
    with pytest.raises(OrderConflict,match='intact completed'):restored_calculations(store,job,['c1'])

def test_model_cannot_change_amounts_or_omit_restored_claim_use():
    restored=compile_plan(plan(),'dollars')['claims']
    candidate=SimpleNamespace(claims=[NumericClaim.model_validate(restored['c1'])],segments=[SimpleNamespace(claim_ids=['c1'])])
    validate_restored_claims(candidate,restored)
    changed=copy.deepcopy(candidate);changed.claims[0].operands[0]=1000
    with pytest.raises(ValueError,match='exactly equal'):validate_restored_claims(changed,restored)
    candidate.segments[0].claim_ids=[]
    validate_restored_claims(candidate,restored,require_used=False)
    with pytest.raises(ValueError,match='must be used'):validate_restored_claims(candidate,restored)
    candidate.segments[0].claim_ids=['c99']
    with pytest.raises(ValueError,match='unknown claims'):validate_restored_claims(candidate,restored,require_used=False)

def test_empty_restoration_does_not_change_legacy_feedback_wire_format():
    original={'editor':'Test editor','reason':'Correct a specific naturalness problem.',
              'issues':[{'code':'naturalness','segment_ids':['s01'],'detail':'Write a natural complete sentence.'}],
              'target_segments':(4,5),'max_drafts':2}
    feedback=ScriptFeedback.model_validate(original)
    assert 'restore_calculation_ids' not in feedback.model_dump(mode='json')
    with pytest.raises(ValueError):ScriptFeedback.model_validate({**original,'restore_calculation_ids':['c1','c1']})

@pytest.mark.parametrize('text,bound',[
    ('Assuming twenty-six percent annually, one hundred dollars could become about one thousand dollars in ten years.', True),
    ('One hundred dollars will become one thousand dollars in ten years at twenty-six percent.', False),
    ('Assuming twenty-six percent annually, one hundred dollars could become two thousand dollars in ten years.', False),
    ('Hypothetically, twenty-six dollars could become one thousand dollars in ten years.', False),
    ('Compare company profits with loan losses.', False),
])
def test_only_complete_matching_hypothetical_narration_gets_a_missing_id(text,bound):
    restored=compile_plan(plan(),'dollars')['claims']
    candidate=SimpleNamespace(segments=[SimpleNamespace(text=text,claim_ids=[])])
    result=bind_restored_calculation_ids(candidate,restored)
    assert result.segments[0].claim_ids == (['c1'] if bound else [])
