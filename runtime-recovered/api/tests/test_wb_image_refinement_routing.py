"""Route-level regressions for one-shot local Flux refinement.

These tests reuse the isolated rejected-parent fixture. They never start a
ComfyUI process or contact an image provider.
"""
from __future__ import annotations

import asyncio

import pytest
from PIL import Image

pytest_plugins = (
    "test_wb_flux_refinement",
    "test_wb_image_api_production",
    "test_wb_local_flux_trial",
)


@pytest.fixture
def plain_refinement_lane(monkeypatch):
    """Exercise plain-refinement routing without the separate Minote stage.

    Its receipt/checkpoint integrity is covered by test_wb_flux_style_candidate;
    these synthetic plain receipts intentionally do not provide that evidence.
    """
    from atnos.production import wb_flux_style_candidate as styled
    monkeypatch.setattr(styled, 'load_flux_style_candidate', lambda *_: None)
    monkeypatch.setattr(styled, 'eligible_flux_style_candidate', lambda *_: None)


def test_production_routes_refinement_receipt_through_verification_and_generation(
    trial, isolated_brain, monkeypatch, plain_refinement_lane,
):
    from atnos.production import wb_flux_refinement as refinement
    from atnos.production import wb_image_api as lane
    from atnos.production import wb_local_flux_trial as flux
    from atnos.production.asset_brain.models import AssetRecord
    from atnos.production.channels.wealth_blueprint.remotion_order_edit import SceneAssetsNotReady
    from atnos.production.channels.wealth_blueprint.visual_styles import style_asset_tags
    from atnos.production.steps.common import write_json
    from test_wb_image_api_production import prepared_executor

    store, job, reference = trial
    local_lane = lane.register_local_image_lane(store.settings, job, reference_path=reference, authorized=True)
    parent = {'digest': 'a' * 64, 'version': flux.VERSION, 'source': 'Atnos autonomous original trial'}
    receipt = {'digest': 'b' * 64, 'version': refinement.VERSION,
               'parent_digest': parent['digest'], 'source': 'Atnos autonomous refinement'}
    need = {'type': 'background', 'asset_subject': 'A small unbranded shopping bag beside a blank card.'}
    plan = {'scenes': [{'scene_id': 's01'}]}
    monkeypatch.setattr(lane, 'image_selection_plan', lambda _context: plan)
    monkeypatch.setattr(lane, '_preflight_image_plan', lambda _plan: [(None, need)])
    monkeypatch.setattr(lane, 'load_image_lane', lambda *_: local_lane)
    monkeypatch.setattr(flux, 'load_flux_trial', lambda *_args, **_kwargs: parent)
    monkeypatch.setattr(refinement, 'load_flux_refinement', lambda *_args: receipt)

    context = store.context(job)
    style = context.artifacts['project_profile']['visual_style']
    tags = style_asset_tags(style, job, 's01')
    image = context.artifact_dir / 'isolated-refinement-candidate.png'
    Image.new('RGB', (768, 1344), '#123344').save(image)
    candidate = AssetRecord(id=f"flux_refinement_{receipt['digest'][:16]}", type='background',
        channel='wealth_blueprint', production_role='background', source='comfyui-flux-trial',
        quality_status='AWAITING_REVIEW', file_path=str(image), tags=tags)
    isolated_brain.append(candidate)
    verified, generated = [], []
    monkeypatch.setattr(refinement, 'verify_flux_refinement_asset',
                        lambda asset, used_receipt: verified.append((asset.id, used_receipt['digest'])))
    with store.lease(job) as token, pytest.raises(SceneAssetsNotReady):
        asyncio.run(lane.produce_image_candidates(prepared_executor(store, job, token)))
    assert verified == [(candidate.id, receipt['digest'])]

    isolated_brain.clear()

    def generate(settings, ctx, candidate_need, visual_style, style_tags, selected_lane, fingerprint, used_receipt):
        generated.append(used_receipt)
        image_path = ctx.artifact_dir / 'isolated-generated-refinement.png'
        Image.new('RGB', (768, 1344), '#234455').save(image_path)
        asset = AssetRecord(id=f"flux_refinement_{used_receipt['digest'][:16]}", type='background',
            channel='wealth_blueprint', production_role='background', source='comfyui-flux-trial',
            quality_status='AWAITING_REVIEW', file_path=str(image_path), tags=style_tags)
        isolated_brain.append(asset)
        return write_json(ctx.artifact_dir / 'isolated-generated-refinement.json', {
            'asset_id': asset.id, 'output_path': str(image_path), 'quality_status': asset.quality_status,
        })

    monkeypatch.setattr(flux, 'generate_flux_candidate', generate)
    with store.lease(job) as token, pytest.raises(SceneAssetsNotReady):
        asyncio.run(lane.produce_image_candidates(prepared_executor(store, job, token)))
    assert generated == [receipt]


