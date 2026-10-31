from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from atnos.production import editorial_film
from atnos.production import order_comfy_routing
from atnos.production.chart_series import verified_chart_series
from atnos.production.order_execution import StageExecutor
from atnos.production.steps import order_assets
from atnos.production.steps.order_assets import EditorialScene, ScenePlan, cosine, library_manifest, rank_library, validate_plan
from atnos.production.steps.common import write_json
from test_production_order_store import store, create
from test_production_order_script import script


@pytest.fixture
def setup(store, monkeypatch):
    job = create(store)["id"]
    context = store.context(job)
    directory = store.settings.data_dir / "test-assets"
    directory.mkdir()
    manifest = directory / "sources.json"
    manifest.write_text(json.dumps({"license": "Test fixture, not licensed production footage", "sources": [
        {"id": "desk", "description": "A person reviewing household bills with a notebook", "page": "https://example.com/desk", "credit": "Fixture"},
        {"id": "clock", "description": "A wall clock with moving hands", "page": "https://example.com/clock", "credit": "Fixture"}]}))
    (directory / "desk.mp4").write_bytes(b"test video bytes - mock probe required")
    (directory / "clock.mp4").write_bytes(b"second test video bytes - mock probe required")
    context.artifacts["project_profile"]["production"]["assets"]["manifests"] = ["test-assets/sources.json"]
    monkeypatch.setattr(order_assets, "probe_media", lambda path: {"available": path.is_file(), "video_codec": "h264",
        "duration_s": 20, "width": 1920, "height": 1080})
    return store, job, context


def plan_for(script):
    return ScenePlan(scenes=[EditorialScene(segment_id=s.id, kind="steps", semantic_goal=s.visual_intent,
        labels=["Review essential bills", "Choose a manageable transfer"], match_reason="Demonstrates the spoken action with a concrete diagram") for s in script.segments])


def test_library_has_checksums_and_does_not_invent_visual_review(setup):
    store, _, context = setup
    library = library_manifest(store.settings, context.artifacts["project_profile"])
    assert len(library["assets"]) == 2
    assert all(len(a["sha256"]) == 64 for a in library["assets"])
    assert all(a["visual_review"] == "not_performed" for a in library["assets"])


@pytest.mark.parametrize("reason", [
    "The video shows hands interacting with a jar, which is not relevant to the car repair. No suitable footage exists; a native graphic should be used.",
    "This asset does not match the required car scene.",
    "No suitable clip exists in the library for this scene.",
])
def test_selected_footage_cannot_contradict_its_own_assessment(setup, script, reason):
    store, _, context = setup
    library = library_manifest(store.settings, context.artifacts["project_profile"])
    plan = plan_for(script)
    plan.scenes[0].asset_id = "desk"
    plan.scenes[0].match_reason = reason
    with pytest.raises(ValueError, match="contradicts its suitability"):
        validate_plan(plan, script, library)


def test_positive_asset_assessment_still_validates(setup, script):
    store, _, context = setup
    library = library_manifest(store.settings, context.artifacts["project_profile"])
    plan = plan_for(script)
    plan.scenes[0].asset_id = "desk"
    plan.scenes[0].match_reason = "The notebook illustrates the review of household bills."
    validate_plan(plan, script, library)


def test_comfy_routing_snapshot_is_part_of_stage_inputs(setup, monkeypatch):
    store, _, context = setup
    context.artifacts["production_order"] = {"project": "wealth_blueprint"}
    context.artifacts["comfy_asset_requests"] = None
    expected_snapshot = {
        "channel_id": "wealth_blueprint",
        "effective_model_profile": {
            "quality_model": "checkpoints/dreamshaper_sdxl_alpha2_wb_candidate.safetensors",
            "img2img_preset": "WB_IMG2IMG_V1",
            "reference_denoise": 0.4,
        },
    }
    monkeypatch.setattr(order_comfy_routing, "routing_snapshot_for_order", lambda *_: expected_snapshot)

    class StopAfterRouting(Exception):
        pass

    class RoutingOnlyExecutor:
        def __init__(self):
            self.store = SimpleNamespace(settings=store.settings)
            self.context = context
            self.inputs = None

        async def run(self, stage, inputs, operation, **kwargs):
            assert stage == "comfy_channel_routing"
            self.inputs = inputs
            await operation()
            raise StopAfterRouting

    executor = RoutingOnlyExecutor()
    with pytest.raises(StopAfterRouting):
        asyncio.run(order_assets.produce_assets(executor))

    assert executor.inputs["routing_snapshot"] == expected_snapshot


