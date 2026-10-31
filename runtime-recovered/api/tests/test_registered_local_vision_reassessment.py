"""A different local auditor is an explicit registry change, not a QA waiver."""
import asyncio
from types import SimpleNamespace as NS
import pytest
from atnos.production import remotion_quality_repair as repair
from atnos.production.channels.wealth_blueprint import remotion_order_edit as edit
from atnos.production.order_store import ProductionBlocked,file_hash
from atnos.production.project_profile import content_hash
from test_renderer_refresh_followup import rejected_refresh,complete
from test_production_order_store import store

@pytest.mark.parametrize('case',['local','missing','no-vision','remote'])
def test_only_an_installed_local_vision_capability_is_eligible(monkeypatch,case):
    class Response:
        def __init__(self,value):self.value=value
        def raise_for_status(self):pass
        def json(self):return self.value
    class Client:
        def __init__(self,**kwargs):assert kwargs['trust_env'] is False
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def get(self,url):return Response({'models':[] if case=='missing' else [{'name':'local-vision','digest':'a'*64}]})
        def post(self,url,json):return Response({'capabilities':[] if case=='no-vision' else ['vision'],'remote_host':'remote.example' if case=='remote' else ''})
    monkeypatch.setattr(repair.httpx,'Client',Client)
    if case=='local':
        assert repair.verified_local_vision_model(NS(ollama_base_url='http://127.0.0.1:11436'),'local-vision')['model_digest']=='a'*64
    else:
        with pytest.raises(ProductionBlocked):repair.verified_local_vision_model(NS(ollama_base_url='http://127.0.0.1:11436'),'local-vision')

def test_unmanaged_endpoint_is_rejected_before_any_connection(monkeypatch):
    monkeypatch.setattr(repair.httpx,'Client',lambda **_:pytest.fail('Do not connect to unapproved endpoints'))
    with pytest.raises(ProductionBlocked):repair.verified_local_vision_model(NS(ollama_base_url='https://remote.example'),'some-model')

@pytest.mark.parametrize('model',['same-local-auditor','different-local-auditor'])
def test_unchanged_video_can_only_be_reassessed_by_a_different_verified_local_model(rejected_refresh,monkeypatch,model):
    store,job,binding=rejected_refresh;root=store.context(job).artifact_dir
    monkeypatch.setattr(edit,'renderer_source_fingerprint',lambda:'a'*64)
    with store.lease(job) as token:
        row=next(r for r in store.get(job)['checkpoints'] if r['stage']=='semantic/s01')
        output=row['output'];output['verdict']['model']='same-local-auditor'
        complete(store,job,token,'semantic/s01',{'fixture':'actual failed model identity'},root/'qa/s01.json',output)
        store.block(job,token,'qa',category='execution_failure',message='Actual rejected fixture')
    binding=binding.model_copy(update={'expected_renderer_source_fingerprint':'a'*64,'expected_failed_reviews':{'semantic/s01':content_hash(output)}})
    monkeypatch.setattr(repair,'verified_local_vision_model',lambda _settings,name:{'model':name,'model_digest':'d'*64,'endpoint':'http://127.0.0.1:11436'})
    before=store.get(job)
    call=repair.register_renderer_refresh(store,job,expected_package_sha256='b'*64,followup=binding,
        visual_qa_model=model,authorized=True,reason='Reassess genuine failed scene evidence with the explicitly selected installed local vision model.')
    if model=='same-local-auditor':
        with pytest.raises(ProductionBlocked,match='different verified'):asyncio.run(call)
        assert store.get(job)['checkpoints']==before['checkpoints']
    else:
        result=asyncio.run(call)
        assert result['status']=='registered'
        after=store.get(job)
        payload=next(r['output'] for r in after['checkpoints'] if r['stage']=='remotion_refresh/'+result['request_digest'][:12])
        assert payload['visual_qa_model']['model']==model
        assert payload['user_appearance_approved'] is False
        assert after['provider_calls']==before['provider_calls']
