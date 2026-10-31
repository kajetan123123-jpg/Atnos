from __future__ import annotations
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
from PIL import Image
import pytest

from atnos.production import wb_image_api as lane
from atnos.production.asset_brain.models import AssetRecord
from atnos.production.channels.wealth_blueprint.remotion_order_edit import SceneAssetsNotReady
from atnos.production.order_execution import StageExecutor
from atnos.production.order_store import OrderConflict, ProductionBlocked, cost_units
from atnos.production.project_profile import ProductionOrder
from atnos.production.steps.common import write_json
from test_production_order_store import store
from test_scoped_script_revision import four_thoughts


@pytest.fixture
def trial(store, tmp_path, four_thoughts, monkeypatch):
    monkeypatch.setattr('atnos.integrations.claim_evidence_gate.ensure_media_claim_gate', lambda **_: {'blocked': False})
    job = store.create(ProductionOrder(topic='Where a pay raise goes', delivery_mode='local_only', visual_style_id='A'))['id']
    reference = tmp_path / 'inspiration.png'
    Image.new('RGB', (300, 200), '#132938').save(reference)
    script = {**four_thoughts.model_dump(mode='json'), 'qa': {'status': 'passed'}}
    # Whole-plan preflight requires every nonnative scene to be a supported
    # physical need, so these controlled scenes cover several supported forms.
    script['segments'][0]['visual_intent'] = 'A plain unprinted bill prop sits beside a small unbranded shopping bag.'
    script['segments'][1]['visual_intent'] = 'A coffee cup stays upright and uncrossed; no red X is visible. That is not the point.'
    script['segments'][2]['visual_intent'] = 'A simple balance scale with two empty unmarked pans.'
    script['segments'][3]['visual_intent'] = 'A bill sits beside an investment account on a phone screen.'
    with store.lease(job) as token:
        store.begin(job, token, 'script', {'source': 'isolated test'})
        artifact = write_json(store.context(job).artifact_dir / 'script.json', script)
        store.complete(job, token, 'script', script, [artifact.artifact_path])
        store.persist_legacy_artifact(job, token, 'script', script)
        store.block(job, token, 'scripting', category='test_hold', message='ready for image trial')
    return store, job, reference


def authorize(trial):
    store, job, reference = trial
    return lane.register_image_lane(store.settings, job, reference_path=reference, authorized=True)


def prepared_executor(store, job, token):
    executor = StageExecutor(store, job, token)
    executor.context.artifacts['script'] = next(row['output'] for row in store.get(job)['checkpoints']
                                               if row['stage'] == 'script')
    return executor


def test_authorization_keeps_frozen_profile_budget_and_approval(trial):
    store, job, reference = trial
    before = store.get(job)
    with pytest.raises(ValueError, match='Explicit'):
        lane.register_image_lane(store.settings, job, reference_path=reference)
    payload = authorize(trial)
    after = store.get(job)
    assert payload['appearance_approval'] == 'pending'
    assert (after['profile_hash'], after['provider_calls'], after['reserved_cost_units']) == (
        before['profile_hash'], before['provider_calls'], before['reserved_cost_units'])
    assert authorize(trial)['digest'] == payload['digest']
    with store.lease(job), pytest.raises(OrderConflict, match='stopped'):
        authorize(trial)


def test_direct_local_selection_creates_local_receipt_without_cloud_lane(trial):
    store, job, reference = trial
    before = store.get(job)
    selected = lane.register_local_image_lane(store.settings, job, reference_path=reference, authorized=True)
    receipt = store.context(job).artifact_dir / 'image_local_authorization.json'
    assert selected['provider'] == 'comfyui-local'
    assert selected['reservation_usd_per_request'] == 0
    assert selected['appearance_approval'] == 'pending'
    assert 'explicitly selected local ComfyUI' in selected['source']
    assert selected['reference_path'] == str(reference.resolve())
    assert receipt.is_file()
    assert not (store.context(job).artifact_dir / 'image_api_authorization.json').exists()
    assert lane.load_image_lane(store.settings, job)['digest'] == selected['digest']
    assert lane.register_local_image_lane(store.settings, job, reference_path=reference,
                                          authorized=True)['digest'] == selected['digest']
    after = store.get(job)
    assert (after['profile_hash'], after['provider_calls'], after['reserved_cost_units']) == (
        before['profile_hash'], before['provider_calls'], before['reserved_cost_units'])


