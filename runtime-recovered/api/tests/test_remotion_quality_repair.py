"""Autonomous repairs must change renderer inputs without changing validated content."""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

from PIL import Image
import pytest

from atnos.production import remotion_quality_repair as repair
from atnos.production.order_execution import StageExecutor
from atnos.production.order_store import ProductionBlocked, ProductionOrderStore, file_hash
from atnos.production.project_manifest import AssetProvenance, SceneAsset, SceneType, load_project_manifest, save_project_manifest
from atnos.production.steps import order_repair
from atnos.production.steps.common import write_json
from test_production_order_script import script
from test_production_order_store import create, store
from test_semantic_qa_remotion_scene_gate import _minimal_manifest


@pytest.fixture
def trial(store, script):
    job = create(store)["id"]
    context = store.context(job)
    image = context.artifact_dir / "assets" / "alex.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (540, 960), (20, 38, 51, 255)).save(image)
    manifest = _minimal_manifest(job, [("s01", 0, 2), ("s02", 2, 5)])
    scene = manifest.scenes[1]
    scene.scene_type = SceneType.IMAGE
    scene.layout_id = "character_left_text_right"
    scene.animation_presets = ["CHARACTER_ENTER_LEFT", "TEXT_SLIDE_UP"]
    scene.assets = [SceneAsset(asset_id="alex-unit", role="selected_image", path=str(image), exists=True,
        provenance=AssetProvenance(provider="test_fixture", input_hash="a" * 64, artifact_hash=file_hash(image),
            generation_parameters={"production_role": "character_cutout", "fit": "contain"}),
        qa={"status": "passed", "action": "PASS"})]
    path = context.artifact_dir / "project_manifest.json"
    save_project_manifest(path, manifest)
    video = context.artifact_dir / "assets" / "final_short.mp4"
    video.write_bytes(b"unit fixture for repair provenance only; not a real rendered film")
    artifacts = {"script": script.model_dump(mode="json"), "voice": {"sha256": "voice-fixed", "timestamps": []},
                 "captions": {"groups": []}, "edit": {"renderer": "remotion", "manifest_path": str(path),
                     "output_file": str(video), "clips": [], "semantic_scene_source": "project_manifest_mp4"}}
    report = {"status": "failed", "source": "remotion_manifest_mp4", "scene_id": "s02",
        "manifest_sha256": file_hash(path), "video_sha256": file_hash(video), "clip_sha256": file_hash(video),
        "timing": {"start_sec": 2., "end_sec": 5.},
        "verdict": {"status": "fail", "contract_satisfied": False, "confidence": 9,
                    "violations": ["The character overlaps the headline; text enters too quickly"]}}
    with store.lease(job) as token:
        store.begin(job, token, "script", {})
        result = write_json(context.artifact_dir / "script.json", artifacts["script"])
        store.complete(job, token, "script", result.payload, [result.artifact_path])
    return store, job, artifacts, report, manifest


def install_model(monkeypatch, *, changes=None):
    calls = []
    async def model(_settings, _context, prompt, _schema):
        calls.append(prompt)
        return {"supported": True, "reason": "Move the host right, keeping the complete headline on the left and reveal it calmly.",
                "layout_id": "character_right_text_left", "animation_presets": ["CHARACTER_ENTER_RIGHT", "TEXT_FADE"],
                **(changes or {})}
    monkeypatch.setattr(repair, "ask_model", model)
    return calls


def execute(trial, monkeypatch, *, changes=None):
    store, job, artifacts, report, manifest = trial
    calls = install_model(monkeypatch, changes=changes)
    with store.lease(job) as token:
        ex = StageExecutor(store, job, token)
        ex.context.artifacts.update(copy.deepcopy(artifacts))
        result = asyncio.run(order_repair.repair_scene(ex, "s02", report))
        fixed = repair.apply_manifest_quality_repairs(ex, manifest)
    return result, fixed, calls


def test_persisted_layout_repair_preserves_content_and_restores_on_restart(trial, monkeypatch):
    store, job, artifacts, report, manifest = trial
    result, fixed, calls = execute(trial, monkeypatch)
    assert result["rendered_qa"] == "not_started"
    assert fixed.scenes[0] == manifest.scenes[0]
    before, after = manifest.scenes[1].model_dump(mode="json"), fixed.scenes[1].model_dump(mode="json")
    assert repair._immutable(before) == repair._immutable(after)
    assert after["layout_id"] == "character_right_text_left"
    assert len(calls) == store.get(job)["provider_calls"] == 1
    restored = ProductionOrderStore(store.settings)
    with restored.lease(job) as token:
        ex = StageExecutor(restored, job, token)
        ex.context.artifacts.update(copy.deepcopy(artifacts))
        assert repair.apply_manifest_quality_repairs(ex, manifest) == fixed
        assert repair.apply_manifest_quality_repairs(ex, fixed) == fixed
    assert store.get(job)["status"] != "ready"
    directory = store.context(job).artifact_dir / "remotion_repairs" / "s02" / "1"
    assert file_hash(directory / "before.mp4") == report["video_sha256"]
    assert file_hash(directory / "before_manifest.json") == report["manifest_sha256"]