def test_changed_comfy_routing_snapshot_retries_without_resetting_attempts(setup):
    store, job, context = setup
    from atnos.production.order_comfy_routing import routing_snapshot_for_order

    base_snapshot = routing_snapshot_for_order(store.settings, "wealth_blueprint")
    old_inputs = {"project": "wealth_blueprint", "requests": None,
                  "routing_snapshot": {**base_snapshot, "effective_model_profile": {"quality_model": "old"}}}
    new_inputs = {"project": "wealth_blueprint", "requests": None,
                  "routing_snapshot": {**base_snapshot, "effective_model_profile": {
                      "quality_model": "checkpoints/dreamshaper_sdxl_alpha2_wb_candidate.safetensors"}}}

    with store.lease(job) as token:
        assert store.begin(job, token, "comfy_channel_routing", old_inputs) is None
        artifact = write_json(context.artifact_dir / "comfy_channel_routing.json",
                              {**base_snapshot, "fulfillment": {"results": [], "metrics": {}}})
        store.complete(job, token, "comfy_channel_routing", artifact.payload, [artifact.artifact_path])
        attempts_before = next(row["attempts"] for row in store.get(job)["checkpoints"]
                               if row["stage"] == "comfy_channel_routing")

        assert store.begin(job, token, "comfy_channel_routing", new_inputs) is None
        attempts_after = next(row["attempts"] for row in store.get(job)["checkpoints"]
                              if row["stage"] == "comfy_channel_routing")

    assert attempts_after == attempts_before + 1


def test_missing_sources_and_path_traversal_fail(setup):
    store, _, context = setup
    (store.settings.data_dir / "test-assets/desk.mp4").unlink()
    library = library_manifest(store.settings, context.artifacts["project_profile"])
    assert library["rejected"] == [{"id": "desk", "reason": "missing_or_invalid_video"}]
    context.artifacts["project_profile"]["production"]["assets"]["manifests"] = ["../../escape.json"]
    with pytest.raises(ValueError, match="escapes"):
        library_manifest(store.settings, context.artifacts["project_profile"])


@pytest.mark.parametrize("left,right", [([], []), ([0, 0], [0, 0]), ([1], [1, 2]), ([float('nan')], [1]), ([float('inf')], [1])])
def test_invalid_embedding_is_not_keyword_fallback(left, right):
    with pytest.raises(ValueError):
        cosine(left, right)


def test_semantic_ranking_uses_embedding_vectors_not_words(setup, script, monkeypatch):
    store, _, context = setup
    library = library_manifest(store.settings, context.artifacts["project_profile"])
    for segment in script.segments:
        segment.text = "Prepare for the expenses that arrive after payday."
    calls = []
    def embed(texts, model):
        calls.append((texts, model))
        return [[1, 0], [0, 1], *[[1, 0] for _ in script.segments]]
    monkeypatch.setattr(order_assets, "OllamaEmbeddingClient", lambda *a, **k: SimpleNamespace(
        status=lambda model: {"model_metadata": {"resolved_name": model, "digest": "fixture"}}, embed=embed))
    ranking = rank_library(store.settings, context.artifacts["project_profile"], script, library)
    assert ranking["method"] == "dense_embedding_cosine"
    assert all(rows[0]["asset_id"] == "desk" for rows in ranking["candidates"].values())
    assert len(calls) == 1


