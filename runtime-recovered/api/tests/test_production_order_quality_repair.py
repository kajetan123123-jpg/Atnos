from __future__ import annotations

import asyncio
import copy
from pathlib import Path

import pytest

from atnos.production.order_execution import StageExecutor
from atnos.production.order_store import ProductionBlocked, ProductionOrderStore, file_hash
from atnos.production.steps import order_repair
from atnos.production.steps.common import write_json
from test_production_order_assets import plan_for
from test_production_order_script import script
from test_production_order_store import store, create


@pytest.fixture
def trial(store, script):
    job = create(store)["id"]
    context = store.context(job)
    scenes = plan_for(script).model_dump(mode="json")["scenes"]
    clips = []
    for scene, segment in zip(scenes, script.segments, strict=True):
        scene.update(headline=segment.on_screen, narration=segment.text)
        clip = context.artifact_dir / f"{segment.id}.mp4"
        clip.write_bytes(b"contract-test clip; rendering is tested separately")
        clips.append({"segment_id": segment.id, "path": str(clip), "frame_range": [0, 150]})
    artifacts = {"script": script.model_dump(mode="json"), "visuals": {"scenes": scenes, "assets": {}, "claims": []},
                 "edit": {"clips": clips}, "voice": {"sentinel": "must not change"}}
    with store.lease(job) as token:
        # Media stages require the same completed zero-claim script checkpoint
        # as production. Do not bypass the financial guard in this fixture.
        store.begin(job, token, "script", {})
        checked = write_json(context.artifact_dir / "script.json", artifacts["script"])
        store.complete(job, token, "script", checked.payload, [checked.artifact_path])
        store.begin(job, token, "asset_library", {})
        result = write_json(context.artifact_dir / "library.json", {"assets": []})
        store.complete(job, token, "asset_library", result.payload, [result.artifact_path])
    report = {"status": "failed", "clip_sha256": file_hash(Path(clips[0]["path"])),
              "verdict": {"status": "fail", "violations": ["Actions are unrelated to the narration"]}}
    return store, job, artifacts, report


def install_model(monkeypatch, artifacts, calls, *, invalid=False):
    async def model(settings, context, prompt, schema):
        calls.append(prompt)
        before = artifacts["visuals"]["scenes"][0]
        result = {key: before[key] for key in order_repair.EditorialScene.model_fields}
        result["labels"] = ["List recurring expenses", "Choose a payday transfer"]
        if invalid:
            result["segment_id"] = "unrelated"
        return result
    monkeypatch.setattr(order_repair, "ask_model", model)


def test_repairs_one_scene_archives_real_input_and_restores_after_restart(trial, monkeypatch):
    store, job, artifacts, report = trial
    calls = []
    install_model(monkeypatch, artifacts, calls)
    with store.lease(job) as token:
        ex = StageExecutor(store, job, token)
        ex.context.artifacts.update(copy.deepcopy(artifacts))
        result = asyncio.run(order_repair.repair_scene(ex, "s01", report))
        assert result["rendered_qa"] == "not_started"
        assert result["source"] == "automatic_model_repair"
        assert ex.context.artifacts["visuals"]["scenes"][1:] == artifacts["visuals"]["scenes"][1:]
        assert ex.context.artifacts["voice"] == artifacts["voice"]
    restored = ProductionOrderStore(store.settings)
    with restored.lease(job) as token:
        ex = StageExecutor(restored, job, token)
        ex.context.artifacts.update(copy.deepcopy(artifacts))
        final = asyncio.run(order_repair.apply_quality_repairs(ex))
        assert final["scenes"][0] == result["scene"]
        assert asyncio.run(order_repair.apply_quality_repairs(ex)) == final
    assert len(calls) == 1 and restored.get(job)["provider_calls"] == 1
    archive = restored.context(job).artifact_dir / "scene_repairs/s01/1/before.mp4"
    assert file_hash(archive) == report["clip_sha256"]
    assert restored.get(job)["status"] != "ready"