def test_known_lossy_overlay_projection_can_be_retired_without_erasing_history(trial,monkeypatch):
    store,job,artifacts,report,manifest=trial
    manifest.scenes[1].semantic_overlay_kind='notebook_log'
    path=Path(artifacts['edit']['manifest_path'])
    save_project_manifest(path,manifest)
    report['manifest_sha256']=file_hash(path)
    original_load=repair.load_project_manifest
    def lossy_read(*args,**kwargs):
        result=original_load(*args,**kwargs)
        result.scenes[1].semantic_overlay_kind=None
        return result
    install_model(monkeypatch)
    monkeypatch.setattr(repair,'load_project_manifest',lossy_read)
    with store.lease(job) as token:
        ex=StageExecutor(store,job,token)
        ex.context.artifacts.update(copy.deepcopy(artifacts))
        asyncio.run(order_repair.repair_scene(ex,'s02',report))
        monkeypatch.setattr(repair,'load_project_manifest',original_load)
        result=asyncio.run(repair.retire_overlay_projection_repair(ex,'s02',1))
        assert result['lost_overlay']=='notebook_log' and result['history_deleted'] is False
        assert repair._rows(ex,'s02')==[]
        assert repair.apply_manifest_quality_repairs(ex,manifest)==manifest
    rows={r['stage']:r for r in store.get(job)['checkpoints']}
    assert rows['remotion_quality/s02/1']['status']=='done'
    assert rows['remotion_quality_retirement/s02/1']['status']=='done'
    assert store.get(job)['provider_calls']==1 and store.get(job)['status']!='ready'


def test_valid_presentation_repair_cannot_be_retired_as_projection_error(trial,monkeypatch):
    execute(trial,monkeypatch)
    store,job,artifacts,_,_=trial
    with store.lease(job) as token:
        ex=StageExecutor(store,job,token)
        ex.context.artifacts.update(copy.deepcopy(artifacts))
        with pytest.raises(ProductionBlocked,match='No proven known overlay projection loss'):
            asyncio.run(repair.retire_overlay_projection_repair(ex,'s02',1))


@pytest.mark.parametrize('overlay',['notebook_log','habit_repeat'])
def test_supported_overlay_survives_hashed_manifest_roundtrip(trial,overlay):
    _,_,artifacts,_,manifest=trial
    manifest.scenes[1].semantic_overlay_kind=overlay
    path=Path(artifacts['edit']['manifest_path'])
    save_project_manifest(path,manifest)
    loaded=load_project_manifest(path,verify_hash=True)
    assert loaded.scenes[1].semantic_overlay_kind==overlay
    assert loaded.scenes[1].model_dump(mode='json')==manifest.scenes[1].model_dump(mode='json')


@pytest.mark.parametrize("field", ["video_sha256", "manifest_sha256", "clip_sha256", "source", "scene_id"])
def test_stale_or_unbound_review_does_not_spend_an_author_call(trial, monkeypatch, field):
    store, job, artifacts, report, _ = trial
    report[field] = "stale"
    calls = install_model(monkeypatch)
    with store.lease(job) as token:
        ex = StageExecutor(store, job, token)
        ex.context.artifacts.update(copy.deepcopy(artifacts))
        with pytest.raises(ProductionBlocked, match="current manifest and actual MP4"):
            asyncio.run(order_repair.repair_scene(ex, "s02", report))
    assert not calls and store.get(job)["provider_calls"] == 0


def test_wrong_narration_field_is_strictly_rejected_with_bounded_attempts(trial, monkeypatch):
    store, job, artifacts, report, _ = trial
    calls = install_model(monkeypatch, changes={"narration": "Altered narration"})
    with store.lease(job) as token:
        ex = StageExecutor(store, job, token)
        ex.context.artifacts.update(copy.deepcopy(artifacts))
        with pytest.raises(ProductionBlocked, match="attempt_limit"):
            asyncio.run(order_repair.repair_scene(ex, "s02", report))
        assert ex.context.artifacts["script"] == artifacts["script"]
        assert ex.context.artifacts["voice"] == artifacts["voice"]
    assert len(calls) == store.get(job)["profile"]["production"]["max_attempts_per_element"]
    assert store.get(job)["status"] == "blocked"


