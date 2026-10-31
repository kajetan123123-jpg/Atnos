"""Isolated admission/transport fixtures, never media quality receipts or LIVE."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from atnos.production import wb_image_api as lane
from atnos.production.local_image_dispatch_budget import (
    LocalImageDispatchBlocked, LocalImageDispatchScope, active_local_image_dispatch_scope,
    creative_workflow_digest, local_image_action_summary,
    local_image_action_status, local_image_dispatch_scope, record_local_image_dispatch,
    register_local_image_action, reserve_local_image_dispatch, workflow_digest,
)
from atnos.production.order_store import OrderConflict
from atnos.production.order_execution import StageExecutor
from atnos.production.comfy_channels.service import ChannelComfyGenerationService, ScopedLocalImageGenerationService
from atnos.production.steps.common import write_json
from atnos.services.comfyui_image_generation_service import (
    ComfyUiImageGenerationService, ComfyUiImageGenerationError,
    ComfyUiRuntimeUnavailableError, ComfyUiWorkflowRejectedError,
)
from test_wb_image_api_production import trial, isolated_brain, prepared_executor
from test_production_order_store import store
from test_scoped_script_revision import four_thoughts


ACTION = "wb-stabilization-20261012-bounded-images"
GRAPH = {"1": {"class_type": "SaveImage", "inputs": {"images": ["image", 0]}}}


@pytest.fixture
def bounded(trial):
    store, job, reference = trial
    lane.register_local_image_lane(store.settings, job, reference_path=reference, authorized=True)
    lane.register_local_image_dispatch_action(store.settings, job, action_key=ACTION, authorized=True,
                                               reason="Isolated authorized local test action")
    return store, job


def start_scope(store, job, token, index=0):
    stage = f"assets/image_local/s01/candidate-{index}"
    store.begin(job, token, stage, {"candidate": index}, local_model_call=True)
    action = local_image_action_status(store.settings, job)
    return LocalImageDispatchScope(store.settings, job, token, stage, f"candidate-fingerprint-{index}", action["digest"])


def finish_fixture(scope, tmp_path, index=0, graph=GRAPH):
    """A mocked transport result; no model/image generation or quality approval."""
    prompt = f"fixture-prompt-{index}"
    record_local_image_dispatch(scope, status="submitted", prompt_id=prompt)
    output = tmp_path / f"transport-output-{index}.bin"
    output.write_bytes(b"isolated fixture output, not an image candidate")
    receipt = tmp_path / f"transport-evidence-{index}.json"
    receipt.write_text(json.dumps({"status": "verified", "prompt_id": prompt,
                                  "artifact_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                                  "workflow_sha256": workflow_digest(graph), "workflow": graph}), encoding="utf-8")
    result = SimpleNamespace(prompt_id=prompt, output_path=output, evidence_path=receipt)
    return record_local_image_dispatch(scope, status="completed", result=result)


def test_registration_stopped_authorized_local_immutable_and_no_counter_reset(trial):
    store, job, reference = trial
    before = store.get(job)
    with pytest.raises(ValueError, match="Explicit"):
        register_local_image_action(store.settings, job, action_key=ACTION)
    with pytest.raises(OrderConflict, match="existing zero-cost local"):
        register_local_image_action(store.settings, job, action_key=ACTION, authorized=True)
    lane.register_image_lane(store.settings, job, reference_path=reference, authorized=True)
    with pytest.raises(OrderConflict, match="zero-cost local"):
        register_local_image_action(store.settings, job, action_key=ACTION, authorized=True)
    lane.register_local_image_lane(store.settings, job, authorized=True)
    registered = register_local_image_action(store.settings, job, action_key=ACTION, authorized=True)
    assert registered["max_new_requests"] == registered["remaining_new_requests"] == 3
    assert registered["used_new_requests"] == 0
    assert registered["user_appearance_approval"] is False
    assert register_local_image_action(store.settings, job, action_key=ACTION, authorized=True) == registered
    with pytest.raises(OrderConflict, match="immutable"):
        register_local_image_action(store.settings, job, action_key=ACTION + "-new", authorized=True)
    with store.lease(job), pytest.raises(OrderConflict, match="stopped"):
        register_local_image_action(store.settings, job, action_key=ACTION, authorized=True)
    after = store.get(job)
    assert (before["provider_calls"], before["reserved_cost_units"], before["profile_hash"]) == (
        after["provider_calls"], after["reserved_cost_units"], after["profile_hash"])
    assert before["request"] == after["request"]


def test_exactly_three_post_admissions_persist_across_fresh_worker_scopes(bounded, tmp_path):
    store, job = bounded
    store.settings = store.settings.model_copy(update={"atnos_local_work_unlimited": True})
    for index in range(3):
        with store.lease(job) as token:
            scope = start_scope(store, job, token, index)
            graph = {**GRAPH, '2': {'class_type': 'CLIPTextEncode', 'inputs': {'text': f'Concrete scene revision {index}'}}}
            state = reserve_local_image_dispatch(scope, graph)
            assert state["used_new_requests"] == index + 1
            assert state["remaining_new_requests"] == 2 - index
            finish_fixture(scope, tmp_path, index, graph)
            store.block(job, token, scope.stage, category="isolated_hold", message="resume test")
    with store.lease(job) as token:
        fourth = start_scope(store, job, token, 3)
        with pytest.raises(LocalImageDispatchBlocked, match="exhausted"):
            reserve_local_image_dispatch(fourth, GRAPH)
    final = local_image_action_status(store.settings, job)
    assert (final["used_new_requests"], final["remaining_new_requests"]) == (3, 0)
    assert len(final["dispatches"]) == 3
    assert store.settings.atnos_local_work_unlimited is True
    receipt = store.context(job).artifact_dir / "local_image_dispatch_status.json"
    assert json.loads(receipt.read_text("utf-8"))["remaining_new_requests"] == 0


def test_seed_only_retry_is_not_a_revision_and_does_not_consume_budget(bounded, tmp_path):
    store, job = bounded
    graph = {'1': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'one car inside a garage'}},
             '2': {'class_type': 'KSampler', 'inputs': {'seed': 123, 'steps': 24}},
             '3': {'class_type': 'SaveImage', 'inputs': {'images': ['2', 0], 'filename_prefix': 'first'}}}
    with store.lease(job) as token:
        first = start_scope(store, job, token)
        reserve_local_image_dispatch(first, graph)
        finish_fixture(first, tmp_path, graph=graph)
        store.block(job, token, first.stage, category='isolated_hold', message='resume test')
    with store.lease(job) as token:
        retry = json.loads(json.dumps(graph))
        retry['2']['inputs']['seed'] = 456
        retry['3']['inputs']['filename_prefix'] = 'second'
        second = start_scope(store, job, token, 1)
        with pytest.raises(LocalImageDispatchBlocked, match='changing only seed') as failure:
            reserve_local_image_dispatch(second, retry)
        assert failure.value.category == 'local_image_retry_needs_revision'
        assert local_image_action_status(store.settings, job)['used_new_requests'] == 1
        retry['1']['inputs']['text'] = 'one mint car fully inside an enclosed cream garage, no street'
        assert reserve_local_image_dispatch(second, retry)['used_new_requests'] == 2


def test_live_seed_only_workflows_have_identical_creative_identity():
    evidence = json.loads((Path(__file__).parent/'fixtures/wb_comfy_seed_only_retries_20261012.json').read_text('utf-8'))
    graphs = evidence['workflows']
    assert len(graphs) == 3
    assert len({workflow_digest(graph) for graph in graphs}) == 3
    assert len({creative_workflow_digest(graph) for graph in graphs}) == 1


def test_ui_projection_is_read_only_and_does_not_expose_paths_or_approval(bounded):
    store, job = bounded
    before = store.get(job)
    summary = local_image_action_summary(store.settings, job)
    assert summary == {'status':'verified', 'provider':'comfyui-local', 'action_key':ACTION,
                       'used_new_requests':0, 'remaining_new_requests':3, 'max_new_requests':3,
                       'pending_or_unknown':False}
    after = store.get(job)
    assert before == after
    path = store.context(job).artifact_dir/'local_image_dispatch_authorization.json'
    path.write_text('{}', encoding='utf-8')
    assert local_image_action_summary(store.settings, job) == {'status':'integrity_blocked', 'provider':'comfyui-local'}


def test_existing_order_endpoint_returns_real_action_summary_without_dispatch(bounded, monkeypatch):
    from atnos.api.routes.production import get_production_order
    store, job = bounded
    monkeypatch.setattr('atnos.production.order_dispatch.ProductionOrderDispatcher.dispatch',
                        lambda *_: pytest.fail('Reading an image budget cannot dispatch a worker'))
    before = store.get(job)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=store.settings)))
    result = get_production_order(request, job)
    assert result['local_image_action']['remaining_new_requests'] == 3
    assert result['local_image_action']['status'] == 'verified'
    assert store.get(job) == before


def test_pending_and_unknown_fail_closed_across_resume_without_freeing_budget(bounded):
    store, job = bounded
    with store.lease(job) as token:
        first = start_scope(store, job, token)
        reserve_local_image_dispatch(first, GRAPH)
        with pytest.raises(LocalImageDispatchBlocked, match="outcome unknown"):
            reserve_local_image_dispatch(start_scope(store, job, token, 1), GRAPH)
        record_local_image_dispatch(first, status="unknown", error_kind="FixtureConnectionLoss")
        store.block(job, token, first.stage, category="execution_outcome_unknown", message="isolated uncertain POST")
    with store.lease(job) as token:
        next_scope = start_scope(store, job, token, 2)
        with pytest.raises(LocalImageDispatchBlocked, match="reconciliation"):
            reserve_local_image_dispatch(next_scope, GRAPH)
    assert local_image_action_status(store.settings, job)["used_new_requests"] == 1


def test_definite_workflow_rejection_is_not_misreported_as_unknown_or_retried(bounded):
    store, job = bounded
    with store.lease(job) as token:
        scope = start_scope(store, job, token)
        reserve_local_image_dispatch(scope, GRAPH)
        record_local_image_dispatch(
            scope,
            status="rejected",
            error_kind=ComfyUiWorkflowRejectedError.__name__,
            error_detail="ComfyUI rejected an invalid node graph",
        )
        state = local_image_action_status(store.settings, job)
        assert state["used_new_requests"] == 1
        assert state["remaining_new_requests"] == 2
        assert state["pending_or_unknown"] is False
        assert state["dispatches"][0]["status"] == "rejected"
        assert state["dispatches"][0]["evidence"]["error_detail"] == "ComfyUI rejected an invalid node graph"
        with pytest.raises(LocalImageDispatchBlocked, match="same creative workflow"):
            reserve_local_image_dispatch(start_scope(store, job, token, 1), GRAPH)


def test_wrong_lease_and_missing_stage_cannot_consume(bounded):
    store, job = bounded
    with store.lease(job) as token:
        scope = start_scope(store, job, token)
        foreign = LocalImageDispatchScope(scope.settings, job, "foreign-owner", scope.stage, scope.fingerprint, scope.action_digest)
        with pytest.raises(OrderConflict, match="lease"):
            reserve_local_image_dispatch(foreign, GRAPH)
        missing = LocalImageDispatchScope(scope.settings, job, token, "assets/image_local/missing", "missing-fp", scope.action_digest)
        with pytest.raises(LocalImageDispatchBlocked, match="checkpoint"):
            reserve_local_image_dispatch(missing, GRAPH)
    assert local_image_action_status(store.settings, job)["used_new_requests"] == 0


def test_atomic_same_candidate_race_admits_once(bounded):
    store, job = bounded
    with store.lease(job) as token:
        scope = start_scope(store, job, token)
        def attempt():
            try:
                reserve_local_image_dispatch(scope, GRAPH)
                return "admitted"
            except LocalImageDispatchBlocked:
                return "blocked"
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(lambda _: attempt(), range(2))) == ["admitted", "blocked"]
    assert local_image_action_status(store.settings, job)["used_new_requests"] == 1


def test_completed_candidate_cannot_post_again_and_invalid_completion_stays_pending(bounded, tmp_path):
    store, job = bounded
    with store.lease(job) as token:
        first = start_scope(store, job, token)
        reserve_local_image_dispatch(first, GRAPH)
        with pytest.raises(LocalImageDispatchBlocked, match="matching generation"):
            record_local_image_dispatch(first, status="completed", result=SimpleNamespace(prompt_id="wrong"))
        assert local_image_action_status(store.settings, job)["pending_or_unknown"]
        finish_fixture(first, tmp_path)
        with pytest.raises(LocalImageDispatchBlocked, match="already dispatched"):
            reserve_local_image_dispatch(first, GRAPH)
    assert local_image_action_status(store.settings, job)["used_new_requests"] == 1


def test_authorization_receipt_tamper_blocks_before_post(bounded):
    store, job = bounded
    path = store.context(job).artifact_dir / "local_image_dispatch_authorization.json"
    payload = json.loads(path.read_text("utf-8"))
    payload["max_new_requests"] = 300
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LocalImageDispatchBlocked, match="integrity"):
        local_image_action_status(store.settings, job)


def test_context_scope_reaches_normal_channel_service_via_to_thread_and_unscoped_stays_original(bounded):
    store, job = bounded
    assert type(ChannelComfyGenerationService(store.settings).images) is ComfyUiImageGenerationService
    with store.lease(job) as token:
        scope = start_scope(store, job, token)
        async def inspect():
            with local_image_dispatch_scope(store.settings, job, token, scope.stage, scope.fingerprint):
                return await asyncio.to_thread(lambda: type(ChannelComfyGenerationService(store.settings).images))
        assert asyncio.run(inspect()) is ScopedLocalImageGenerationService
        assert active_local_image_dispatch_scope() is None
    assert local_image_action_status(store.settings, job)["used_new_requests"] == 0


def test_post_transport_failure_blocks_internal_retry_at_exact_queue_boundary(bounded, monkeypatch):
    store, job = bounded
    posts = []
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kwargs):
            posts.append(url)
            raise TimeoutError("isolated queue transport failure")
    monkeypatch.setattr("atnos.services.comfyui_image_generation_service.httpx.Client", Client)
    with store.lease(job) as token:
        scope = start_scope(store, job, token)
        with local_image_dispatch_scope(store.settings, job, token, scope.stage, scope.fingerprint):
            service = ScopedLocalImageGenerationService(store.settings)
            with pytest.raises(LocalImageDispatchBlocked, match="outcome unknown"):
                service._queue_prompt(GRAPH)
            with pytest.raises(LocalImageDispatchBlocked, match="One POST"):
                service._queue_prompt(GRAPH)
    assert len(posts) == 1
    final = local_image_action_status(store.settings, job)
    assert final["used_new_requests"] == 1 and final["dispatches"][0]["status"] == "unknown"


def test_definite_comfy_workflow_rejection_is_audited_separately(bounded, monkeypatch):
    store, job = bounded
    posts = []

    class Response:
        status_code = 400
        text = "invalid node graph"

    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kwargs):
            posts.append(url)
            return Response()

    monkeypatch.setattr("atnos.services.comfyui_image_generation_service.httpx.Client", Client)
    with store.lease(job) as token:
        scope = start_scope(store, job, token)
        with local_image_dispatch_scope(store.settings, job, token, scope.stage, scope.fingerprint):
            service = ScopedLocalImageGenerationService(store.settings)
            with pytest.raises(LocalImageDispatchBlocked, match="rejected") as failure:
                service._queue_prompt(GRAPH)
            assert failure.value.category == "workflow_rejected"
            with pytest.raises(LocalImageDispatchBlocked, match="One POST"):
                service._queue_prompt(GRAPH)
    assert len(posts) == 1
    final = local_image_action_status(store.settings, job)
    assert final["used_new_requests"] == 1
    assert final["pending_or_unknown"] is False
    assert final["dispatches"][0]["status"] == "rejected"
    assert final["dispatches"][0]["evidence"]["error_kind"] == "ComfyUiWorkflowRejectedError"


def test_posted_backend_failure_cannot_retry_or_restart_normal_comfy(bounded, tmp_path, monkeypatch):
    store, job = bounded
    posts = []
    monkeypatch.setattr("atnos.services.comfyui_image_generation_service.HardwareGuardService.require_gpu", lambda *_: None)
    monkeypatch.setattr("atnos.services.production_gpu_residency_service.ProductionGpuResidencyService.prepare_for_comfy", lambda *_: None)
    monkeypatch.setattr(ComfyUiImageGenerationService, "_runtime_incompatible_backends", lambda *_: set())
    monkeypatch.setattr(ComfyUiImageGenerationService, "_persistently_blocked_backends", lambda *_: set())
    monkeypatch.setattr(ComfyUiImageGenerationService, "_build_workflow", lambda *a, **k: GRAPH)
    def queue(*args): posts.append("POST"); return "fixture-provider-id"
    monkeypatch.setattr(ComfyUiImageGenerationService, "_queue_prompt", queue)
    def wait(*a, **k): raise ComfyUiImageGenerationError("fixture: backend VRAM failure after POST")
    monkeypatch.setattr(ComfyUiImageGenerationService, "_wait_for_image", wait)
    with store.lease(job) as token:
        scope = start_scope(store, job, token)
        with local_image_dispatch_scope(store.settings, job, token, scope.stage, scope.fingerprint):
            service = ScopedLocalImageGenerationService(store.settings)
            monkeypatch.setattr(service.runtime, "ensure_server_running", lambda **_: True)
            monkeypatch.setattr(service.runtime, "restart_server", lambda **_: pytest.fail("Posted failure restarted the runtime"))
            with pytest.raises(LocalImageDispatchBlocked, match="outcome unknown"):
                service.generate_background(prompt="fixture", output_path=tmp_path / "not-rendered.png",
                                            width=512, height=512, seed=1, preferred_checkpoint="fixture.safetensors")
    assert posts == ["POST"]
    assert local_image_action_status(store.settings, job)["used_new_requests"] == 1


def test_runtime_startup_failure_before_post_consumes_zero(bounded, monkeypatch):
    store, job = bounded
    def unavailable(*a, **k): raise ComfyUiRuntimeUnavailableError("isolated startup failed before POST")
    monkeypatch.setattr(ComfyUiImageGenerationService, "generate_background", unavailable)
    with store.lease(job) as token:
        scope = start_scope(store, job, token)
        with local_image_dispatch_scope(store.settings, job, token, scope.stage, scope.fingerprint):
            service = ScopedLocalImageGenerationService(store.settings)
            with pytest.raises(ComfyUiRuntimeUnavailableError, match="before POST"):
                service.generate_background(prompt="fixture")
    assert local_image_action_status(store.settings, job)["used_new_requests"] == 0


def test_normal_stage_cache_reuse_does_not_consume_another_post(bounded, tmp_path):
    store, job = bounded
    with store.lease(job) as token:
        scope = start_scope(store, job, token)
        reserve_local_image_dispatch(scope, GRAPH)
        finish_fixture(scope, tmp_path)
        artifact = write_json(store.context(job).artifact_dir / "candidate-cache.json",
                              {"asset_id": "isolated-pending", "user_approved": False})
        store.complete(job, token, scope.stage, artifact.payload, [artifact.artifact_path])
        store.block(job, token, scope.stage, category="isolated_hold", message="cache test")
    with store.lease(job) as token:
        async def forbidden(): pytest.fail("Cache reuse generated another image")
        result = asyncio.run(StageExecutor(store, job, token).run(scope.stage, {"candidate": 0}, forbidden,
                                                                  local_model_call=True))
    assert result["asset_id"] == "isolated-pending"
    assert local_image_action_status(store.settings, job)["used_new_requests"] == 1


def test_remote_endpoint_is_blocked_before_admission_and_post(bounded, monkeypatch):
    store, job = bounded
    monkeypatch.setattr(ComfyUiImageGenerationService, "_queue_prompt", lambda *_: pytest.fail("Remote image POST"))
    with store.lease(job) as token:
        scope = start_scope(store, job, token)
        with local_image_dispatch_scope(store.settings, job, token, scope.stage, scope.fingerprint):
            service = ScopedLocalImageGenerationService(store.settings)
            monkeypatch.setattr(type(service.runtime), "base_url", property(lambda _: "https://paid.example"))
            with pytest.raises(LocalImageDispatchBlocked, match="remote or paid"):
                service._queue_prompt(GRAPH)
    assert local_image_action_status(store.settings, job)["used_new_requests"] == 0


def test_normal_fallback_binds_abandoned_flux_reconciliation_and_local_action(bounded, isolated_brain, monkeypatch):
    from atnos.production.asset_brain.models import AssetRecord
    from atnos.production.channels.wealth_blueprint.remotion_order_edit import SceneAssetsNotReady
    from atnos.production.order_store import file_hash

    store, job = bounded
    old = {"digest": "old-flux-digest-1234567890", "source": "User explicit historical trial"}
    old_stage = f"assets/image_local/flux2-trial/s01/{old['digest'][:16]}"
    with store.lease(job) as token:
        store.begin(job, token, old_stage, {"historical_trial": True}, local_model_call=True)
        store.fail(job, token, old_stage, category="local_operation_abandoned", message="isolated reconciled historical operation")
    old_checkpoint = next(row for row in store.get(job)["checkpoints"] if row["stage"] == old_stage)
    reconciliation = write_json(store.context(job).artifact_dir / "reconciliation-fixture.json",
                                {"outcome": "abandoned_without_product", "fixture": True}).artifact_path
    fallback = {"outcome": "abandoned_without_product", "evidence_path": str(reconciliation), "digest": "isolated-reconciliation-digest"}
    monkeypatch.setattr("atnos.production.wb_local_flux_trial.load_flux_trial",
                        lambda *args, **kwargs: old if args[2] == "s01" else None)
    monkeypatch.setattr("atnos.production.wb_flux_refinement.load_flux_refinement", lambda *args: None)
    monkeypatch.setattr("atnos.production.wb_local_flux_trial.verify_failed_flux_allows_local_comfy_fallback", lambda *args: fallback)
    monkeypatch.setattr("atnos.production.wb_local_flux_trial.generate_flux_candidate", lambda *_: pytest.fail("No new Flux POST"))
    monkeypatch.setattr(lane, "_generate_candidate", lambda *_: pytest.fail("No paid image call"))
    calls = []
    def generate(settings, context, need, style, tags, lane_data, fingerprint, previous_reference):
        scope = active_local_image_dispatch_scope()
        assert scope is not None and scope.fingerprint == fingerprint
        reserve_local_image_dispatch(scope, GRAPH)
        finish_fixture(scope, context.artifact_dir)
        output = context.artifact_dir / "transport-output-0.bin"
        asset = AssetRecord(id="isolated-local-action-candidate", type="background", channel="wealth_blueprint",
                            file_path=str(output), quality_status="AWAITING_REVIEW", production_role="background",
                            source="comfyui-test", tags=tags)
        isolated_brain.append(asset)
        calls.append("normal local candidate")
        return write_json(context.artifact_dir / "candidate-fallback-fixture.json",
                          {"asset_id": asset.id, "output_path": str(output), "quality_status": asset.quality_status,
                           "user_approved": False})
    monkeypatch.setattr(lane, "_generate_local_candidate", generate)
    with store.lease(job) as token, pytest.raises(SceneAssetsNotReady):
        asyncio.run(lane.produce_image_candidates(prepared_executor(store, job, token)))
    new_checkpoint = next(row for row in store.get(job)["checkpoints"]
                          if row["stage"].startswith("assets/image_local/") and row["stage"] != old_stage)
    proof = new_checkpoint["output"]["replaces_local_operation"]
    assert proof == {"stage": old_stage, "input_hash": old_checkpoint["input_hash"],
                     "attempts": old_checkpoint["attempts"], "reconciliation_sha256": file_hash(reconciliation)}
    assert new_checkpoint["status"] == "done" and new_checkpoint["output"]["user_approved"] is False
    assert any(row["path"] == reconciliation.name for row in new_checkpoint["files"])
    assert calls == ["normal local candidate"]
    assert local_image_action_status(store.settings, job)["used_new_requests"] == 1


def test_rejected_candidate_with_exhausted_action_stops_before_second_stage(trial, isolated_brain, monkeypatch):
    from atnos.production.asset_brain.models import AssetRecord
    from atnos.production.channels.wealth_blueprint.remotion_order_edit import SceneAssetsNotReady
    from atnos.production.order_store import ProductionBlocked

    store, job, reference = trial
    lane.register_local_image_lane(store.settings, job, reference_path=reference, authorized=True)
    lane.register_local_image_dispatch_action(
        store.settings, job, action_key='wb-one-image-only-2026-10-27', max_new_requests=1,
        authorized=True, reason='Isolated regression for stopping after a rejected bounded candidate')
    calls = []

    def generate(settings, context, need, style, tags, lane_data, fingerprint, previous_reference):
        scope = active_local_image_dispatch_scope()
        assert scope is not None and scope.fingerprint == fingerprint
        reserve_local_image_dispatch(scope, GRAPH)
        finish_fixture(scope, context.artifact_dir)
        output = context.artifact_dir / 'single-rejected-candidate.bin'
        asset = AssetRecord(id='single-rejected-bounded-candidate', type='background', channel='wealth_blueprint',
                            file_path=str(output), quality_status='AWAITING_REVIEW', production_role='background',
                            source='comfyui-test', tags=tags)
        isolated_brain.append(asset)
        calls.append(asset.id)
        return write_json(context.artifact_dir / 'single-rejected-candidate.json',
                          {'asset_id': asset.id, 'output_path': str(output), 'quality_status': 'AWAITING_REVIEW',
                           'user_approved': False})

    monkeypatch.setattr(lane, '_generate_local_candidate', generate)
    with store.lease(job) as token, pytest.raises(SceneAssetsNotReady):
        asyncio.run(lane.produce_image_candidates(prepared_executor(store, job, token)))

    next(asset for asset in isolated_brain if asset.id == 'single-rejected-bounded-candidate').quality_status = 'REJECTED'
    with store.lease(job) as token, pytest.raises(ProductionBlocked, match='no additional image stage'):
        asyncio.run(lane.produce_image_candidates(prepared_executor(store, job, token)))

    image_stages = [row for row in store.get(job)['checkpoints'] if row['stage'].startswith('assets/image_local/')]
    action = local_image_action_status(store.settings, job)
    assert len(image_stages) == 1
    assert image_stages[0]['status'] == 'done'
    assert calls == ['single-rejected-bounded-candidate']
    assert (action['used_new_requests'], action['remaining_new_requests']) == (1, 0)
