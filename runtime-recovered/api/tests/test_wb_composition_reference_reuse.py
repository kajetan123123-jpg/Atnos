"""Offline boundary tests: an explicit reference must survive both reuse layers."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image
import pytest

from atnos.production import wb_image_api as lane
from atnos.production.asset_brain.models import AssetRecord
from atnos.production.channels.wealth_blueprint.remotion_order_edit import SceneAssetsNotReady
from atnos.production.channels.wealth_blueprint.visual_styles import freeze_visual_style, style_asset_tags
from atnos.production.order_comfy_routing import OrderAssetRequest, OrderComfyAssetFulfiller
from atnos.production.order_store import ProductionBlocked, file_hash
from test_comfy_order_routing import settings, fake_service
from test_production_order_store import store
from test_scoped_script_revision import four_thoughts
from test_wb_image_api_production import trial, isolated_brain, prepared_executor


class StopBeforeDispatch(Exception):
    pass


@pytest.mark.parametrize('with_reference', [False, True])
def test_pending_style_match_cannot_replace_explicit_reference(settings, fake_service, tmp_path, with_reference):
    asset = AssetRecord(id='prior-unconditioned', type='background', channel='wealth_blueprint',
        production_role='background', quality_status='GENERATED', file_path=str(tmp_path/'prior.png'))
    brain = SimpleNamespace(select_best=lambda query: SimpleNamespace(
        status='missing' if query.require_approved else 'found',
        match=None if query.require_approved else SimpleNamespace(asset=asset), generation_need=None),
                            resolve_file=lambda candidate: Path(candidate.file_path))
    fulfiller = OrderComfyAssetFulfiller(settings, channel_service=fake_service, asset_brain=brain)
    style = freeze_visual_style('D')
    reference = tmp_path / 'composition.png'
    Image.new('RGB', (8, 8), 'navy').save(reference)
    request = OrderAssetRequest(channel_id='wealth_blueprint', asset_type='background', subject='card and bag',
        visual_style_id='D', visual_style_prompt=style['prompt'], visual_style_tags=[style['asset_tag']],
        visual_style_definition_hash=style['definition_hash'],
        references=[str(reference)] if with_reference else [], seed=531990040)
    result = fulfiller.fulfill(request)
    if with_reference:
        assert len(fake_service.calls) == 1, 'Pending style hit silently discarded the explicit reference'
        assert fake_service.calls[0].references == [str(reference)]
        assert fake_service.calls[0].seed == 531990040
        assert result.asset_brain_id != asset.id
    else:
        assert fake_service.calls == []
        assert result.asset_brain_id == asset.id


def conditioned_executor(trial, monkeypatch, *, autonomy=False):
    store, job, brand = trial
    local_lane = lane.register_local_image_lane(store.settings, job, reference_path=brand, authorized=True)
    context = store.context(job)
    reference = context.artifact_dir / 'composition.png'
    Image.new('RGB', (8, 8), '#123456').save(reference)
    need = {'type':'background', 'asset_subject':'one thin card beside one bag',
            'composition_reference_path':str(reference), 'seed':531990040}
    monkeypatch.setattr(lane, 'image_selection_plan', lambda _: {'scenes':[{'scene_id':'s01'}], 'claims':[]})
    monkeypatch.setattr(lane, '_preflight_image_plan', lambda _: [(False, need)])
    monkeypatch.setattr('atnos.production.wb_local_flux_trial.load_flux_trial', lambda *_args, **_kwargs: None)
    monkeypatch.setattr('atnos.production.order_autonomy.policy', lambda *_: {
        'enabled':True, 'recoveries_used':0, 'max_recoveries':3, 'last_block_key':None} if autonomy else None)
    monkeypatch.setattr('atnos.production.wb_image_review.verify_qualification', lambda *_: None)
    monkeypatch.setattr(lane, '_generate_local_candidate', lambda *_: (_ for _ in ()).throw(StopBeforeDispatch()))
    return store, job, context, reference, need, local_lane


def conditioning_receipt(source, reference, seed):
    evidence = {'reference_upload': {'source_path':str(reference), 'source_sha256':file_hash(reference),
        'resolved_image_name':'fixture.png'}, 'workflow':{
        '4':{'class_type':'LoadImage','inputs':{'image':'fixture.png'}},
        '5':{'class_type':'ImageScale','inputs':{'image':['4',0]}},
        '6':{'class_type':'VAEEncode','inputs':{'pixels':['5',0]}},
        '7':{'class_type':'KSampler','inputs':{'latent_image':['6',0], 'seed':seed}}}}
    source.with_suffix('.png.comfyui.json').write_text(json.dumps(evidence), encoding='utf8')


@pytest.mark.parametrize('quality_status', ['APPROVED','AWAITING_REVIEW'])
@pytest.mark.parametrize('evidence_kind', ['absent','wrong_reference','wrong_seed','matching'])
def test_image_lane_reuse_requires_current_reference_and_seed(trial, isolated_brain, monkeypatch, quality_status, evidence_kind):
    store, job, context, reference, need, _ = conditioned_executor(trial, monkeypatch)
    style = context.artifacts['project_profile']['visual_style']
    source = context.artifact_dir / 'candidate.png'
    Image.new('RGB', (768,1344), '#123344').save(source)
    candidate = AssetRecord(id='reference-reuse-candidate', type='background', channel='wealth_blueprint',
        production_role='background', source='comfyui', quality_status=quality_status,
        file_path=str(source), tags=style_asset_tags(style, job, 's01'))
    isolated_brain.append(candidate)
    if evidence_kind != 'absent':
        old_reference = context.artifact_dir / 'old-reference.png'
        Image.new('RGB', (8,8), '#ffffff').save(old_reference)
        conditioning_receipt(source, old_reference if evidence_kind=='wrong_reference' else reference,
                             1 if evidence_kind=='wrong_seed' else need['seed'])
    with store.lease(job) as token:
        executor = prepared_executor(store, job, token)
        async def stop_at_callback(stage, inputs, callback, **kwargs):
            return await callback()
        executor.run = AsyncMock(side_effect=stop_at_callback)
        if evidence_kind=='matching':
            if quality_status=='APPROVED':
                asyncio.run(lane.produce_image_candidates(executor))
                # Reuse still finalizes the normal image inventory checkpoint;
                # it must not dispatch a new per-candidate generation stage.
                executor.run.assert_awaited_once()
                assert executor.run.await_args.args[0] == 'image_generation'
                assert not executor.run.await_args.kwargs.get('provider_call')
            else:
                with pytest.raises(SceneAssetsNotReady):
                    asyncio.run(lane.produce_image_candidates(executor))
                executor.run.assert_not_awaited()
        else:
            with pytest.raises(StopBeforeDispatch):
                asyncio.run(lane.produce_image_candidates(executor))
            executor.run.assert_awaited_once()


def test_explicit_pixel_conditioning_never_auto_selects_unconditioned_flux(trial, isolated_brain, monkeypatch):
    store, job, *_ = conditioned_executor(trial, monkeypatch, autonomy=True)
    monkeypatch.setattr('atnos.production.wb_image_review.proven_rejection_count', lambda *_: 2)
    registrations = []
    def register(*args, **kwargs):
        registrations.append(args)
        raise AssertionError('Autonomous Flux ignored explicit composition conditioning')
    monkeypatch.setattr('atnos.production.wb_local_flux_trial.register_flux_trial', register)
    with store.lease(job) as token:
        executor = prepared_executor(store, job, token)
        async def stop_at_callback(stage, inputs, callback, **kwargs):
            return await callback()
        executor.run = AsyncMock(side_effect=stop_at_callback)
        with pytest.raises(StopBeforeDispatch):
            asyncio.run(lane.produce_image_candidates(executor))
    assert registrations == []


def test_existing_flux_receipt_conflict_blocks_without_new_stage(trial, isolated_brain, monkeypatch):
    store, job, *_ = conditioned_executor(trial, monkeypatch)
    monkeypatch.setattr('atnos.production.wb_local_flux_trial.load_flux_trial',
                        lambda *_args, **_kwargs: {'digest':'a'*64, 'source':'existing scoped Flux trial'})
    before = store.get(job)
    with store.lease(job) as token:
        executor = prepared_executor(store, job, token)
        executor.run = AsyncMock(side_effect=AssertionError('Conflict dispatched a new stage'))
        with pytest.raises(ProductionBlocked, match='reconcile its scoped authorization'):
            asyncio.run(lane.produce_image_candidates(executor))
        executor.run.assert_not_awaited()
    after = store.get(job)
    assert after['provider_calls']==before['provider_calls']
    assert after['reserved_cost_units']==before['reserved_cost_units']
    assert after['checkpoints']==before['checkpoints']


def test_incomplete_review_for_old_reference_is_not_repaired_as_current(trial, isolated_brain, monkeypatch):
    store, job, context, _, _, _ = conditioned_executor(trial, monkeypatch, autonomy=True)
    style = context.artifacts['project_profile']['visual_style']
    source = context.artifact_dir/'unconditioned.png'
    Image.new('RGB', (768,1344), '#123344').save(source)
    isolated_brain.append(AssetRecord(id='old-incomplete-review', type='background', channel='wealth_blueprint',
        production_role='background', source='comfyui', quality_status='REJECTED', file_path=str(source),
        tags=style_asset_tags(style, job, 's01')))
    monkeypatch.setattr('atnos.production.wb_review_evidence_repair.candidate_evidence', lambda *_: {'incomplete':True})
    repair = AsyncMock(side_effect=AssertionError('Review repair used an incompatible old candidate'))
    monkeypatch.setattr('atnos.production.wb_review_evidence_repair.repair_incomplete_review', repair)
    with store.lease(job) as token:
        executor = prepared_executor(store, job, token)
        async def stop_at_callback(stage, inputs, callback, **kwargs):
            return await callback()
        executor.run = AsyncMock(side_effect=stop_at_callback)
        with pytest.raises(StopBeforeDispatch):
            asyncio.run(lane.produce_image_candidates(executor))
    repair.assert_not_awaited()