def test_rejected_refinement_is_never_registered_or_dispatched_again(
    rejected_flux_parent, isolated_brain, monkeypatch, plain_refinement_lane,
):
    from atnos.production import wb_flux_refinement as refinement
    from atnos.production import wb_image_api as lane
    from atnos.production import wb_local_flux_trial as flux
    from atnos.production.steps.common import write_json
    from atnos.production.channels.wealth_blueprint.remotion_order_edit import SceneAssetsNotReady
    from atnos.production.asset_brain.models import AssetGenerationProvenance, AssetRecord
    from test_wb_image_api_production import prepared_executor

    store, job, context, _, _, _, _, _ = rejected_flux_parent
    registrations, flux_posts, local_posts = [], [], []
    register = refinement.register_flux_refinement

    def register_once(*args, **kwargs):
        result = register(*args, **kwargs)
        registrations.append(result)
        return result

    def generate_flux(settings, ctx, need, style, tags, local_lane, fingerprint, authorization):
        flux_posts.append(authorization['digest'])
        image = ctx.artifact_dir / 'isolated-rejected-refinement.png'
        Image.new('RGB', (768, 1344), '#234455').save(image)
        asset = AssetRecord(id=f"flux_refinement_{authorization['digest'][:16]}", type='background',
            channel='wealth_blueprint', production_role='background', source='comfyui-flux-trial',
            quality_status='AWAITING_REVIEW', file_path=str(image), width=768, height=1344, tags=tags,
            generation=AssetGenerationProvenance(prompt=authorization['workflow']['4']['inputs']['text'],
                workflow_id=flux.VERSION, model=flux.MODEL_NAMES['unet'][1], generated_at='2026-10-08',
                source_workflow=flux.VERSION, channel='wealth_blueprint', fingerprint=authorization['digest'],
                evidence_path=str(ctx.artifact_dir / 'isolated-refinement-evidence.json')))
        isolated_brain.append(asset)
        write_json(ctx.artifact_dir / 'isolated-refinement-evidence.json', {'isolated': True})
        return write_json(ctx.artifact_dir / 'isolated-refinement-output.json', {
            'asset_id': asset.id, 'output_path': str(image), 'quality_status': asset.quality_status,
        })

    def generate_local(settings, ctx, need, style, tags, authorization, fingerprint, previous_reference):
        local_posts.append(fingerprint)
        image = ctx.artifact_dir / 'isolated-ordinary-local.png'
        Image.new('RGB', (768, 1344), '#345566').save(image)
        asset = AssetRecord(id='ordinary-local-after-refinement', type='background',
            channel='wealth_blueprint', production_role='background', source='comfyui-test',
            quality_status='AWAITING_REVIEW', file_path=str(image), width=768, height=1344, tags=tags)
        isolated_brain.append(asset)
        return write_json(ctx.artifact_dir / 'isolated-ordinary-output.json', {
            'asset_id': asset.id, 'output_path': str(image), 'quality_status': asset.quality_status,
        })

    async def reject_refinement(executor, asset_id, *_args, **_kwargs):
        for index, asset in enumerate(isolated_brain):
            if asset.id == asset_id:
                if not asset.id.startswith('flux_refinement_'):
                    raise SceneAssetsNotReady([])
                isolated_brain[index] = asset.model_copy(update={'quality_status': 'REJECTED', 'approved': False})
        # The parent fixture already contains real immutable rejection evidence;
        # this controlled gate marks the newly mocked rejected image as proven.
        return {'passed': False, 'user_approved': False}

    monkeypatch.setattr(refinement, 'register_flux_refinement', register_once)
    monkeypatch.setattr(flux, 'generate_flux_candidate', generate_flux)
    monkeypatch.setattr(lane, '_generate_local_candidate', generate_local)
    monkeypatch.setattr('atnos.production.wb_image_review.proven_rejection_count', lambda *_: 1)
    monkeypatch.setattr('atnos.production.wb_image_review.review_candidate', reject_refinement)

    with store.lease(job) as token, pytest.raises(SceneAssetsNotReady):
        asyncio.run(lane.produce_image_candidates(prepared_executor(store, job, token)))
    # Resume follows the consumed refinement with ordinary local generation.
    with store.lease(job) as token, pytest.raises(SceneAssetsNotReady):
        asyncio.run(lane.produce_image_candidates(prepared_executor(store, job, token)))

    assert len(registrations) == 1
    assert flux_posts == [registrations[0]['digest']]
    assert len(local_posts) == 1


