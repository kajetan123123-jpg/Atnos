import json
import pytest
from atnos.production.order_autonomy import enable, policy, recover_due
from test_production_order_store import store
from atnos.production.project_profile import ProductionOrder


def create_local(store):
    return store.create(ProductionOrder(topic='A simple savings decision',delivery_mode='local_only'))['id']


def test_autonomy_dispatches_opted_in_order_and_preserves_budgets(store,monkeypatch):
    job=create_local(store)
    before=store.get(job)
    calls=[]
    monkeypatch.setattr('atnos.production.order_dispatch.ProductionOrderDispatcher._dispatch_locked',
                        lambda self,jid: calls.append(jid) or {'id':jid})
    enable(store.settings,job,reason='User requests autonomous local production without operator retries.')
    assert recover_due(store.settings)==[{'id':job}]
    after=store.get(job)
    assert calls==[job]
    assert after['provider_calls']==before['provider_calls']
    assert after['profile_hash']==before['profile_hash']
    assert policy(store.settings,job)['recoveries_used']==0


def test_autonomy_leaves_unregistered_order_alone(store,monkeypatch):
    create_local(store)
    monkeypatch.setattr('atnos.production.order_dispatch.ProductionOrderDispatcher._dispatch_locked',
                        lambda *args: pytest.fail('Unregistered order was dispatched'))
    assert recover_due(store.settings)==[]


def test_interrupted_recovery_is_reserved_once_without_reset(store,monkeypatch):
    job=create_local(store)
    monkeypatch.setattr('atnos.integrations.claim_evidence_gate.ensure_media_claim_gate',lambda **_: {'blocked':False})
    with store.lease(job) as token:
        store.block(job,token,'remotion_render',category='worker_interrupted',message='Worker stopped')
    enable(store.settings,job,reason='User requests automatic recovery of interrupted local rendering.',max_recoveries=1)
    calls=[]
    monkeypatch.setattr('atnos.production.order_dispatch.ProductionOrderDispatcher._dispatch_locked',
                        lambda self,jid: calls.append(jid) or {'id':jid})
    recover_due(store.settings)
    recover_due(store.settings)
    assert calls==[job]
    assert policy(store.settings,job)['recoveries_used']==1
    enable(store.settings,job,reason='User requests the same policy again without resetting recovery use.',max_recoveries=1)
    assert policy(store.settings,job)['recoveries_used']==1


def test_billing_error_is_never_blindly_retried(store,monkeypatch):
    job=create_local(store)
    monkeypatch.setattr('atnos.integrations.claim_evidence_gate.ensure_media_claim_gate',lambda **_: {'blocked':False})
    with store.lease(job) as token:
        store.block(job,token,'voice',category='dependency_failure',message='credit_balance_exhausted')
    enable(store.settings,job,reason='User requests autonomous local production with persistent budgets.')
    monkeypatch.setattr('atnos.production.order_dispatch.ProductionOrderDispatcher._dispatch_locked',
                        lambda *args: pytest.fail('Billing failure was retried'))
    assert recover_due(store.settings)==[]


def test_configured_future_local_film_gets_policy_without_reset_on_idempotent_create(store):
    store.settings=store.settings.model_copy(update={'shorts_order_autonomy_enabled':True})
    order=ProductionOrder(topic='A sustainable savings decision',delivery_mode='local_only')
    first=store.create(order,request_key='auto-future-local')
    assert first['autonomous_recovery']['enabled']
    again=store.create(order,request_key='auto-future-local')
    assert again['id']==first['id']
    assert again['autonomous_recovery']['used']==0


def test_latest_review_means_update_time_not_original_insertion_order():
    from atnos.production.studio_script_recovery import _latest_issues
    current={'code':'naturalness','segment_ids':['1'],'detail':'Current actual defect'}
    obsolete={'code':'practical_share','segment_ids':['0','5'],'detail':'Old irrelevant defect'}
    state={'error':json.dumps({'category':'invalid_output'}),'current_step':'script_repair/1',
           'checkpoints':[
        {'stage':'script_review/0','updated_at':'2026-10-06T18:00:00+00:00',
         'output':{'status':'failed','issues':[current]}},
        {'stage':'script_review/2','updated_at':'2026-10-05T18:00:00+00:00',
         'output':{'status':'failed','issues':[obsolete]}}]}
    assert _latest_issues(state)==[current]


