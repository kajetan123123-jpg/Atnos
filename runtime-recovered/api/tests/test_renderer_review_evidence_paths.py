from test_production_order_store import store
from atnos.production.project_profile import ProductionOrder
from atnos.production.steps.common import write_json
from atnos.production.order_store import OrderConflict
import pytest

def test_repair_preserves_both_exact_reviews_counters_and_statuses(store,monkeypatch):
    # Isolate evidence persistence; claim admission has separate integration tests.
    monkeypatch.setattr('atnos.integrations.claim_evidence_gate.ensure_media_claim_gate',lambda **kwargs:{'blocked':False})
    job=store.create(ProductionOrder(topic='Review filename collision regression',delivery_mode='local_only'))['id']
    root=store.context(job).artifact_dir;digest='a'*64
    with store.lease(job) as token:
        stage='remotion_refresh/'+digest[:12]
        store.begin(job,token,stage,{'action':digest})
        a=write_json(root/'action.json',{'digest':digest})
        store.complete(job,token,stage,a.payload,[a.artifact_path])
        for version in ('v42','v42-p2'):
            stage='remotion_script_review/'+digest[:12]+'/'+version
            store.begin(job,token,stage,{'version':version})
            result=write_json(root/'renderer_refresh'/digest[:12]/'script-review.json',
                {'action_digest':digest,'scope':'review_only','version':version,'status':'failed'})
            store.complete(job,token,stage,result.payload,[result.artifact_path])
        before=store.get(job)
        assert not store._valid_files(root,before['checkpoints'][1]['files_json'])
        changes=store.repair_renderer_review_evidence(job,token,digest)
        assert len(changes)==2
        assert store.repair_renderer_review_evidence(job,token,digest)==[]
        after=store.get(job)
        assert after['provider_calls']==before['provider_calls']
        for old,new in zip(before['checkpoints'],after['checkpoints']):
            assert old['status']==new['status'] and old['output']==new['output'] and old['attempts']==new['attempts']
            assert store._valid_files(root,new['files_json'])
        with pytest.raises(OrderConflict):store.repair_renderer_review_evidence(job,token,'b'*64)