@pytest.mark.parametrize("tamper", ["archive", "voice", "caption", "scene"])
def test_changed_proof_or_protected_inputs_block_revision_reuse(trial, monkeypatch, tamper):
    store, job, artifacts, report, manifest = trial
    execute(trial, monkeypatch)
    if tamper == "archive":
        (store.context(job).artifact_dir / "remotion_repairs/s02/1/before.mp4").write_bytes(b"changed proof")
    elif tamper == "voice":
        artifacts["voice"]["sha256"] = "new voice"
    elif tamper == "caption":
        artifacts["captions"]["groups"] = [[{"text": "changed subtitles"}]]
    else:
        manifest.scenes[1].headline = "Changed financial assertion"
    with store.lease(job) as token:
        ex = StageExecutor(store, job, token)
        ex.context.artifacts.update(copy.deepcopy(artifacts))
        with pytest.raises(ProductionBlocked, match="evidence|recorded repair chain"):
            repair.apply_manifest_quality_repairs(ex, manifest)


def test_full_background_and_unknown_scene_controls_are_not_fake_repaired(trial):
    _, _, _, _, manifest = trial
    scene = manifest.scenes[1]
    scene.assets[0].provenance.generation_parameters.update(production_role="background", fit="cover")
    with pytest.raises(ProductionBlocked, match="ignores layout/preset"):
        repair.supported_layouts(scene)
    scene.scene_type = SceneType.CHART
    with pytest.raises(ProductionBlocked, match="scene family"):
        repair.supported_layouts(scene)


def test_render_capacity_is_checked_before_another_model_call(trial, monkeypatch):
    store, job, artifacts, report, _ = trial
    calls = install_model(monkeypatch)
    with store.lease(job) as token:
        ex = StageExecutor(store, job, token)
        ex.context.artifacts.update(copy.deepcopy(artifacts))
        limit = store.get(job)["profile"]["production"]["max_attempts_per_element"]
        for _ in range(limit):
            store.begin(job, token, "semantic/s01", {})
            store.fail(job, token, "semantic/s01", category="fixture", message="capacity consumed")
        with pytest.raises(ProductionBlocked, match="complete review capacity"):
            asyncio.run(order_repair.repair_scene(ex, "s02", report))
    assert not calls and store.get(job)["provider_calls"] == 0


def test_no_effective_motion_change_is_rejected_even_if_json_changes():
    before = {"layout_id": "character_left_text_right", "animation_presets": []}
    after = {"layout_id": "character_left_text_right", "animation_presets": ["CHARACTER_ENTER_LEFT", "TEXT_FADE"]}
    assert repair._rendered_presentation(before) == repair._rendered_presentation(after)
    diagram_before = {"layout_id": "split_screen", "animation_presets": ["CHARACTER_ENTER_RIGHT", "TEXT_SLIDE_UP"],
                      "semantic_overlay_kind": "income_split"}
    diagram_after = {**diagram_before, "animation_presets": ["CHARACTER_ENTER_RIGHT", "TEXT_FADE"]}
    assert repair._rendered_presentation(diagram_before) == repair._rendered_presentation(diagram_after)


def test_unsupported_diagnostic_stops_without_lowering_quality_gate(trial, monkeypatch):
    store, job, artifacts, report, _ = trial
    calls = install_model(monkeypatch, changes={"supported": False, "reason": "Required scene object is absent from the image and cannot be recovered by a layout."})
    with store.lease(job) as token:
        ex = StageExecutor(store, job, token)
        ex.context.artifacts.update(copy.deepcopy(artifacts))
        with pytest.raises(ProductionBlocked, match="unsupported"):
            asyncio.run(order_repair.repair_scene(ex, "s02", report))
    state = store.get(job)
    assert len(calls) == 1 and state["status"] == "blocked"
    assert json.loads(state["error"])["category"] == "remotion_repair_unsupported"


def test_final_quality_rerenders_and_reaudits_without_another_voice_run(trial, monkeypatch):
    from atnos.production.steps import order_delivery, order_media
    store, job, artifacts, report, manifest = trial
    calls = install_model(monkeypatch)
    counts = {"render": 0, "qa": 0}
    async def media(ex):
        return {"status": "passed"}
    async def qa(ex):
        counts["qa"] += 1
        return {"status": "failed", "reports": {"s02": report}} if counts["qa"] == 1 else {"status": "passed", "reports": {}}
    async def render(ex):
        counts["render"] += 1
        fixed = repair.apply_manifest_quality_repairs(ex, manifest)
        assert fixed.scenes[1].layout_id == "character_right_text_left"
        assert fixed.scenes[0] == manifest.scenes[0]
        assert ex.context.artifacts["voice"] == artifacts["voice"]
        assert ex.context.artifacts["captions"] == artifacts["captions"]
    monkeypatch.setattr(order_media, "produce_media_qa", media)
    monkeypatch.setattr(order_delivery, "produce_semantic_qa", qa)
    monkeypatch.setattr(order_media, "produce_edit", render)
    with store.lease(job) as token:
        ex = StageExecutor(store, job, token)
        ex.context.artifacts.update(copy.deepcopy(artifacts))
        assert asyncio.run(order_repair.produce_final_quality(ex))["status"] == "passed"
    assert len(calls) == 1 and counts == {"render": 1, "qa": 2}
