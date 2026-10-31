import asyncio
import pytest
from atnos.production.order_execution import StageExecutor
from atnos.production.order_store import ProductionBlocked
from atnos.production.project_profile import ProductionOrder
from atnos.production.steps.common import write_json
from test_production_order_store import store


def test_refresh_never_dispatches_missing_upstream_voice_or_changes_counters(store):
    job=store.create(ProductionOrder(topic='A local renderer correction',delivery_mode='local_only'))['id']
    before=store.get(job)
    async def never():raise AssertionError('Voice inference must not start')
    with store.lease(job) as token:
        executor=StageExecutor(store,job,token)
        executor.context.artifacts['_renderer_refresh']={'scope':'renderer_and_downstream_only'}
        with pytest.raises(ProductionBlocked,match='intact cached upstream'):
            asyncio.run(executor.run('voice/local_ryan/narration',{'script':'changed'},never,local_model_call=True))
    after=store.get(job)
    assert after['provider_calls']==before['provider_calls']
    assert after['reserved_cost_units']==before['reserved_cost_units']
    assert not after['checkpoints']


def test_refresh_reuses_intact_upstream_but_blocks_a_stale_input(store):
    job=store.create(ProductionOrder(topic='A local cached prerequisite',delivery_mode='local_only'))['id']
    with store.lease(job) as token:
        inputs={'revision':'original'}
        store.begin(job,token,'script_context',inputs)
        result=write_json(store.context(job).artifact_dir/'context.json',{'actual_fixture':'original'})
        store.complete(job,token,'script_context',result.payload,[result.artifact_path])
        executor=StageExecutor(store,job,token)
        executor.context.artifacts['_renderer_refresh']={'scope':'renderer_and_downstream_only'}
        async def never():raise AssertionError('Cached operation must not run')
        assert asyncio.run(executor.run('script_context',inputs,never))==result.payload
        with pytest.raises(ProductionBlocked,match='intact cached upstream'):
            asyncio.run(executor.run('script_context',{'revision':'different'},never))
    assert store.get(job)['checkpoints'][0]['attempts']==1


def test_refresh_rejects_a_paid_downstream_relay_before_dispatch(store):
    job=store.create(ProductionOrder(topic='A paid request remains excluded',delivery_mode='local_only'))['id']
    with store.lease(job) as token:
        executor=StageExecutor(store,job,token)
        executor.context.artifacts['_renderer_refresh']={'scope':'renderer_and_downstream_only'}
        async def never():raise AssertionError('Paid relay must not run')
        with pytest.raises(ProductionBlocked,match='paid or external'):
            asyncio.run(executor.run('covers',{},never,provider_call=True,estimated_cost_usd=1))
    state=store.get(job)
    assert state['provider_calls']==0 and state['reserved_cost_units']==0
    assert not state['checkpoints']