def test_image_selection_plan_preserves_segment_visual_intent(trial, monkeypatch):
    store, job, _ = trial
    script = next(row['output'] for row in store.get(job)['checkpoints'] if row['stage'] == 'script')
    script['segments'][0]['visual_intent'] = 'A quiet desk with a sealed envelope'
    captured = {}

    def planner(value):
        captured['input'] = value
        return SimpleNamespace(model_dump=lambda mode: {'scenes': []})

    monkeypatch.setattr('atnos.production.channels.wealth_blueprint.deterministic.plan_scenes_deterministic', planner)
    context = store.context(job)
    context.artifacts['script'] = script
    lane.image_selection_plan(context)
    assert captured['input'].segments[0].visual_intent == 'A quiet desk with a sealed envelope'


def test_native_scene_trial_skip_requires_verified_role_and_real_renderer_contract():
    chart = {
        'layout_id': 'chart_focus', 'visual_focus': 'chart',
        'chart': {'values': [10.0, 100.0], 'claim_ids': ['c1']},
        'meaning_contract': {'native_scene_role': 'chart'},
    }
    assert lane._native_scene_for_image_trial(chart, index=1, total=3, claims=[{'id': 'c1'}]) == 'chart'
    invalid_chart = {**chart, 'chart': {'values': [10.0], 'claim_ids': ['missing']}}
    assert lane._native_scene_for_image_trial(invalid_chart, index=1, total=3, claims=[{'id': 'c1'}]) is None

    end_card = {
        'layout_id': 'fullscreen_statement', 'visual_focus': 'statement',
        'meaning_contract': {'native_scene_role': 'end_card'},
        'source_visual_intents': ['Wealth Blueprint logo appears for the closing call to action.'],
        'narration_excerpt': 'Follow Wealth Blueprint for more practical choices.',
    }
    assert lane._native_scene_for_image_trial(end_card, index=2, total=3, claims=[]) == 'end_card'
    assert lane._native_scene_for_image_trial(end_card, index=1, total=3, claims=[]) is None
    payoff={**end_card,'narration_excerpt':'Before buying, consider what you are giving up.'}
    assert lane._native_scene_for_image_trial(payoff,index=2,total=3,claims=[])=='end_card'
    physical={**payoff,'source_visual_intents':['A shopping bag with the Wealth Blueprint logo printed on it.']}
    assert lane._native_scene_for_image_trial(physical,index=2,total=3,claims=[]) is None
    unsupported = {
        'layout_id': 'fullscreen_statement', 'visual_focus': 'statement',
        'meaning_contract': {}, 'source_visual_intents': ['A title appears.'],
    }
    assert lane._native_scene_for_image_trial(unsupported, index=2, total=3, claims=[]) is None


@pytest.mark.parametrize('tamper', ['reference', 'receipt'])
def test_tampering_blocks_before_paid_work(trial, tamper):
    store, job, reference = trial
    authorize(trial)
    if tamper == 'reference':
        Image.new('RGB', (300, 200), '#ffffff').save(reference)
    else:
        receipt = store.context(job).artifact_dir / 'image_api_authorization.json'
        data = json.loads(receipt.read_text())
        data['model'] = 'another-model'
        receipt.write_text(json.dumps(data))
    before = store.get(job)['provider_calls']
    with pytest.raises(ProductionBlocked, match='integrity'):
        lane.load_image_lane(store.settings, job)
    assert store.get(job)['provider_calls'] == before


def test_no_authorization_never_uses_provider(trial, monkeypatch):
    store, job, _ = trial
    monkeypatch.setattr(lane, '_generate_candidate', lambda *_: pytest.fail('unauthorized provider call'))
    with store.lease(job) as token:
        asyncio.run(lane.produce_image_candidates(StageExecutor(store, job, token)))
    assert store.get(job)['provider_calls'] == 0


@pytest.fixture
def isolated_brain(monkeypatch):
    assets = []
    class Brain:
        def __init__(self, settings):
            self.catalog = SimpleNamespace(assets=assets)
        def resolve_file(self, asset):
            return Path(asset.file_path)
    monkeypatch.setattr('atnos.production.asset_brain.service.WealthBlueprintAssetBrain', Brain)
    return assets


