"""Curated neutral intake on isolated fixtures; no real media approval or inference."""
import copy
import json

import pytest
from fastapi.testclient import TestClient
from atnos.intelligence.storage import connect

from atnos.production import local_voice_lane as lane
from atnos.production.order_dispatch import ProductionOrderDispatcher
from atnos.production.order_store import ProductionOrderStore, file_hash
from atnos.production.project_profile import ProductionOrder, load_project_profile, profile_for_order
from test_support import build_test_settings, create_test_app, bootstrap_test_app


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    root = tmp_path/'neutral'; root.mkdir()
    (root/'sample.wav').write_bytes(b'ISOLATED VOICE REFERENCE FIXTURE')
    sample = {'provider': 'qwen_local', 'preset': lane.NEUTRAL_PRESET, 'model_id': lane.MODEL_ID,
        'revision': lane.MODEL_REVISION, 'speaker': 'Ryan', 'instruction': lane.NEUTRAL_INSTRUCTION,
        'instruction_sha256': lane.hashlib.sha256(lane.NEUTRAL_INSTRUCTION.encode()).hexdigest(),
        'production_enabled': False, 'user_approved': False, 'output_sha256': 'a'*64}
    audition = {'sha256': file_hash(root/'sample.wav'), 'raw_sha256': 'a'*64, 'time_stretch': False}
    review = {'status': 'USER_SELECTED_VOICE_FOR_FURTHER_WB_TRIALS', 'preset': lane.NEUTRAL_PRESET,
        'source_job_id': 'isolated-source', 'channel_default_changed': False, 'final_film_approved': False,
        'user_answer': 'ISOLATED FIXTURE TRIAL ASSESSMENT', 'video_sha256': 'b'*64,
        'feedback': {'active': True, 'category': 'voice', 'scope': 'film', 'source_job_id': 'isolated-source',
            'note': 'ISOLATED FIXTURE TRIAL ASSESSMENT '+('b'*64)}}
    for name, value in [('sample-receipt.json', sample), ('audition.json', audition), ('user-assessment.json', review)]:
        (root/name).write_text(json.dumps(value), encoding='utf-8')
    manifest = {'schema_version': 1, 'candidate_id': 'Ryan-neutral', 'preset': lane.NEUTRAL_PRESET,
        'status': 'candidate_for_further_trials', 'channel_default_changed': False, 'final_film_approved': False,
        'model_id': lane.MODEL_ID, 'model_revision': lane.MODEL_REVISION, 'source_job_id': 'isolated-source',
        'files': {p.name: file_hash(p) for p in root.iterdir()}}
    (root/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    monkeypatch.setattr(lane, 'NEUTRAL_INTAKE_ROOT', root)
    monkeypatch.setattr(lane, 'verify_runtime_availability', lambda **_: {'available': True})
    original = lane._read_json
    monkeypatch.setattr(lane, '_read_json', lambda p: {'model_id': lane.MODEL_ID, 'revision': lane.MODEL_REVISION,
        'license': 'apache-2.0', 'verified_files': True} if p.name == 'model-provenance.json' else original(p))
    return root


def order(**kw):
    return ProductionOrder(topic='ISOLATED neutral intake', voice_candidate='Ryan-neutral',
        provider_policy='strict_local', delivery_mode='local_only', target_duration_seconds=40, **kw)


def test_neutral_choice_freezes_its_own_preset_without_changing_defaults(tmp_path, candidate):
    settings = build_test_settings(tmp_path)
    original = load_project_profile(settings, 'wealth_blueprint'); before = copy.deepcopy(original)
    req = order(direction='Spokojny Ryan. Bez płatnych API.'); req.validate_automatic_submission()
    scoped = profile_for_order(original, req)
    assert scoped['voice_candidate']['preset'] == lane.NEUTRAL_PRESET
    assert scoped['production']['max_cost_usd'] == 0
    assert scoped['production']['voice']['provider'] == 'qwen_local'
    assert original == before
    with pytest.raises(ValueError):ProductionOrder(topic='Unscoped neutral voice', voice_candidate='Ryan-neutral')


def test_normal_api_binds_neutral_before_dispatch_and_reuses_idempotency_key(tmp_path, candidate, monkeypatch):
    observed = []
    def dispatch(self, job_id):
        state = self.store.get(job_id)
        bound = lane.load_local_voice_lane(self.settings, job_id)
        assert bound.preset == lane.NEUTRAL_PRESET
        assert state['request']['voice_candidate'] == 'Ryan-neutral'
        assert state['current_step'] == 'queued'
        observed.append(job_id)
        return state
    monkeypatch.setattr(ProductionOrderDispatcher, 'dispatch', dispatch)
    app = create_test_app(tmp_path); bootstrap_test_app(app)
    with connect(app.state.settings) as db:
        db.execute("INSERT INTO intelligence_accounts(account_id,niches,region,last_requested_at) VALUES('yt-wealth-blueprint','[]','US','2026-10-30T00:00:00Z')")
        db.commit()
    try:
        with TestClient(app) as client:
            a = client.post('/api/production/orders', headers={'Idempotency-Key': 'neutral-fixture'}, json=order().model_dump(mode='json'))
            b = client.post('/api/production/orders', headers={'Idempotency-Key': 'neutral-fixture'}, json=order().model_dump(mode='json'))
            sample = client.get('/api/production/voice-candidates/Ryan-neutral/sample')
        assert a.status_code == b.status_code == 202, a.text
        assert a.json()['id'] == b.json()['id'] and observed == [a.json()['id']]*2
        assert sample.status_code == 200 and sample.content == (candidate/'sample.wav').read_bytes()
        assert sample.headers['content-disposition'].startswith('inline;')
        root = ProductionOrderStore(app.state.settings).context(a.json()['id']).artifact_dir
        receipt = json.loads((root/'voice/intake-selection.json').read_text())
        assert receipt['candidate_id'] == 'Ryan-neutral' and receipt['quality_approved_by_user'] is False
        assert receipt['channel_default_changed'] is False and receipt['final_film_approved'] is False
    finally:app.state.database.dispose()


@pytest.mark.parametrize('name', ['sample.wav', 'sample-receipt.json', 'audition.json', 'user-assessment.json'])
def test_changed_neutral_reference_blocks_instead_of_falling_back(candidate, name):
    (candidate/name).write_bytes(b'changed')
    with pytest.raises(lane.LocalVoiceLaneError):lane.intake_voice_sample('Ryan-neutral')


def test_forged_global_approval_in_curated_manifest_is_rejected(candidate):
    path = candidate/'manifest.json'; value = json.loads(path.read_text())
    value['channel_default_changed'] = True; path.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(lane.LocalVoiceLaneError):lane.intake_voice_sample('Ryan-neutral')
