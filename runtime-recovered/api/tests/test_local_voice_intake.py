"""New local intake uses existing registration; offline fixtures grant no real approval."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from atnos.production.project_profile import ProductionOrder, profile_for_order, load_project_profile, content_hash
from atnos.production.order_store import ProductionOrderStore, ProductionBlocked
from atnos.production.order_execution import StageExecutor
from atnos.production import local_voice_lane as lane
from test_support import build_test_settings


def local_order(**kwargs):
    return ProductionOrder(topic='TEST local intake', project='wealth_blueprint', delivery_mode='local_only',
        voice_candidate='Ryan-B', provider_policy='strict_local', target_duration_seconds=30, **kwargs)


def test_explicit_scope_timing_budget_and_default_request_compatibility(tmp_path):
    settings = build_test_settings(tmp_path)
    order = local_order(direction='Ryan B, bez płatnych API.')
    order.validate_automatic_submission()
    original = load_project_profile(settings, 'wealth_blueprint')
    profile = profile_for_order(original, order)
    assert profile['production']['voice']['provider'] == 'qwen_local'
    assert profile['production']['voice']['voice_id'] == 'Ryan'
    assert profile['production']['max_cost_usd'] == 0
    assert profile['production']['duration_seconds'] == [28, 32]
    assert original['production']['voice']['provider'] == 'elevenlabs'
    assert 'provider_policy' not in ProductionOrder(topic='Legacy request').model_dump(mode='json')
    assert 'target_duration_seconds' not in ProductionOrder(topic='Legacy request').model_dump(mode='json')
    with pytest.raises(ValueError):
        ProductionOrder(topic='Missing strict local selection', voice_candidate='Ryan-B')


def test_ui_availability_never_hashes_model_weights_and_full_preflight_still_does(tmp_path, monkeypatch):
    from atnos.services import local_ctc_alignment_service as ctc
    from unittest.mock import patch
    from pathlib import Path
    for name in ('python.exe', 'worker.py', 'weights.pth'):
        (tmp_path / name).write_bytes(b'fixture')
    monkeypatch.setattr(ctc, 'ROOT', tmp_path)
    monkeypatch.setattr(ctc, 'PYTHON', tmp_path / 'python.exe')
    monkeypatch.setattr(ctc, 'WORKER', tmp_path / 'worker.py')
    monkeypatch.setattr(ctc, 'EXPECTED_WEIGHTS_BYTES', 7)
    monkeypatch.setattr(ctc, 'EXPECTED_WEIGHTS_SHA256', lane.hashlib.sha256(b'fixture').hexdigest())
    with patch.object(Path, 'open', side_effect=AssertionError('UI polling must not read model weights')):
        status = ctc.runtime_availability(verify_hash=False)
    assert status['available'] and status['integrity_check'] == 'deferred_before_inference'
    assert ctc.runtime_availability()['weights_sha256'] == ctc.EXPECTED_WEIGHTS_SHA256
    (tmp_path / 'weights.pth').write_bytes(b'changed')
    assert 'weights_sha256_mismatch' in ctc.runtime_availability()['problems']


@pytest.mark.parametrize('flags', [{'provider_call': True}, {'external_tool_call': True}, {'estimated_cost_usd': .01}])
def test_strict_local_stops_before_any_external_operation(flags):
    executor = StageExecutor.__new__(StageExecutor)
    executor.context = SimpleNamespace(artifacts={'production_order': {'provider_policy': 'strict_local'}})
    operation = AsyncMock(side_effect=AssertionError('must not call a provider'))
    with pytest.raises(ProductionBlocked, match='STRICT_LOCAL'):
        asyncio.run(executor.run('voice', {}, operation, **flags))
    operation.assert_not_called()


def test_registration_is_bound_idempotent_and_does_not_approve_quality(tmp_path, monkeypatch):
    settings = build_test_settings(tmp_path)
    from atnos.intelligence.storage import connect
    with connect(settings) as db:
        db.execute("INSERT INTO intelligence_accounts(account_id,niches,region,last_requested_at) VALUES('yt-wealth-blueprint','[\"finance\"]','US','2026-10-21T00:00:00Z')")
        db.commit()
    sample = tmp_path / 'candidate.wav'
    sample.write_bytes(b'isolated offline sample, not real audio')
    sample_receipt = tmp_path / 'sample.json'
    sample_receipt.write_text(json.dumps({'output_path': str(sample), 'output_sha256': lane.file_hash(sample),
        'sample_id': 'ryan_clear', 'speaker': 'Ryan', 'provider': 'qwen_local', 'model_id': lane.MODEL_ID,
        'revision': lane.MODEL_REVISION, 'instruction': lane.CLEAR_INSTRUCTION,
        'instruction_sha256': lane.hashlib.sha256(lane.CLEAR_INSTRUCTION.encode()).hexdigest()}), encoding='utf-8')
    monkeypatch.setattr(lane, 'INTAKE_SAMPLE_RECEIPT', sample_receipt)
    monkeypatch.setattr(lane, 'verify_runtime_availability', lambda: {'available': True})
    read_json = lane._read_json
    monkeypatch.setattr(lane, '_read_json', lambda path: {'model_id': lane.MODEL_ID, 'revision': lane.MODEL_REVISION,
        'license': 'apache-2.0', 'verified_files': True} if path.name == 'model-provenance.json' else read_json(path))
    selection = lane.validate_intake_voice_selection(settings, local_order())
    store = ProductionOrderStore(settings)
    order = store.create(local_order(), request_key='isolated-local-intake')
    assert order['status'] == 'blocked' and order['current_step'] == 'voice_setup'
    assert not order['can_resume']
    # Simulate a process losing its response after the durable lane was written
    # but before the setup hold was released. Retrying the same intake must
    # recover that registration, without another job or a quality approval.
    register_lane = lane.register_local_voice_lane
    def interrupt_after_registration(*args, **kwargs):
        register_lane(*args, **kwargs)
        raise lane.LocalVoiceLaneError('isolated interruption after durable registration')
    monkeypatch.setattr(lane, 'register_local_voice_lane', interrupt_after_registration)
    with pytest.raises(lane.LocalVoiceLaneError, match='isolated interruption'):
        lane.register_intake_voice_lane(settings, order['id'], selection)
    assert store.get(order['id'])['current_step'] == 'voice_setup'
    assert (store.context(order['id']).artifact_dir / 'voice/local_voice_lane.json').is_file()
    monkeypatch.setattr(lane, 'register_local_voice_lane', register_lane)
    registered = lane.register_intake_voice_lane(settings, order['id'], selection)
    assert registered.preset == lane.CLEAR_PRESET
    assert store.get(order['id'])['status'] == 'queued'
    assert lane.register_intake_voice_lane(settings, order['id'], selection).digest == registered.digest
    context = store.context(order['id'])
    receipt = json.loads((context.artifact_dir / 'voice/intake-selection.json').read_text(encoding='utf-8'))
    assert receipt['quality_approved_by_user'] is False
    assert receipt['final_film_approved'] is False
    assert receipt['request_hash'] == content_hash(store.get(order['id'])['request'])
    assert lane.load_local_voice_lane(settings, order['id']).digest == registered.digest
    # Replay the same ordinary HTTP intake. Dispatch must only see a bound
    # voice lane; a lost response must not create another job or registration.
    from fastapi.testclient import TestClient
    from test_support import create_test_app, bootstrap_test_app
    from atnos.production.order_dispatch import ProductionOrderDispatcher
    observed = []
    def inspect_dispatch(dispatcher, job_id, **kwargs):
        current = ProductionOrderStore(settings).get(job_id)
        bound = lane.load_local_voice_lane(settings, job_id)
        assert bound.request_hash == content_hash(current['request'])
        assert bound.frozen_profile_hash == content_hash(current['profile'])
        assert bound.preset == lane.CLEAR_PRESET
        if job_id == order['id']:
            assert bound.digest == registered.digest
        assert current['request']['provider_policy'] == 'strict_local'
        observed.append(job_id)
        return current
    monkeypatch.setattr(ProductionOrderDispatcher, 'dispatch', inspect_dispatch)
    app = create_test_app(tmp_path)
    bootstrap_test_app(app)
    try:
        with TestClient(app) as client:
            response = client.post('/api/production/orders', headers={'Idempotency-Key': 'isolated-local-intake'}, json=local_order().model_dump(mode='json'))
            fresh = client.post('/api/production/orders', headers={'Idempotency-Key': 'isolated-fresh-local-intake'}, json=local_order().model_dump(mode='json'))
        assert response.status_code == 202, response.text
        assert response.json()['id'] == order['id']
        assert fresh.status_code == 202, fresh.text
        assert fresh.json()['id'] != order['id']
        assert observed == [order['id'], fresh.json()['id']]
    finally:
        app.state.database.dispose()
    # Image setup has its own recovery entry point. It must not release a
    # combined local order before its explicitly selected voice is bound.
    from atnos.production import order_image_setup, wb_image_api
    image_payload = local_order().model_dump(mode='json')
    image_payload.update(image_provider='comfyui-local', image_reference_id='wb-original-board', visual_style_id='A')
    combined = store.create(ProductionOrder(**image_payload), request_key='isolated-combined-intake')
    reference = tmp_path / 'reference.jpg'
    monkeypatch.setattr(order_image_setup, '_REFERENCE_PATH', reference)
    def image_binding(settings, job_id):
        current = store.get(job_id)
        return {'provider': 'comfyui-local', 'reference_path': str(reference.resolve()),
                'request_hash': content_hash(current['request']), 'profile_hash': content_hash(current['profile'])}
    monkeypatch.setattr(wb_image_api, 'load_image_lane', image_binding)
    with pytest.raises(ProductionBlocked, match='voice'):
        order_image_setup._release_setup_hold(settings, combined['id'])
    assert store.get(combined['id'])['current_step'] == 'image_setup'
    lane.register_intake_voice_lane(settings, combined['id'], selection)
    order_image_setup._release_setup_hold(settings, combined['id'])
    assert store.get(combined['id'])['status'] == 'queued'
    sample.write_bytes(b'changed candidate')
    with pytest.raises(lane.LocalVoiceLaneError):
        lane.load_local_voice_lane(settings, order['id'])