def test_plan_requires_complete_coverage_and_relevant_real_assets(setup, script):
    store, _, context = setup
    library = library_manifest(store.settings, context.artifacts["project_profile"])
    plan = plan_for(script)
    validate_plan(plan, script, library)
    plan.scenes.reverse()
    with pytest.raises(ValueError, match="exactly once"):
        validate_plan(plan, script, library)
    plan = plan_for(script)
    plan.scenes[0].kind, plan.scenes[0].asset_id = "footage", "invented"
    with pytest.raises(ValueError, match="unknown asset"):
        validate_plan(plan, script, library)
    plan.scenes[0].asset_id, plan.scenes[0].source_start = "desk", 20
    with pytest.raises(ValueError, match="outside"):
        validate_plan(plan, script, library)


def test_repeated_footage_and_unrelated_claims_rejected(setup, script):
    store, _, context = setup
    library = library_manifest(store.settings, context.artifacts["project_profile"])
    plan = plan_for(script)
    for scene in plan.scenes[:3]:
        scene.kind, scene.asset_id = "footage", "desk"
    with pytest.raises(ValueError, match="repeated"):
        validate_plan(plan, script, library)
    plan = plan_for(script)
    plan.scenes[0].claim_ids = ["fake_claim"]
    with pytest.raises(ValueError, match="matching script"):
        validate_plan(plan, script, library)


def test_renderer_reuses_editorial_path_for_arbitrary_script(setup, script, tmp_path):
    plan = plan_for(script).model_dump(mode="json")
    brief = {"schema_version": "production-order-1", "claims": [], "scenes": plan["scenes"]}
    scene = {**plan["scenes"][0], "headline": "Give your next payday a purpose"}
    frame = editorial_film.draw_scene(brief, scene, .7, 0)
    assert frame.size == (1080, 1920)
    assert frame.getbbox() == (0, 0, 1080, 1920)
    paths = editorial_film.create_order_covers(tmp_path, headline=script.title, detail="Start with an amount that fits your budget.")
    assert all(path.stat().st_size > 1000 for path in paths)
    from PIL import Image
    assert [Image.open(p).size for p in paths] == [(1080, 1920), (1280, 720)]


def test_renderer_refuses_missing_footage_and_false_numbers():
    brief = {"schema_version": "production-order-1", "claims": [], "scenes": [{}]}
    scene = {"kind": "footage", "claim_ids": [], "headline": "A real action"}
    with pytest.raises(ValueError, match="no decoded image"):
        editorial_film.draw_scene(brief, scene, .5, 0)
    brief["claims"] = [{"id": "c1", "operation": "product", "operands": [2, 2], "result": 100,
        "unit": "dollars", "spoken_result": "one hundred", "assumptions": "A deliberately invalid test calculation"}]
    scene.update(kind="number", claim_ids=["c1"])
    with pytest.raises(ValueError, match="unverified numeric"):
        editorial_film.draw_scene(brief, scene, .5, 0)


def test_explicit_storyboard_edit_preserves_failures_budget_and_revalidates(setup, script, tmp_path):
    from atnos.production import scene_revision
    from atnos.production.order_store import ProductionBlocked
    store, job, context = setup
    library = library_manifest(store.settings, context.artifacts["project_profile"])
    ranking = {"method": "test_fixture", "candidates": {}}
    with store.lease(job) as token:
        for stage, output in [("script",script.model_dump(mode="json")), ("asset_library",library), ("asset_ranking",ranking)]:
            store.begin(job,token,stage,{"fixture":stage})
            evidence = write_json(context.artifact_dir / f"{stage}.json",output)
            store.complete(job,token,stage,output,[evidence.artifact_path])
        store.begin(job,token,"asset_planning",{"original": True},local_model_call=True)
        store.fail(job,token,"asset_planning",category="invalid_output",message="Invalid original storyboard")
    before = store.get(job)
    source = tmp_path / "edited.json"
    source.write_text(json.dumps({"job_id":job,"editor":"test fixture","reason":"Test explicit corrected input",
                                  "scenes":plan_for(script).model_dump()["scenes"]}))
    with pytest.raises(ValueError,match="explicit"):
        scene_revision.admit(store.settings,job,source)
    assert scene_revision.admit(store.settings,job,source,approved=True)["status"] == "accepted"
    after = store.get(job)
    assert after["provider_calls"] == before["provider_calls"]
    assert after["reserved_cost_units"] == before["reserved_cost_units"]
    path = context.artifact_dir / "editorial/storyboard-accepted.json"
    assert json.loads(path.read_text())["superseded"]["status"] == "failed"
    assert json.loads(path.read_text())["visual_qa"] == "not_started"
    with store.lease(job) as token:
        executor = StageExecutor(store,job,token)
        output = asyncio.run(scene_revision.produce_accepted(executor,script,library,ranking))
        assert output["visual_qa"] == "not_started"
        assert len(output["scenes"]) == len(script.segments)
        with pytest.raises(ProductionBlocked,match="inputs changed"):
            asyncio.run(scene_revision.produce_accepted(executor,script,{"assets": []},ranking))
        path.write_text("corrupt")
        with pytest.raises(ProductionBlocked,match="missing or changed"):
            asyncio.run(scene_revision.produce_accepted(executor,script,library,ranking))
    assert store.get(job)["status"] != "ready"