def test_one_candidate_then_review_resume_never_spends_again(trial, isolated_brain, monkeypatch):
    store, job, _ = trial
    authorize(trial)
    calls = []
    def generate(settings, context, need, style, tags, authorization, fingerprint, previous_reference):
        calls.append(fingerprint)
        assert previous_reference is None
        image = context.artifact_dir / 'actual-candidate.png'
        Image.new('RGB', (1024, 1536), '#163142').save(image)
        asset = AssetRecord(id='test-image', type='background', file_path=str(image), channel='wealth_blueprint',
            quality_status='AWAITING_REVIEW', production_role='background', tags=tags)
        isolated_brain.append(asset)
        return write_json(context.artifact_dir / 'candidate.json', {'asset_id': asset.id, 'output_path': str(image),
            'quality_status': asset.quality_status})
    monkeypatch.setattr(lane, '_generate_candidate', generate)
    before = store.get(job)
    for _ in range(2):
        with store.lease(job) as token, pytest.raises(SceneAssetsNotReady):
            asyncio.run(lane.produce_image_candidates(prepared_executor(store, job, token)))
    after = store.get(job)
    assert len(calls) == 1
    assert after['provider_calls'] == before['provider_calls'] + 1
    assert after['reserved_cost_units'] > before['reserved_cost_units']
    assert not any(row['stage'].startswith('voice') for row in after['checkpoints'])
    assert isolated_brain[0].approved is False


def test_autonomous_local_lane_rejects_then_regenerates_and_finishes_all_scenes(trial,isolated_brain,monkeypatch):
    from atnos.production.order_autonomy import enable
    store,job,reference=trial
    lane.register_local_image_lane(store.settings,job,reference_path=reference,authorized=True)
    enable(store.settings,job,reason='User authorizes independent automatic QC for this local production.')
    generated,reviewed=[],[]

    def generate(settings,context,need,style,tags,authorization,fingerprint,previous_reference):
        image=context.artifact_dir/(fingerprint+'.png')
        Image.new('RGB',(1024,1536),'#163142').save(image)
        asset=AssetRecord(id='candidate-'+fingerprint,type='background',file_path=str(image),channel='wealth_blueprint',
                          quality_status='AWAITING_REVIEW',production_role='background',tags=tags)
        isolated_brain.append(asset)
        generated.append(asset.id)
        return write_json(context.artifact_dir/(fingerprint+'.json'),
                          {'asset_id':asset.id,'output_path':str(image),'quality_status':asset.quality_status})

    async def review(executor,asset_id,*args):
        reviewed.append(asset_id)
        index=next(i for i,asset in enumerate(isolated_brain) if asset.id==asset_id)
        # The first observation fails; a separate next generation succeeds.
        isolated_brain[index]=isolated_brain[index].model_copy(update={
            'quality_status':'REJECTED' if len(reviewed)==1 else 'APPROVED',
            'approved':len(reviewed)>1})
        return {'passed':len(reviewed)>1,'user_approved':False}

    monkeypatch.setattr(lane,'_generate_local_candidate',generate)
    monkeypatch.setattr('atnos.production.wb_image_review.review_candidate',review)
    before=store.get(job)
    with store.lease(job) as token:
        report=asyncio.run(lane.produce_image_candidates(prepared_executor(store,job,token)))
    assert report['waiting']==[]
    assert len(report['qualified_assets'])==4
    gate=next(row for row in store.get(job)['checkpoints'] if row['stage']=='image_generation')
    assert gate['status']=='done'
    gate_hash=gate['files'][0]['sha256']
    assert len(generated)==len(reviewed)==5
    assert isolated_brain[0].quality_status=='REJECTED'
    assert store.get(job)['provider_calls']==before['provider_calls']+5
    with store.lease(job) as token:
        asyncio.run(lane.produce_image_candidates(prepared_executor(store,job,token)))
    assert len(generated)==5  # Qualified cached scenes never regenerate on resume.
    cached_gate=next(row for row in store.get(job)['checkpoints'] if row['stage']=='image_generation')
    assert cached_gate['attempts']==gate['attempts'] and cached_gate['files'][0]['sha256']==gate_hash


