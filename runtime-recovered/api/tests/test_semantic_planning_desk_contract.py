"""Offline contract coverage; no vision inference or production state mutations."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from atnos.production.channels.wealth_blueprint.planning_desk_contract import ACTION_CHANGES
from atnos.production.order_store import file_hash
from atnos.production.project_manifest import SceneType, save_project_manifest
from atnos.production.steps import order_delivery
from test_semantic_qa_remotion_scene_gate import _minimal_manifest
from test_support import build_test_settings


def _context(tmp_path: Path, *, selected="wb_planning_desk_painted_v1", action="existing-assignments"):
    manifest = _minimal_manifest("test-desk-audit", [("s01", 0., 3.)])
    scene = manifest.scenes[0]
    scene.narration = "Inspect the notebook and check which portion is already committed."
    scene.semantic_overlay_kind = "reserve_readiness"
    scene.visual_relation = {"kind": "reserve_readiness", "stage": "review"}
    if action is not None:
        scene.visual_relation["planning_desk_action"] = action
    if selected is not None:
        manifest.brand.illustrationSystem = selected
    profile = {"project": "wealth_blueprint", "production": {"visual_rules": [], "visual_qa": {}}}
    if selected is not None:
        profile["native_illustration_system"] = selected
    path = tmp_path / "project_manifest.json"
    save_project_manifest(path, manifest)
    video = tmp_path / "final.mp4"
    video.write_bytes(b"isolated-fixture-video")
    covers = {}
    for orientation in ("portrait", "landscape"):
        image = tmp_path / f"{orientation}.png"
        Image.new("RGB", (8, 8), "navy").save(image)
        covers[orientation] = str(image)
    context = SimpleNamespace(artifact_dir=tmp_path, artifacts={
        "project_profile": profile, "production_order": {}, "covers": covers,
        "script": {"title": "Avoid counting one portion twice", "segments": [{
            "id": "s01", "text": scene.narration, "visual_intent": "review (review both roles)"}]},
        "edit": {"renderer": "remotion", "manifest_path": str(path), "output_file": str(video)},
    })
    return context, manifest, path


def _payload(target):
    return json.loads(target["contract"].split("\nAUTHOR VISUAL HANDOFF", 1)[0])


@pytest.mark.parametrize("system,material", [
    ("wb_planning_desk_v1", "deliberate ink contours"),
    ("wb_planning_desk_painted_v1", "painted wall/window/wood"),
])
def test_opted_in_contract_requires_the_real_action_palette_and_materials(tmp_path, system, material):
    context, manifest, _ = _context(tmp_path, selected=system)
    target = order_delivery.resolve_semantic_scene_targets(context)[0]
    contract = _payload(target)["native_illustration"]
    assert contract["illustration_system"] == system
    assert contract["planning_desk_action"] == "existing-assignments"
    assert contract["visible_change"] == ACTION_CHANGES["existing-assignments"]
    assert contract["chrome_palette"]["background"] == manifest.brand.background
    assert contract["world_palette"]["navy"] == "#171A3B"
    assert "Every listed accent need not appear" in contract["world_palette_scope"]
    assert material in contract["materials"]
    assert "generic text card, flat icon" in target["audit_instructions"]
    assert "established brand uses a deep green" not in target["audit_instructions"]
    assert "live-action footage is not required" not in _payload(target)["intended_visual"]


def test_world_palette_contract_matches_the_renderer_without_replacing_brand_chrome(tmp_path):
    context, manifest, path = _context(tmp_path)
    manifest.brand.background = "#142633"
    save_project_manifest(path, manifest)
    native = _payload(order_delivery.resolve_semantic_scene_targets(context)[0])["native_illustration"]
    source = (Path(__file__).resolve().parents[2] / "remotion-renderer/src/WBPlanningDeskScene.tsx").read_text(encoding="utf-8")
    for name, color in native["world_palette"].items():
        assert re.search(rf"\b{name.upper()}\s*=\s*'{color}'", source), "Audit world palette drifted from renderer"
    assert native["chrome_palette"]["background"] == "#142633"
    assert native["world_palette"]["navy"] == "#171A3B"


@pytest.mark.parametrize("action", [None, "review-both", "unsupported-action"])
def test_missing_or_wrong_action_is_rejected_before_model_audit(tmp_path, action):
    context, _, _ = _context(tmp_path, action=action)
    with pytest.raises(ValueError, match="action missing or inconsistent"):
        order_delivery.resolve_semantic_scene_targets(context)
    assert not (tmp_path / "qa").exists()


def test_opted_in_generic_scene_cannot_substitute_for_the_authored_scene(tmp_path):
    context, manifest, path = _context(tmp_path)
    manifest.scenes[0].semantic_overlay_kind = None
    manifest.scenes[0].visual_relation = None
    save_project_manifest(path, manifest)
    with pytest.raises(ValueError, match="Unsupported planning desk semantic contract"):
        order_delivery.resolve_semantic_scene_targets(context)


def test_selected_illustration_must_match_the_frozen_profile(tmp_path):
    context, manifest, path = _context(tmp_path)
    manifest.brand.illustrationSystem = "wb_planning_desk_v1"
    save_project_manifest(path, manifest)
    with pytest.raises(ValueError, match="differs from the frozen order profile"):
        order_delivery.resolve_semantic_scene_targets(context)


def test_default_remotion_contract_keeps_existing_instructions_and_shape(tmp_path):
    context, _, _ = _context(tmp_path, selected=None, action=None)
    target = order_delivery.resolve_semantic_scene_targets(context)[0]
    assert "native_illustration" not in _payload(target)
    assert "audit_instructions" not in target
    assert "Readable typography, numbers, charts, icons, or connectors" in _payload(target)["intended_visual"]
    assert "established brand uses a deep green" in order_delivery.SCENE_AUDIT_INSTRUCTIONS


def test_logo_only_cta_is_not_forced_to_reintroduce_desk_objects(tmp_path):
    context, manifest, path = _context(tmp_path)
    scene = manifest.scenes[0]
    scene.scene_type = SceneType.CTA
    scene.narration = "Follow Wealth Blueprint. Build wealth on purpose."
    scene.semantic_overlay_kind = None
    scene.visual_relation = None
    context.artifacts["script"]["segments"] = [{"id": "s01", "text": scene.narration,
                                                "visual_intent": "Wealth Blueprint logo only, no host or props"}]
    save_project_manifest(path, manifest)
    target = order_delivery.resolve_semantic_scene_targets(context)[0]
    native = _payload(target)["native_illustration"]
    assert native["logo_only"] is True
    assert "planning_desk_action" not in native
    assert "no host or desk props required" in native["visible_change"]


@pytest.mark.parametrize("selected", [None, "wb_planning_desk_painted_v1"])
@pytest.mark.parametrize('local_override',[False,True])
def test_normal_semantic_call_and_checkpoint_inputs_use_the_selected_contract(tmp_path, monkeypatch, selected,local_override):
    context, _, path = _context(tmp_path, selected=selected)
    calls, stages = [], {}
    if local_override:
        evidence={'model':'larger-local-vision-fixture','model_digest':'a'*64,'endpoint':'http://127.0.0.1:11436'}
        context.artifacts['_renderer_refresh']={'visual_qa_model':evidence}
        monkeypatch.setattr('atnos.production.remotion_quality_repair.verified_local_vision_model',lambda *_:evidence)

    def extract(_video, output, **_kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), "navy").save(output)
        return output

    def audit(_settings, _path, *, required_visual_contract):
        assert _settings.ollama_vision_model==('larger-local-vision-fixture' if local_override else 'qwen3-vl:8b-instruct')
        calls.append(required_visual_contract)
        return {"status": "pass", "contract_satisfied": True, "violations": [], "confidence": 9}

    class Executor:
        def __init__(self):
            self.context = context
            self.job_id='isolated-desk-audit'
            self.store = SimpleNamespace(settings=build_test_settings(tmp_path / "settings"),get=lambda _job:{'checkpoints':[]})

        async def run(self, stage, inputs, operation, **_kwargs):
            stages[stage] = inputs
            return (await operation()).payload

    monkeypatch.setattr(order_delivery.visual_semantic_audit, "extract_storyboard_contact_sheet", extract)
    monkeypatch.setattr(order_delivery.visual_semantic_audit, "audit_visual_contract_compliance", audit)
    monkeypatch.setattr(order_delivery.visual_semantic_audit, "audit_visual_semantics",
                        lambda *_a, **_k: {"status": "pass", "passed": True})
    result = asyncio.run(order_delivery.produce_semantic_qa(Executor()))
    expected = (order_delivery.PLANNING_DESK_AUDIT_INSTRUCTIONS if selected
                else order_delivery.SCENE_AUDIT_INSTRUCTIONS)
    assert calls[0].startswith(expected)
    assert stages["semantic/s01"]["instructions"] == expected
    assert stages['semantic/s01']['config']['minimum_score']==7
    assert stages['semantic/s01']['config']['model']==('larger-local-vision-fixture' if local_override else 'qwen3-vl:8b-instruct')
    assert stages["semantic/s01"]["manifest_sha256"] == file_hash(path)
    assert stages["semantic/s01"]["frame_window"] == {
        "start_frame": 0, "end_frame_exclusive": 90, "frame_rate": 30}
    assert result["reports"]["s01"]["sample_frame_indices"] == [0, 18, 36, 53, 71, 89]
    assert result["status"] == "passed"
    assert result["human_review"] == "not_performed"
    assert result["reports"]["s01"]["manifest_sha256"] == file_hash(path)