def test_context_cache_recovery_extends_once_and_preserves_attempts(store,monkeypatch):
    from atnos.production.order_store import ProductionBlocked
    job=create_local(store)
    monkeypatch.setattr('atnos.integrations.claim_evidence_gate.ensure_media_claim_gate',lambda **_: {'blocked':False})
    maximum=store.get(job)['profile']['production']['max_attempts_per_element']
    with store.lease(job) as token:
        for attempt in range(maximum):
            store.begin(job,token,'script_context',{'attempt':attempt})
            store.fail(job,token,'script_context',category='invalid_input',message='Old cache invalidated')
        with pytest.raises(ProductionBlocked):
            store.begin(job,token,'script_context',{'fixed_cache':True})
    before=store.get(job)
    enable(store.settings,job,reason='User authorizes bounded automatic recovery after cache correction.')
    calls=[]
    monkeypatch.setattr('atnos.production.order_dispatch.ProductionOrderDispatcher._dispatch_locked',
                        lambda self,jid: calls.append(jid) or {'id':jid})
    recover_due(store.settings)
    after=store.get(job)
    assert calls==[job] and after['can_resume']
    assert after['provider_calls']==before['provider_calls']
    assert next(row for row in after['checkpoints'] if row['stage']=='script_context')['attempts']==maximum


def test_valid_supplied_intake_starts_through_normal_dispatcher(store,monkeypatch):
    from test_hypothetical_script_intake import order
    job=store.create(order())['id']
    assert store.get(job)['current_step']=='intake_review'
    enable(store.settings,job,reason='User requests automatic local production from this validated supplied scenario.')
    calls=[]
    monkeypatch.setattr('atnos.production.order_dispatch.ProductionOrderDispatcher._dispatch_locked',
                        lambda self,jid: calls.append(jid) or {'id':jid})
    recover_due(store.settings)
    assert calls==[job]


@pytest.mark.parametrize('failure_point',['state_read','dispatch'])
def test_one_broken_order_does_not_starve_later_orders(store,monkeypatch,failure_point):
    from atnos.production.order_store import ProductionOrderStore
    first=create_local(store)
    second=store.create(ProductionOrder(topic='Another independent savings decision',delivery_mode='local_only'))['id']
    for job in (first,second):
        enable(store.settings,job,reason='User authorizes normal local production and isolated recovery of each order.')
    before={job:store.get(job) for job in (first,second)}
    original_get=ProductionOrderStore.get
    if failure_point=='state_read':
        def get(self,job):
            if job==first:
                raise ValueError('Broken order payload; access_token=private-value')
            return original_get(self,job)
        monkeypatch.setattr(ProductionOrderStore,'get',get)
    dispatched=[]
    def dispatch(self,job):
        if job==first:
            raise OSError('Cannot launch worker; access_token=private-value')
        dispatched.append(job)
        return {'id':job}
    monkeypatch.setattr('atnos.production.order_dispatch.ProductionOrderDispatcher._dispatch_locked',dispatch)
    assert recover_due(store.settings)==[{'id':second}]
    assert recover_due(store.settings)==[{'id':second}]
    assert dispatched==[second,second]
    for job in (first,second):
        after=original_get(store,job)
        assert after['provider_calls']==before[job]['provider_calls']
        assert after['reserved_cost_units']==before[job]['reserved_cost_units']
        assert after['profile_hash']==before[job]['profile_hash']
        assert policy(store.settings,job)['recoveries_used']==0
    events=[row for row in original_get(store,first)['history'] if row['event']=='autonomous_recovery_deferred']
    assert len(events)==1
    assert 'private-value' not in json.dumps(events)
    assert events[0]['detail']['other_orders_continue']


@pytest.mark.parametrize('maximum',[True,1.5,0,41])
def test_recovery_limit_requires_bounded_integer(store,maximum):
    job=create_local(store)
    with pytest.raises(ValueError,match='bounded limit'):
        enable(store.settings,job,reason='User authorizes bounded local production recovery.',max_recoveries=maximum)
    assert policy(store.settings,job) is None


def test_default_enrolment_does_not_extend_to_project_publication_policy(store):
    store.settings=store.settings.model_copy(update={'shorts_order_autonomy_enabled':True})
    job=store.create(ProductionOrder(topic='A normal project policy order'))['id']
    assert policy(store.settings,job) is None


@pytest.mark.parametrize('category',['cost_limit','provider_call_limit'])
def test_authorized_recovery_never_extends_billing_or_call_limits(store,monkeypatch,category):
    from atnos.intelligence.storage import connect
    job=create_local(store)
    enable(store.settings,job,reason='User authorizes local automatic recovery preserving all consumed limits.')
    with connect(store.settings) as db:
        db.execute("UPDATE pipeline_jobs SET status='blocked',current_step='script_repair/1',error=? WHERE id=?",(category,job))
        db.commit()
    before=store.get(job)
    monkeypatch.setattr('atnos.production.order_dispatch.ProductionOrderDispatcher._dispatch_locked',
                        lambda *args:pytest.fail('An exhausted billing/call limit was retried'))
    assert recover_due(store.settings)==[]
    after=store.get(job)
    assert after['profile_hash']==before['profile_hash']
    assert after['provider_calls']==before['provider_calls']
    assert after['reserved_cost_units']==before['reserved_cost_units']
    assert policy(store.settings,job)['recoveries_used']==0