def test_recurring_savings_chart_matches_contributions_and_never_adds_returns(script):
    from atnos.production.steps.order_script import NumericClaim
    claim = NumericClaim(id="saving", operation="product", operands=[100, 100], result=10000,
        unit="dollars", spoken_result="ten thousand dollars", assumptions="One hundred weekly deposits without investment returns.")
    narration = "Save one hundred dollars a week for one hundred weeks to reach ten thousand dollars."
    series = verified_chart_series(claim.model_dump(), narration)
    assert series["values"][0] == 0
    assert series["values"][100] == 5000
    assert series["values"][-1] == 10000
    assert series["period_unit"] == "weeks"
    assert "%" not in series["label"]
    script.claims = [claim]
    script.segments[0].text = narration
    script.segments[0].claim_ids = ["saving"]
    plan = plan_for(script)
    plan.scenes[0].kind = "chart"
    plan.scenes[0].claim_ids = ["saving"]
    validate_plan(plan, script, {"assets": []})
    scene = {**plan.scenes[0].model_dump(), "headline": "Your savings plan", "narration": narration}
    brief = {"schema_version": "production-order-1", "claims": [claim.model_dump()], "scenes": [scene]}
    first = editorial_film.draw_scene(brief, scene, 0, 0)
    last = editorial_film.draw_scene(brief, scene, 1, 0)
    assert first.size == last.size == (1080, 1920)
    assert first.tobytes() != last.tobytes()
    with pytest.raises(ValueError, match="time unit"):
        verified_chart_series(claim.model_dump(), "Save one hundred dollars and watch your balance grow.")
    with pytest.raises(ValueError, match="verified monetary"):
        verified_chart_series({**claim.model_dump(), "result": 20000}, narration)


