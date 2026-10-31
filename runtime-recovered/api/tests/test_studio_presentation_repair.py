"""Real registration lifecycle on isolated fixtures. No model/media dispatch."""
import asyncio
import copy
import pytest
from atnos.production import studio_renderer_recovery as studio
from atnos.production.order_store import ProductionBlocked
from atnos.production.remotion_quality_repair import _latest_refresh
from test_local_voice_revision import trial
from test_production_order_store import store
from test_production_order_script import script
from test_local_voice_current_review import renewed


def request(**changes):
    return studio.StudioRendererRepair(request_key='studio-render-fixture',expected_package_sha256='b'*64,
        expected_renderer_source='c'*64,note='Improve attention timing with the current unchanged voice and script',**changes)


def prepare(trial,monkeypatch):
    proof={'stage':'isolated-current-review','input_hash':'d'*64,'output_hash':'e'*64}
    monkeypatch.setattr('atnos.production.scoped_script_revision.verified_current_renderer_review',lambda *a,**kw:(proof,{}))
    return studio.presentation_snapshot(trial.store,trial.job)


def test_prepare_is_read_only_and_registration_is_idempotent_after_a_lost_response(trial,monkeypatch):
    before=copy.deepcopy(trial.store.get(trial.job))
    snapshot=prepare(trial,monkeypatch)
    assert snapshot['can_repair'] and snapshot['package_sha256']=='b'*64
    assert trial.store.get(trial.job)==before
    first=asyncio.run(studio.register_studio_presentation(trial.store,trial.job,request()))
    after=copy.deepcopy(trial.store.get(trial.job))
    second=asyncio.run(studio.register_studio_presentation(trial.store,trial.job,request()))
    assert second['already_registered'] and second['request_digest']==first['request_digest']
    assert trial.store.get(trial.job)==after
    action=_latest_refresh(after)['output']
    assert action['scope']=='renderer_and_downstream_only' and action['current_script_review']['input_hash']=='d'*64
    assert after['provider_calls']==before['provider_calls'] and after['reserved_cost_units']==before['reserved_cost_units']
    for old in before['checkpoints']:
        assert next(r for r in after['checkpoints'] if r['stage']==old['stage'])==old


@pytest.mark.parametrize('changed',['package','source','review'])
def test_stale_preparation_stops_before_registration(trial,monkeypatch,changed):
    prepare(trial,monkeypatch)
    payload=request()
    if changed=='package':payload.expected_package_sha256='f'*64
    if changed=='source':payload.expected_renderer_source='f'*64
    if changed=='review':
        def reject(*a,**kw):raise ProductionBlocked('current review is invalid')
        monkeypatch.setattr('atnos.production.scoped_script_revision.verified_current_renderer_review',reject)
    before=copy.deepcopy(trial.store.get(trial.job))
    with pytest.raises(ProductionBlocked):asyncio.run(studio.register_studio_presentation(trial.store,trial.job,payload))
    assert trial.store.get(trial.job)==before


def test_same_key_cannot_change_note_and_active_worker_is_not_reopened(trial,monkeypatch):
    prepare(trial,monkeypatch)
    asyncio.run(studio.register_studio_presentation(trial.store,trial.job,request()))
    payload=request();payload.note='A different unregistered reason must not reuse the previous action key'
    with pytest.raises(ProductionBlocked,match='different input'):
        asyncio.run(studio.register_studio_presentation(trial.store,trial.job,payload))
    assert not studio.presentation_snapshot(trial.store,trial.job)['can_repair']


def test_already_current_renderer_is_not_offered_as_a_new_correction(trial,monkeypatch):
    from atnos.production.project_profile import content_hash
    from atnos.production.order_store import file_hash
    from atnos.production.channels.wealth_blueprint.remotion_order_edit import REMOTION_REQUIRED,RENDERER_BOUNDARY
    prepare(trial,monkeypatch)
    original=trial.store.get
    def same_source(job):
        state=copy.deepcopy(original(job))
        row=next(r for r in state['checkpoints'] if r['stage']=='remotion_render')
        row['input_hash']=content_hash({'manifest_sha256':file_hash(trial.root/'project_manifest.json'),
            'renderer_source_fingerprint':'c'*64,'mode':REMOTION_REQUIRED,'renderer_boundary':RENDERER_BOUNDARY})
        return state
    monkeypatch.setattr(trial.store,'get',same_source)
    snapshot=studio.presentation_snapshot(trial.store,trial.job)
    assert not snapshot['can_repair'] and 'Brak nowej zmiany' in snapshot['reason']


@pytest.mark.parametrize('trial',[True],indirect=True)
def test_render_only_action_reuses_actual_bound_current_review_without_model(renewed):
    from atnos.production import scoped_script_revision as scoped,remotion_quality_repair as repair
    from atnos.production.order_execution import StageExecutor
    t=renewed
    assert studio.presentation_snapshot(t.store,t.job)['can_repair']
    asyncio.run(studio.register_studio_presentation(t.store,t.job,request()))
    before=copy.deepcopy(t.store.get(t.job))
    with t.store.lease(t.job) as token:
        executor=StageExecutor(t.store,t.job,token)
        executor.context.artifacts['_renderer_refresh']=repair.load_renderer_refresh(executor)
        async def forbidden(*a,**kw):pytest.fail('Reusing the unchanged current review must not reserve a model stage')
        executor.run=forbidden
        assert asyncio.run(scoped.renew_renderer_review(executor,t.feedback_request,t.accepted,t.script,{}))==t.review_result
    after=t.store.get(t.job)
    assert after['provider_calls']==before['provider_calls'] and after['checkpoints']==before['checkpoints']