def test_uncertain_http_outcome_stops_retry_and_resume(trial, isolated_brain, monkeypatch):
    store, job, _ = trial
    authorize(trial)
    calls = []
    def timeout(*args):
        calls.append('HTTP dispatched')
        raise httpx.ReadTimeout('no response after request')
    monkeypatch.setattr(lane, '_generate_candidate', timeout)
    for _ in range(2):
        with store.lease(job) as token, pytest.raises(ProductionBlocked):
            asyncio.run(lane.produce_image_candidates(prepared_executor(store, job, token)))
    assert len(calls) == 1
    assert store.get(job)['provider_calls'] == 1
    checkpoints = [r for r in store.get(job)['checkpoints'] if r['stage'].startswith('assets/image_api/')]
    assert len(checkpoints) == 1 and checkpoints[0]['attempts'] == 1


def test_empty_balance_429_is_not_a_rate_limit_retry(trial, isolated_brain, monkeypatch):
    store, job, _ = trial
    authorize(trial)
    calls = []
    def exhausted(*args):
        calls.append('HTTP rejected')
        response = httpx.Response(429, json={'error': {'code': 'credit_balance_exhausted'}},
                                  request=httpx.Request('POST', 'https://api.openai.com/v1/images/edits'))
        raise httpx.HTTPStatusError('OpenAI image API returned HTTP 429 (credit_balance_exhausted)',
                                   request=response.request, response=response)
    monkeypatch.setattr(lane, '_generate_candidate', exhausted)
    for _ in range(2):
        with store.lease(job) as token, pytest.raises(ProductionBlocked):
            asyncio.run(lane.produce_image_candidates(prepared_executor(store, job, token)))
    assert calls == ['HTTP rejected']
    assert store.get(job)['provider_calls'] == 1


def test_invalid_image_output_does_not_pay_for_another_drawing(trial, isolated_brain, monkeypatch):
    store, job, _ = trial
    authorize(trial)
    calls = []
    def invalid(*args):
        calls.append('image response')
        raise ValueError('Invalid image dimensions')
    monkeypatch.setattr(lane, '_generate_candidate', invalid)
    with store.lease(job) as token, pytest.raises(ProductionBlocked):
        asyncio.run(lane.produce_image_candidates(prepared_executor(store, job, token)))
    assert calls == ['image response']
    assert store.get(job)['provider_calls'] == 1


def reserve_historical_cloud_calls(store, job):
    """Use the real store accounting, without dispatching any provider request."""
    with store.lease(job) as token:
        for number in range(20):
            stage = f'assets/image_api/historical/{number:02d}'
            store.begin(job, token, stage, {'historical_request': number},
                        provider_call=True, estimated_cost_usd=1.0)
            store.fail(job, token, stage, category='dependency_failure',
                       message='credit_balance_exhausted: preserved historical reservation')


