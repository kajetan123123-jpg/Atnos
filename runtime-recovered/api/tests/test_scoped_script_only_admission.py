"""Offline admission tests; no inference, audio, rendering or network."""
import pytest
from test_production_order_store import store
from test_production_order_script import script
from test_scoped_script_revision import feedback, original_order
from atnos.production.scoped_script_revision import register_feedback
from atnos.production.order_store import ProductionBlocked, file_hash


@pytest.mark.parametrize('suffix', ['draft/0','review/0','accepted'])
def test_script_only_admits_matching_registered_editorial_stage(store,original_order,feedback,suffix):
    job,_=original_order
    root=store.context(job).artifact_dir
    registration=register_feedback(store.settings,job,feedback,approved=True)
    digest=registration['feedback_hash']
    canonical=file_hash(root/'script.json')
    before=store.get(job)['provider_calls']
    with store.lease(job,script_only=True) as token:
        store.begin(job,token,'script_scoped/'+digest[:12]+'/'+suffix,{'feedback_hash':digest},local_model_call=suffix!='accepted')
        assert file_hash(root/'script.json')==canonical
    assert store.get(job)['provider_calls']==before+(suffix!='accepted')


@pytest.mark.parametrize('suffix,kwargs', [
    ('draft/0',{'provider_call':True,'estimated_cost_usd':0.1}),
    ('review/0',{'external_tool_call':True}),
    ('accepted',{'estimated_cost_usd':0.1}),
    ('voice/s01',{}),('remotion_render',{}),('image_generation',{}),
])
def test_registered_editorial_scope_never_admits_paid_or_media_work(store,original_order,feedback,suffix,kwargs):
    job,_=original_order
    registration=register_feedback(store.settings,job,feedback,approved=True)
    digest=registration['feedback_hash']
    before=store.get(job)['provider_calls']
    with store.lease(job,script_only=True) as token:
        with pytest.raises(ProductionBlocked):
            store.begin(job,token,'script_scoped/'+digest[:12]+'/'+suffix,{'feedback_hash':digest},**kwargs)
    assert store.get(job)['provider_calls']==before


@pytest.mark.parametrize('wrong_stage,wrong_input', [(True,False),(False,True)])
def test_script_only_rejects_foreign_feedback_hash(store,original_order,feedback,wrong_stage,wrong_input):
    job,_=original_order
    digest=register_feedback(store.settings,job,feedback,approved=True)['feedback_hash']
    stage_hash='0'*12 if wrong_stage else digest[:12]
    with store.lease(job,script_only=True) as token:
        with pytest.raises(ProductionBlocked):
            store.begin(job,token,'script_scoped/'+stage_hash+'/draft/0',{'feedback_hash':'0'*64 if wrong_input else digest},local_model_call=True)
