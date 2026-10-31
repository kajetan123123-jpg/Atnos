import asyncio
import json
import pytest
from atnos.production import order_execution
from atnos.production.order_store import ProductionBlocked
from atnos.production.order_autonomy import enable,recover_due
from atnos.production.steps.common import write_json
from test_production_order_store import store
from test_order_autonomy import create_local


def test_update_finishes_active_operation_before_handoff_without_new_call(store,monkeypatch):
    monkeypatch.setattr('atnos.integrations.claim_evidence_gate.ensure_media_claim_gate',lambda **_: {'blocked':False})
    revision=['old']
    monkeypatch.setattr(order_execution,'_runtime_code_identity',lambda:revision[0])
    job=create_local(store)
    enable(store.settings,job,reason='User authorizes safe autonomous production and deployment recovery.')
    reached=[]
    with store.lease(job) as token:
        executor=order_execution.StageExecutor(store,job,token)
        async def first():
            reached.append('started')
            revision[0]='updated'
            reached.append('completed')
            return write_json(executor.context.artifact_dir/'completed.json',{'status':'passed'})
        async def second():
            pytest.fail('Next operation began before safe worker handoff')
        assert asyncio.run(executor.run('assets/first',{},first,local_model_call=True))['status']=='passed'
        with pytest.raises(ProductionBlocked,match='Safe worker update'):
            asyncio.run(executor.run('assets/second',{},second,local_model_call=True))
        state=store.get(job)
        assert reached==['started','completed']
        assert state['provider_calls']==1
        assert json.loads(state['error'])['category']=='worker_interrupted'
        assert next(r for r in state['checkpoints'] if r['stage']=='assets/first')['status']=='done'
        assert not any(r['stage']=='assets/second' for r in state['checkpoints'])
    dispatched=[]
    monkeypatch.setattr('atnos.production.order_dispatch.ProductionOrderDispatcher._dispatch_locked',
                        lambda self,jid:dispatched.append(jid) or {'id':jid})
    recover_due(store.settings)
    recover_due(store.settings)
    assert dispatched==[job]
    assert store.get(job)['provider_calls']==1
