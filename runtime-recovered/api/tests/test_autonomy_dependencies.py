"""Read-only readiness enables one audited resume when dependencies return."""
from threading import Event

import httpx
import pytest

from atnos.production import order_autonomy as autonomy
from atnos.production.order_store import ProductionBlocked
from test_production_order_store import store
from test_order_autonomy import create_local


@pytest.fixture
def dependency_order(store,monkeypatch):
    monkeypatch.setattr('atnos.integrations.claim_evidence_gate.ensure_media_claim_gate',lambda **_:{'blocked':False})
    job=create_local(store)
    with store.lease(job) as token:
        store.block(job,token,'preflight',category='dependency_unavailable',message='All connection attempts failed')
    autonomy.enable(store.settings,job,reason='User authorizes independent readiness checks and bounded local recovery.',max_recoveries=2)
    return store,job


@pytest.fixture
def available_preflight(dependency_order,monkeypatch):
    store,job=dependency_order
    state=store.get(job)
    config=state['profile']['production']
    names={config['script_model']['generator'],config['script_model']['reviewer'],
           config['assets']['embedding_model'],config.get('visual_qa',{}).get('model','qwen3-vl:8b-instruct')}
    paths=[]
    for value in [config['reference_profile'],*config['assets']['manifests']]:
        path=store.settings.data_dir/value
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('isolated valid configured file',encoding='utf-8')
        paths.append(path)
    monkeypatch.setattr('atnos.production.order_execution._voice_preflight_settings',lambda executor,config:store.settings)
    monkeypatch.setattr('atnos.production.model_runtime_startup._launch_models_only',
                        lambda *_:pytest.fail('A monitor readiness check launched a model service'))
    live={'online':True,'names':names,'requests':[]}
    class Client:
        def __init__(self,**_):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*_):pass
        async def get(self,url):
            live['requests'].append(url)
            if not live['online']:
                raise httpx.ConnectError('Service is unavailable')
            return httpx.Response(200,json={'models':[{'name':name} for name in live['names']]},
                                  request=httpx.Request('GET',url))
    monkeypatch.setattr('atnos.production.order_execution.httpx.AsyncClient',Client)
    return live,paths


def finish_probe(store):
    with autonomy._dependency_probe_lock:
        futures=[future for key,(future,_) in autonomy._dependency_probes.items()
                 if key[0]==str(store.settings.data_dir.resolve())]
    assert len(futures)==1
    futures[0].result(timeout=5)


def test_preflight_recovery_waits_for_real_readiness_and_reserves_once(dependency_order,available_preflight,monkeypatch):
    store,job=dependency_order
    live,_=available_preflight
    before=store.get(job)
    dispatch=[]
    monkeypatch.setattr('atnos.production.order_dispatch.ProductionOrderDispatcher.dispatch',
                        lambda self,jid:dispatch.append(jid) or {'id':jid})
    live['online']=False
    assert autonomy.recover_due(store.settings)==[]
    finish_probe(store)
    assert autonomy.recover_due(store.settings)==[]
    assert autonomy.policy(store.settings,job)['recoveries_used']==0 and dispatch==[]
    live['online']=True
    assert autonomy.recover_due(store.settings)==[]
    finish_probe(store)
    assert autonomy.recover_due(store.settings)==[{'id':job}]
    requests=len(live['requests'])
    assert autonomy.recover_due(store.settings)==[]
    assert len(live['requests'])==requests  # Same stored failure does not start another probe.
    after=store.get(job)
    assert dispatch==[job]
    assert after['provider_calls']==before['provider_calls']
    assert after['profile_hash']==before['profile_hash']
    assert after['reserved_cost_units']==before['reserved_cost_units']
    assert autonomy.policy(store.settings,job)['recoveries_used']==1
    event=next(event for event in after['history'] if event['event']=='autonomous_recovery_reserved')
    assert event['detail']['kind']=='dependency_readiness_recovery'
    assert event['detail']['evidence']['read_only'] is True
    assert event['detail']['evidence']['models_started'] is False