def test_asset_copy_and_corruption_repair_are_stage_local(setup, script, monkeypatch):
    from atnos.production import asset_observation
    from atnos.production import asset_observation_cache

    store, job, context = setup
    # This test exercises stage-local asset observation/copy/reuse. Claim-gate
    # behavior is covered independently; isolate it from this fixture's absent
    # ledger so the stage admission under test remains reachable.
    monkeypatch.setattr('atnos.integrations.claim_evidence_gate.ensure_media_claim_gate',
                        lambda **_: {'blocked': False})
    model_calls = []
    observation_calls = []

    def contact_sheet(source, destination, *, frame_count):
        # The library deliberately contains fake video bytes. Keep observation's
        # real checkpoint/cache behavior while replacing decoding and inference.
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fixture contact sheet")
        return destination

    def describe(settings, sheet, config, *, retry_feedback=""):
        observation_calls.append(sheet.name)
        return {"status": "observed", "observation": {
            "summary": "A person examines household bills beside a notebook.",
            "visible_objects": ["notebook", "bills"], "visible_actions": ["examining bills"],
            "readable_text": [], "limitations": "Synthetic test observation; not a production quality verdict.",
        }}

    monkeypatch.setattr(asset_observation.visual_semantic_audit, "extract_storyboard_contact_sheet", contact_sheet)
    monkeypatch.setattr(asset_observation, "describe_frames", describe)
    monkeypatch.setattr(asset_observation_cache, "model_revision", lambda *_: None)
    async def planner(*args, **kwargs):
        model_calls.append(1)
        plan = plan_for(script)
        for i, identity in [(0, "desk"), (1, "clock")]:
            plan.scenes[i].kind, plan.scenes[i].asset_id = "footage", identity
            plan.scenes[i].labels = []
        return {'scenes': {scene.segment_id: scene.model_dump(mode='json') for scene in plan.scenes}}
    monkeypatch.setattr(order_assets, "ask_model", planner)
    monkeypatch.setattr(order_assets, "rank_library", lambda *args: {"method": "dense_embedding_cosine", "candidates": {}})
    async def run(token):
        executor = StageExecutor(store, job, token)
        executor.context = context
        executor.context.artifacts["script"] = script.model_dump(mode="json")
        return await order_assets.produce_assets(executor)
    with store.lease(job) as token:
        first = asyncio.run(run(token))
    Path(first["assets"]["desk"]["staged_path"]).write_bytes(b"corrupted asset")
    with store.lease(job) as token:
        second = asyncio.run(run(token))
    assert len(model_calls) == 1
    assert observation_calls == ["desk.png", "clock.png"]
    attempts = {s["stage"]: s["attempts"] for s in store.get(job)["checkpoints"]}
    assert attempts["asset_observation/desk"] == attempts["asset_observation/clock"] == 1
    assert attempts["asset/desk"] == 2
    assert attempts["asset/clock"] == 1
    assert second["assets"]["desk"]["sha256"] == order_assets.file_hash(Path(second["assets"]["desk"]["staged_path"]))


def test_timing_repair_replaces_only_short_scene_and_reuses_repair_after_restart(setup, script, monkeypatch):
    store, job, context = setup
    # Keep this test focused on timing repair and its durable checkpoint reuse;
    # the shared fixture intentionally has no claim ledger.
    monkeypatch.setattr('atnos.integrations.claim_evidence_gate.ensure_media_claim_gate',
                        lambda **_: {'blocked': False})
    library = library_manifest(store.settings, context.artifacts["project_profile"])
    plan = plan_for(script).model_dump(mode="json")
    for i, scene in enumerate(plan["scenes"]):
        scene.update(headline=script.segments[i].on_screen, narration=script.segments[i].text)
    plan["scenes"][0].update(kind="footage", asset_id="desk", source_start=0)
    plan.update(assets={"desk": {**library["assets"][0], "staged_path": str(store.settings.data_dir / "test-assets/desk.mp4")}}, claims=[])
    timings, cursor = [], 0
    for index, segment in enumerate(script.segments):
        duration = 22 if index == 0 else 5
        timings.append({"segment_id": segment.id, "start_sec": cursor, "end_sec": cursor + duration, "duration_sec": duration})
        cursor += duration
    calls = []

    async def model(settings, ctx, prompt, schema, *, decoding_schema):
        assert any(d.get('properties',{}).get('labels',{}).get('maxItems')==3 for d in decoding_schema['$defs'].values())
        calls.append(prompt)
        assert "22.0" in prompt
        return plan_for(script).scenes[0].model_dump()

    monkeypatch.setattr(order_assets, "ask_model", model)
    with store.lease(job) as token:
        store.begin(job, token, "asset_library", {})
        result = write_json(context.artifact_dir / "library.json", library)
        store.complete(job, token, "asset_library", library, [result.artifact_path])
    for _ in range(2):
        context.artifacts.update(script=script.model_dump(mode="json"), visuals=copy.deepcopy(plan), voice={"timestamps": timings})
        with store.lease(job) as token:
            ex = StageExecutor(store, job, token)
            ex.context = context
            fixed = asyncio.run(order_assets.fit_scenes_to_narration(ex))
        assert fixed["scenes"][0]["kind"] == "steps"
        assert fixed["scenes"][1:] == plan["scenes"][1:]
    assert len(calls) == 1
    assert store.get(job)["provider_calls"] == 1
