import asyncio,json
from types import SimpleNamespace as NS
import pytest
from test_production_order_script import script
from atnos.production import scoped_script_revision as scoped
from atnos.production.order_store import ProductionBlocked,file_hash

def setup(tmp_path,script):
    raw=script.model_dump(mode='json')
    path=tmp_path/'script.json';path.write_text(json.dumps(raw),encoding='utf8')
    ex=NS(job_id='test-job',token='fixture-token',context=NS(artifact_dir=tmp_path,artifacts={'_renderer_refresh':{
        'digest':'a'*64,'scope':'renderer_and_downstream_only','job_id':'test-job'}}),store=NS(settings=NS(),get=lambda job:{'checkpoints':[]},repair_renderer_review_evidence=lambda *args:[]))
    cache={}
    async def run(stage,inputs,operation,**kwargs):
        key=(stage,json.dumps(inputs,sort_keys=True))
        if key not in cache:cache[key]=(await operation()).payload
        return cache[key]
    ex.run=run
    return ex,{'feedback_hash':'b'*64,'feedback':{}},{'input_hash':'c'*64},raw,cache

def test_fresh_review_is_bound_cached_and_does_not_modify_canonical(tmp_path,script,monkeypatch):
    ex,req,accepted,raw,cache=setup(tmp_path,script)
    calls=[]
    async def review(settings,context,actual):
        assert context.artifacts['scoped_script_guidance']['reason'].startswith('Independently assess')
        assert context.artifacts['scoped_script_guidance'].get('issues')==req['feedback'].get('issues')
        calls.append(1);return {'status':'passed','issues':[]}
    monkeypatch.setattr(scoped.order_script,'review',review)
    monkeypatch.setattr(scoped,'feedback_issues',lambda *args:[])
    monkeypatch.setattr(scoped,'validate_restored_claims',lambda *args:None)
    before=file_hash(tmp_path/'script.json')
    for _ in range(2):
        result=asyncio.run(scoped.renew_renderer_review(ex,req,accepted,raw,[]))
        assert result['canonical_changed'] is False
    assert calls==[1] and len(cache)==1
    assert file_hash(tmp_path/'script.json')==before

def test_three_durable_attempts_prevent_another_prompt_version(tmp_path,script):
    ex,req,accepted,raw,cache=setup(tmp_path,script)
    ex.store.get=lambda job:{'checkpoints':[{'stage':'remotion_script_review/'+('a'*12)+'/v41','attempts':3}]}
    with pytest.raises(ProductionBlocked,match='exhausted'):
        asyncio.run(scoped.renew_renderer_review(ex,req,accepted,raw,[]))

@pytest.mark.parametrize('case',['no_action','failed','false_pass','unavailable','canonical_changed'])
def test_rejected_or_unauthorized_review_never_passes(tmp_path,script,monkeypatch,case):
    ex,req,accepted,raw,cache=setup(tmp_path,script)
    if case=='no_action':ex.context.artifacts={}
    async def review(*args):
        if case=='unavailable':raise RuntimeError('offline')
        if case=='canonical_changed':(tmp_path/'script.json').write_text('{}')
        return {'status':'failed' if case=='failed' else 'passed','issues':[{'code':'bad'}] if case=='false_pass' else []}
    monkeypatch.setattr(scoped.order_script,'review',review)
    monkeypatch.setattr(scoped,'feedback_issues',lambda *args:[])
    monkeypatch.setattr(scoped,'validate_restored_claims',lambda *args:None)
    with pytest.raises(ProductionBlocked):asyncio.run(scoped.renew_renderer_review(ex,req,accepted,raw,[]))
