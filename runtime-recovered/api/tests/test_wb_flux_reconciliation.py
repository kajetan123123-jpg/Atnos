import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from atnos.production import wb_local_flux_trial as flux
from atnos.production.order_store import OrderConflict, ProductionBlocked
from atnos.production.steps.common import write_json
from test_wb_local_flux_trial import local_trial, register
from test_wb_image_api_production import trial
from test_production_order_store import store
from test_scoped_script_revision import four_thoughts

REAL_PROCESS = flux.psutil.Process


@pytest.fixture
def interrupted(local_trial, monkeypatch):
    store, job, context, need, paths = local_trial
    receipt = register(local_trial)
    folder = context.artifact_dir / 'assets/image_local/flux_trial' / receipt['digest'][:16]
    (folder / 'output').mkdir(parents=True)
    write_json(folder / 'dispatch.json', {'status':'queued','prompt_id':'one-request',
        'trial_digest':receipt['digest'],'workflow_sha256':receipt['workflow_sha256']})
    write_json(folder / 'workflow.json', receipt['workflow'])
    instance = {'pid':900001,'created':10,'launcher_pid':900002,'launcher_created':9,
        'port':8195,'marker':flux.VERSION,'trial_digest':receipt['digest'],
        'cwd':str(paths['main'].parent),'exe':str(paths['python']),
        'argv':[str(paths['python']),'-s',str(paths['main']),'--listen','127.0.0.1','--port','8195',
                '--base-directory',str(folder),'--output-directory',str(folder / 'output')]}
    write_json(folder / 'instance.json', instance)
    write_json(folder / 'runtime.json', {'identity':instance, 'trial_digest':receipt['digest']})
    stage = 'assets/image_local/flux2-trial/s01/' + receipt['digest'][:16]
    with store.lease(job) as token:
        store.begin(job, token, stage, {'trial':receipt['digest']}, local_model_call=True)
        store.block(job, token, stage, category='execution_outcome_unknown', message='Worker disappeared after dispatch')
    def gone(pid=None):
        if pid in (None, os.getpid()):
            return REAL_PROCESS(pid)
        raise flux.psutil.NoSuchProcess(pid)
    monkeypatch.setattr(flux, 'psutil', SimpleNamespace(Process=gone,
        NoSuchProcess=flux.psutil.NoSuchProcess, AccessDenied=flux.psutil.AccessDenied,
        CONN_LISTEN=flux.psutil.CONN_LISTEN, net_connections=lambda **_: []))
    monkeypatch.setattr(flux.ColdFluxSession, '_launch', lambda *_: pytest.fail('reconciliation launched a generator'))
    return store, job, context, receipt, folder, stage


def test_read_only_observation_proves_abandonment_not_connection_failure(interrupted):
    _, _, _, receipt, folder, _ = interrupted
    proof = flux._observe_abandoned_flux(folder, receipt)
    assert proof['outcome'] == 'abandoned_without_product'
    assert proof['flux_retry_permitted'] is False
    assert not (folder / 'failure.json').exists()


@pytest.mark.parametrize('case', ['process','pid_reuse','denied','listener','result','history','identity','port','argv'])
def test_unsafe_or_incomplete_outcome_remains_blocked(interrupted, monkeypatch, case):
    _, _, _, receipt, folder, _ = interrupted
    if case in {'process','pid_reuse'}:
        monkeypatch.setattr(flux.psutil, 'Process', lambda _: SimpleNamespace(create_time=lambda:10 if case=='process' else 80))
    elif case == 'denied':
        def denied(pid): raise flux.psutil.AccessDenied(pid)
        monkeypatch.setattr(flux.psutil, 'Process', denied)
    elif case == 'listener':
        monkeypatch.setattr(flux.psutil,'net_connections',lambda **_: [SimpleNamespace(status=flux.psutil.CONN_LISTEN,laddr=SimpleNamespace(port=8195))])
    elif case == 'result': (folder / 'output/result.png').write_bytes(b'not reviewed')
    elif case == 'history': write_json(folder / 'history.json', {'one-request':{'status':{'completed':True}}})
    elif case in {'port','argv'}:
        record=json.loads((folder / 'instance.json').read_text())
        if case=='port':
            record['port']=8196
            record['argv'][record['argv'].index('--port')+1]='8196'
        else: record['argv'][2]=str(folder / 'another-main.py')
        write_json(folder / 'instance.json',record)
        write_json(folder / 'runtime.json',{'identity':record,'trial_digest':receipt['digest']})
    else:
        record=json.loads((folder / 'dispatch.json').read_text())
        record['workflow_sha256']='changed'
        write_json(folder / 'dispatch.json',record)
    with pytest.raises(ProductionBlocked): flux._observe_abandoned_flux(folder,receipt)
    assert not (folder / 'reconciliation.json').exists()


def test_direct_runtime_without_wrapper_can_be_observed(interrupted):
    _,_,_,receipt,folder,_=interrupted
    instance=json.loads((folder / 'instance.json').read_text())
    instance.pop('launcher_pid')
    instance.pop('launcher_created')
    write_json(folder / 'instance.json',instance)
    write_json(folder / 'runtime.json',{'identity':instance,'trial_digest':receipt['digest']})
    assert flux._observe_abandoned_flux(folder,receipt)['outcome']=='abandoned_without_product'


def test_registered_transition_preserves_attempts_and_binds_fallback(interrupted, monkeypatch):
    store,job,context,receipt,folder,stage=interrupted
    before=store.get(job)
    result=flux.reconcile_interrupted_flux_trial(store.settings,job)
    after=store.get(job)
    checkpoint=next(row for row in after['checkpoints'] if row['stage']==stage)
    assert after['provider_calls']==before['provider_calls']
    assert after['reserved_cost_units']==before['reserved_cost_units']
    assert after['profile_hash']==before['profile_hash']
    assert checkpoint['attempts']==1 and checkpoint['status']=='failed'
    assert after['can_resume'] and not after['worker_live']
    assert after['history'][-1]['event']=='local_operation_reconciled'
    assert result['outcome']=='abandoned_without_product'
    fallback=flux.verify_failed_flux_allows_local_comfy_fallback(context,'s01',receipt,checkpoint)
    assert fallback['fallback_provider']=='comfyui-local' and not fallback['flux_retry_permitted']
    assert not (folder / 'failure.json').exists()
    with pytest.raises(OrderConflict): flux.reconcile_interrupted_flux_trial(store.settings,job)
    altered=json.loads((folder / 'reconciliation.json').read_text())
    altered['prompt_id']='different'
    write_json(folder / 'reconciliation.json',altered)
    with pytest.raises(ProductionBlocked): flux.verify_failed_flux_allows_local_comfy_fallback(context,'s01',receipt,checkpoint)


def test_existing_production_route_reconciles_without_dispatching_worker(interrupted):
    from atnos.api.routes.production import reconcile_production_local_operation
    store,job,_,_,_,_=interrupted
    request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=store.settings)))
    response=reconcile_production_local_operation(request,job)
    assert response['worker_requested'] is False and response['worker_live'] is False
    assert response['status']=='blocked' and response['can_resume'] is True
    assert response['provider_calls']==store.get(job)['provider_calls']