def test_local_override_preserves_twenty_cloud_reservations_and_uses_only_normal_local_acquisition(
    trial, isolated_brain, monkeypatch,
):
    store, job, _ = trial
    cloud = authorize(trial)
    cloud_receipt = store.context(job).artifact_dir / 'image_api_authorization.json'
    original_receipt_bytes = cloud_receipt.read_bytes()
    reserve_historical_cloud_calls(store, job)
    before = store.get(job)
    assert before['provider_calls'] == 20
    assert before['reserved_cost_units'] == cost_units(20.0)
    historical = [row for row in before['checkpoints'] if row['stage'].startswith('assets/image_api/')]

    with pytest.raises(ValueError, match='Explicit local'):
        lane.register_local_image_lane(store.settings, job)
    selected = lane.register_local_image_lane(store.settings, job, authorized=True)
    assert selected['provider'] == 'comfyui-local'
    assert selected['reservation_usd_per_request'] == 0
    assert selected['supersedes_digest'] == cloud['digest']
    assert selected['appearance_approval'] == 'pending'
    assert lane.register_local_image_lane(store.settings, job, authorized=True)['digest'] == selected['digest']
    assert cloud_receipt.read_bytes() == original_receipt_bytes

    calls = []
    source_bytes = []
    def acquire(settings, need, *, allow_generate, visual_style, style_tags):
        assert settings is store.settings
        assert allow_generate is True
        assert need['type'] == 'background' and need['asset_subject']
        assert visual_style['id'] == 'A'
        source = store.context(job).artifact_dir / 'isolated-comfy-output' / 'image.png'
        source.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (768, 1344), '#173246').save(source)
        (source.parent / 'metadata.json').write_text(json.dumps({'model': 'isolated local fixture'}))
        (source.parent / 'image.png.comfyui.json').write_text(json.dumps({'graph': 'isolated fixture'}))
        source_bytes.append((source, source.read_bytes()))
        asset = AssetRecord(id='isolated-local-candidate', type='background', file_path=str(source),
            channel='wealth_blueprint', quality_status='AWAITING_REVIEW', production_role='background',
            source='comfyui-test', tags=style_tags)
        isolated_brain.append(asset)
        calls.append(need)
        return None, {'status': 'awaiting_approval', 'asset_id': asset.id, 'output_path': str(source),
                      'quality_status': 'AWAITING_REVIEW'}

    monkeypatch.setattr('atnos.production.channels.wealth_blueprint.scene_production_assets._try_acquisition', acquire)
    monkeypatch.setattr(lane, '_generate_candidate', lambda *args: pytest.fail('local override must not enter cloud lane'))
    monkeypatch.setattr('atnos.services.creative_asset_operator_service.CreativeAssetOperatorService.generate_image_api_candidate',
                        lambda *args, **kwargs: pytest.fail('local override must never dispatch image API'))
    for _ in range(2):
        with store.lease(job) as token, pytest.raises(SceneAssetsNotReady):
            asyncio.run(lane.produce_image_candidates(prepared_executor(store, job, token)))
    after = store.get(job)
    assert len(calls) == 1
    assert after['provider_calls'] == 21  # One local model call; still counted by normal admission.
    assert after['reserved_cost_units'] == before['reserved_cost_units'] == cost_units(20.0)
    assert after['profile_hash'] == before['profile_hash']
    assert after['profile'] == before['profile'] and after['request'] == before['request']
    assert [row for row in after['checkpoints'] if row['stage'].startswith('assets/image_api/')] == historical
    local_checkpoints = [row for row in after['checkpoints'] if row['stage'].startswith('assets/image_local/')]
    assert len(local_checkpoints) == 1
    assert local_checkpoints[0]['attempts'] == 1 and local_checkpoints[0]['status'] == 'done'
    assert local_checkpoints[0]['output']['provider'] == 'comfyui-local'
    assert local_checkpoints[0]['output']['user_approved'] is False
    assert not any(row['stage'].startswith('voice') for row in after['checkpoints'])
    assert isolated_brain[0].approved is False
    assert all(path.is_relative_to(store.settings.data_dir) and path.read_bytes() == original
               for path, original in source_bytes)
    assert cloud_receipt.read_bytes() == original_receipt_bytes


@pytest.mark.parametrize('tamper', ['local_receipt', 'cloud_receipt', 'reference'])
def test_local_override_receipt_and_original_authorization_stay_immutable_before_any_generation(
    trial, isolated_brain, monkeypatch, tamper,
):
    store, job, reference = trial
    authorize(trial)
    selected = lane.register_local_image_lane(store.settings, job, authorized=True)
    assert selected['provider'] == 'comfyui-local'
    if tamper == 'reference':
        Image.new('RGB', (300, 200), '#ffffff').save(reference)
    else:
        filename = 'image_local_authorization.json' if tamper == 'local_receipt' else 'image_api_authorization.json'
        path = store.context(job).artifact_dir / filename
        changed = json.loads(path.read_text())
        changed['provider'] = 'unapproved-provider'
        path.write_text(json.dumps(changed))
    before = store.get(job)
    monkeypatch.setattr(lane, '_generate_local_candidate', lambda *args: pytest.fail('tampered local authorization'))
    monkeypatch.setattr(lane, '_generate_candidate', lambda *args: pytest.fail('tampered cloud authorization'))
    with pytest.raises(ProductionBlocked, match='integrity'):
        lane.load_image_lane(store.settings, job)
    with store.lease(job) as token, pytest.raises(ProductionBlocked, match='integrity'):
        asyncio.run(lane.produce_image_candidates(prepared_executor(store, job, token)))
    after = store.get(job)
    assert after['provider_calls'] == before['provider_calls']
    assert after['reserved_cost_units'] == before['reserved_cost_units']
    assert after['checkpoints'] == before['checkpoints']


def test_local_override_refuses_active_worker_before_changing_provider(trial):
    store, job, _ = trial
    cloud = authorize(trial)
    with store.lease(job), pytest.raises(OrderConflict, match='stopped'):
        lane.register_local_image_lane(store.settings, job, authorized=True)
    assert lane.load_image_lane(store.settings, job)['digest'] == cloud['digest']
    assert not (store.context(job).artifact_dir / 'image_local_authorization.json').exists()