@pytest.mark.parametrize('remaining_failure',['missing_model','missing_manifest','missing_voice','frozen_contract'])
def test_responding_ollama_is_not_sufficient_without_all_preflight_gates(dependency_order,available_preflight,monkeypatch,remaining_failure):
    store,job=dependency_order
    live,paths=available_preflight
    if remaining_failure=='missing_model':
        live['names']=set()
    elif remaining_failure=='missing_manifest':
        paths[0].unlink()
    elif remaining_failure=='missing_voice':
        def absent_voice(*_):
            raise ProductionBlocked('Whisper or the approved local voice is unavailable')
        monkeypatch.setattr('atnos.production.order_execution._voice_preflight_settings',absent_voice)
    else:
        from atnos.production.channel_contract import ChannelContractError
        def changed_contract(*_):
            raise ChannelContractError('Frozen channel style changed')
        monkeypatch.setattr('atnos.production.channel_contract.validate_frozen',changed_contract)
    monkeypatch.setattr('atnos.production.order_dispatch.ProductionOrderDispatcher.dispatch',
                        lambda *_:pytest.fail('Incomplete availability was treated as ready'))
    assert autonomy.recover_due(store.settings)==[]
    finish_probe(store)
    assert autonomy.recover_due(store.settings)==[]
    assert autonomy.policy(store.settings,job)['recoveries_used']==0
    assert store.get(job)['provider_calls']==0


def test_slow_readiness_probe_does_not_delay_other_queued_orders(dependency_order,monkeypatch):
    from atnos.production.project_profile import ProductionOrder
    store,job=dependency_order
    second=store.create(ProductionOrder(topic='A separate production can start',delivery_mode='local_only'))['id']
    autonomy.enable(store.settings,second,reason='User authorizes this second local order independently of unavailable dependencies.')
    release=Event();entered=Event()
    def slow_probe(*_):
        entered.set()
        release.wait(timeout=5)
        return None
    monkeypatch.setattr(autonomy,'_probe_dependency_preflight',slow_probe)
    dispatched=[]
    monkeypatch.setattr('atnos.production.order_dispatch.ProductionOrderDispatcher.dispatch',
                        lambda self,jid:dispatched.append(jid) or {'id':jid})
    try:
        assert autonomy.recover_due(store.settings)==[{'id':second}]
        assert entered.wait(timeout=2)
        assert dispatched==[second] and not release.is_set()
        assert autonomy.policy(store.settings,job)['recoveries_used']==0
    finally:
        release.set()
        finish_probe(store)
    assert autonomy.recover_due(store.settings)==[{'id':second}]


def test_new_actual_preflight_failure_can_recover_with_distinct_audit_hash(dependency_order,available_preflight,monkeypatch):
    from atnos.intelligence.storage import connect
    store,job=dependency_order
    dispatched=[]
    monkeypatch.setattr('atnos.production.order_dispatch.ProductionOrderDispatcher.dispatch',
                        lambda self,jid:dispatched.append(jid) or {'id':jid})
    for blocked_at in ('2026-10-08T10:00:00+00:00','2026-10-08T10:01:00+00:00'):
        with connect(store.settings) as db:
            db.execute('UPDATE pipeline_jobs SET updated_at=? WHERE id=?',(blocked_at,job))
            db.commit()
        assert autonomy.recover_due(store.settings)==[]
        finish_probe(store)
        assert autonomy.recover_due(store.settings)==[{'id':job}]
    assert dispatched==[job,job]
    assert autonomy.policy(store.settings,job)['recoveries_used']==2
    events=[event for event in store.get(job)['history'] if event['event']=='autonomous_recovery_reserved']
    assert len({event['detail']['failure_hash'] for event in events})==2
    assert store.get(job)['provider_calls']==0
    assert autonomy.recover_due(store.settings)==[]