def test_exhausted_original_cap_uses_only_a_valid_local_extension(
    trial, isolated_brain, monkeypatch,
):
    from atnos.production import wb_image_api as lane
    from atnos.production.local_image_allowance import (
        effective_local_image_allowance,
        register_local_image_allowance,
    )
    from atnos.production.order_autonomy import enable
    from atnos.production.steps.common import write_json
    from atnos.production.channels.wealth_blueprint.remotion_order_edit import SceneAssetsNotReady
    from atnos.production.asset_brain.models import AssetRecord

    store, job, reference = trial
    local_lane = lane.register_local_image_lane(store.settings, job, reference_path=reference, authorized=True)
    enable(store.settings, job, reason='Isolated test authorizes finite local image recovery.', max_recoveries=6)
    with store.lease(job) as token:
        for number in range(local_lane['max_requests']):
            stage = f'assets/image_local/historical/{number:02d}'
            store.begin(job, token, stage, {'historical': number}, local_model_call=True)
            store.fail(job, token, stage, category='execution_failure', message='Recorded local attempt')
        store.block(job, token, 'image_generation', category='execution_failure',
                    message='Authorized image request limit reached; history and cost remain preserved')
    extension = register_local_image_allowance(store.settings, job, local_lane['max_requests'] + 1,
        reason='Isolated test explicitly authorizes one finite local request extension.', authorized=True)
    assert effective_local_image_allowance(store.settings, job, local_lane) == local_lane['max_requests'] + 1
    assert extension['provider'] == 'comfyui-local'

    calls = []

    def generate_local(settings, context, need, style, tags, authorization, fingerprint, previous_reference):
        calls.append(fingerprint)
        image = context.artifact_dir / 'isolated-extension-candidate.png'
        Image.new('RGB', (768, 1344), '#345566').save(image)
        asset = AssetRecord(id='isolated-extension-candidate', type='background', file_path=str(image),
            channel='wealth_blueprint', production_role='background', quality_status='AWAITING_REVIEW', tags=tags)
        isolated_brain.append(asset)
        return write_json(context.artifact_dir / 'isolated-extension-output.json', {
            'asset_id': asset.id, 'output_path': str(image), 'quality_status': asset.quality_status,
        })

    async def stop_after_generation(*_args, **_kwargs):
        raise SceneAssetsNotReady([])

    monkeypatch.setattr(lane, '_generate_local_candidate', generate_local)
    monkeypatch.setattr(lane, '_generate_candidate', lambda *_: pytest.fail('local extension entered cloud lane'))
    monkeypatch.setattr('atnos.production.wb_image_review.review_candidate', stop_after_generation)
    from test_wb_image_api_production import prepared_executor
    with store.lease(job) as token, pytest.raises(SceneAssetsNotReady):
        asyncio.run(lane.produce_image_candidates(prepared_executor(store, job, token)))
    assert len(calls) == 1
    assert store.get(job)['provider_calls'] == local_lane['max_requests'] + 1


def test_cloud_lane_never_uses_local_extension_or_effective_local_cap(trial, isolated_brain, monkeypatch):
    from atnos.production import wb_image_api as lane
    from atnos.production.local_image_allowance import register_local_image_allowance
    from atnos.production.order_store import OrderConflict, ProductionBlocked

    store, job, reference = trial
    cloud = lane.register_image_lane(store.settings, job, reference_path=reference, authorized=True)
    with store.lease(job) as token:
        for number in range(cloud['max_requests']):
            stage = f'assets/image_api/historical/{number:02d}'
            store.begin(job, token, stage, {'historical': number}, provider_call=True,
                        estimated_cost_usd=cloud['reservation_usd_per_request'])
            store.fail(job, token, stage, category='dependency_failure', message='Recorded cloud attempt')
        store.block(job, token, 'image_generation', category='execution_failure',
                    message='Authorized image request limit reached; history and cost remain preserved')

    with pytest.raises(OrderConflict, match='local ComfyUI'):
        register_local_image_allowance(store.settings, job, cloud['max_requests'] + 1,
            reason='Isolated test attempts an invalid cloud extension.', authorized=True)
    monkeypatch.setattr('atnos.production.local_image_allowance.effective_local_image_allowance',
                        lambda *_: pytest.fail('cloud request consulted local allowance'))
    monkeypatch.setattr(lane, '_generate_candidate', lambda *_: pytest.fail('cloud cap must block before dispatch'))
    from test_wb_image_api_production import prepared_executor
    with store.lease(job) as token, pytest.raises(ProductionBlocked, match='request limit'):
        asyncio.run(lane.produce_image_candidates(prepared_executor(store, job, token)))
    assert store.get(job)['provider_calls'] == cloud['max_requests']