def test_invalid_repair_has_bounded_calls_and_cannot_become_ready(trial, monkeypatch):
    store, job, artifacts, report = trial
    calls = []
    install_model(monkeypatch, artifacts, calls, invalid=True)
    with store.lease(job) as token:
        ex = StageExecutor(store, job, token)
        ex.context.artifacts.update(copy.deepcopy(artifacts))
        with pytest.raises(ProductionBlocked, match="attempt_limit"):
            asyncio.run(order_repair.repair_scene(ex, "s01", report))
    state = store.get(job)
    assert len(calls) == state["provider_calls"] == state["profile"]["production"]["max_attempts_per_element"]
    assert state["status"] == "blocked"
    assert artifacts["visuals"]["scenes"][0]["labels"] == ["Review essential bills", "Choose a manageable transfer"]


@pytest.mark.parametrize("problem", ["unavailable", "wrong_clip", "no_visual_change"])
def test_repair_requires_actionable_evidence_and_actual_visual_change(trial, monkeypatch, problem):
    store, job, artifacts, report = trial
    calls = []
    install_model(monkeypatch, artifacts, calls)
    if problem == "unavailable":
        report["verdict"]["status"] = "unavailable"
    elif problem == "wrong_clip":
        report["clip_sha256"] = "wrong"
    else:
        async def no_change(*_):
            before = artifacts["visuals"]["scenes"][0]
            return {key: before[key] for key in order_repair.EditorialScene.model_fields}
        monkeypatch.setattr(order_repair, "ask_model", no_change)
    with store.lease(job) as token:
        ex = StageExecutor(store, job, token)
        ex.context.artifacts.update(copy.deepcopy(artifacts))
        with pytest.raises(ProductionBlocked):
            asyncio.run(order_repair.repair_scene(ex, "s01", report))
    if problem != "no_visual_change":
        assert not calls and store.get(job)["provider_calls"] == 0
    assert store.get(job)["status"] != "ready"


def test_changed_archive_blocks_resume_instead_of_reusing_unverified_edit(trial, monkeypatch):
    store, job, artifacts, report = trial
    install_model(monkeypatch, artifacts, [])
    with store.lease(job) as token:
        ex = StageExecutor(store, job, token)
        ex.context.artifacts.update(copy.deepcopy(artifacts))
        asyncio.run(order_repair.repair_scene(ex, "s01", report))
    (store.context(job).artifact_dir / "scene_repairs/s01/1/before.mp4").write_bytes(b"modified")
    with store.lease(job) as token:
        ex = StageExecutor(store, job, token)
        ex.context.artifacts.update(copy.deepcopy(artifacts))
        with pytest.raises(ProductionBlocked, match="evidence"):
            asyncio.run(order_repair.apply_quality_repairs(ex))


def test_quality_loop_rechecks_changed_scene_without_tts(trial, monkeypatch):
    from atnos.production.steps import order_delivery, order_media
    store, job, artifacts, report = trial
    calls, counts = [], {"render": 0, "qa": 0}
    install_model(monkeypatch, artifacts, calls)
    async def media(ex):
        return {"status": "passed"}
    async def qa(ex):
        counts["qa"] += 1
        return {"status": "failed", "reports": {"s01": report}} if counts["qa"] == 1 else {"status": "passed", "reports": {}}
    async def render(ex):
        counts["render"] += 1
        assert ex.context.artifacts["visuals"]["scenes"][1:] == artifacts["visuals"]["scenes"][1:]
        assert ex.context.artifacts["voice"] == artifacts["voice"]
    monkeypatch.setattr(order_media, "produce_media_qa", media)
    monkeypatch.setattr(order_delivery, "produce_semantic_qa", qa)
    monkeypatch.setattr(order_media, "produce_edit", render)
    with store.lease(job) as token:
        ex = StageExecutor(store, job, token)
        ex.context.artifacts.update(copy.deepcopy(artifacts))
        assert asyncio.run(order_repair.produce_final_quality(ex))["status"] == "passed"
    assert len(calls) == 1 and counts == {"render": 1, "qa": 2}
