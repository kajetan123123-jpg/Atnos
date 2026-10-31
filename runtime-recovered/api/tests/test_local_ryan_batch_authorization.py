"""A production request permits a candidate; it does not approve new voice quality."""
import json
from types import SimpleNamespace
import pytest
from atnos.production import local_voice_lane as module
from atnos.production.order_store import file_hash
from atnos.production.project_profile import content_hash


def put(path, value):
    path.write_text(json.dumps(value), encoding='utf8')
    return path


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    sample = tmp_path / 'prior.wav'
    sample.write_bytes(b'isolated-sample-no-inference')
    request = {'project':'wealth_blueprint','delivery_mode':'local_only',
               'series_key':'isolated-batch','topic':'Access before balance'}
    state = {'request':request,'profile':{'frozen':True},'status':'queued',
             'worker_live':False,'checkpoints':[]}
    monkeypatch.setattr(module, 'ProductionOrderStore', lambda _: SimpleNamespace(get=lambda _:state))
    authority = put(tmp_path/'request.json', {'status':'USER_REQUESTED_LOCAL_WB_BATCH',
        'series_key':'isolated-batch','user_request':'Create ten local WB reels',
        'paid_calls_authorized':False})
    prior = put(tmp_path/'prior.json', {'decision':'approved_for_pilot_trial',
        'preset':module.PRESET,'sample':str(sample),'sample_sha256':file_hash(sample)})
    receipt = {'job_id':'prod-fixture','decision':'production_requested_candidate_unapproved',
        'preset':module.NEUTRAL_PRESET,'sample':str(sample),'sample_sha256':file_hash(sample),
        'channel_default_changed':False,'frozen_profile_changed':False,'final_film_approved':False,
        'new_sample_approved_by_user':False,'scope':"This order's trial AV only; quality remains unapproved.",
        'request_hash':content_hash(request),'production_request_path':str(authority),
        'production_request_sha256':file_hash(authority),'prior_sample_receipt_path':str(prior),
        'prior_sample_receipt_sha256':file_hash(prior)}
    original = module._read_json
    def read(path):
        if path.name == 'model-provenance.json':
            return {'model_id':module.MODEL_ID,'revision':module.MODEL_REVISION,
                    'license':'apache-2.0','verified_files':True}
        return original(path)
    monkeypatch.setattr(module, '_read_json', read)
    approval = tmp_path/'candidate.json'
    put(approval, receipt)
    return approval, sample, receipt, authority, state


def test_truthful_candidate_binds_neutral_instruction_and_unchanged_profile(candidate):
    approval, sample, _, _, state = candidate
    body = module._lane_body(None,'prod-fixture',approval,sample,require_idle=True)
    assert body['preset'] == module.NEUTRAL_PRESET
    assert 'ten dollars' not in module.instruction_for_preset(body['preset'])
    assert state['profile'] == {'frozen':True}


@pytest.mark.parametrize('change', ['fake_approval','stale_request','changed_authority','foreign_batch','active_worker'])
def test_candidate_rejects_scope_provenance_or_identity_change(candidate, change):
    approval, sample, receipt, authority, state = candidate
    if change == 'fake_approval': receipt['new_sample_approved_by_user'] = True
    elif change == 'stale_request': receipt['request_hash'] = 'old'
    elif change == 'changed_authority': authority.write_text('{}',encoding='utf8')
    elif change == 'foreign_batch': state['request']['series_key'] = 'other'
    else: state['worker_live'] = True
    put(approval,receipt)
    with pytest.raises(module.LocalVoiceLaneError):
        module._lane_body(None,'prod-fixture',approval,sample,require_idle=True)


def test_old_preset_instruction_and_receipt_contract_still_work(candidate):
    approval,sample,receipt,_,_=candidate
    receipt.update(decision='approved_for_pilot_trial',preset=module.PRESET)
    put(approval,receipt)
    body=module._lane_body(None,'prod-fixture',approval,sample,require_idle=True)
    assert body['preset']==module.PRESET
    assert module.instruction_for_preset(module.PRESET)==module.INSTRUCTION
