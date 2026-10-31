"""Offline regression from real failed v047 replies; never rewrites their verdicts."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from atnos.production.project_manifest import SceneManifest, save_project_manifest
from atnos.production.steps import order_delivery
from test_semantic_planning_desk_contract import _context, _payload
from test_support import build_test_settings


FIXTURES = Path(__file__).parent / "fixtures" / "wb_semantic_v047_20261015"


def _read(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf8"))


def _actual_context(tmp_path):
    context, manifest, path = _context(tmp_path)
    manifest.scenes = [SceneManifest.model_validate(row) for row in _read("scenes.json")]
    context.artifacts["script"]["segments"] = [
        {"id": scene.scene_id, "text": scene.narration} for scene in manifest.scenes]
    save_project_manifest(path, manifest)
    return context, manifest, path


def test_real_v047_replies_remain_intact_failed_evidence():
    provenance = _read("provenance.json")
    for name, expected in provenance["fixture_hashes"].items():
        assert hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest() == expected
    for scene_id in ("s02", "s03", "s05"):
        report = _read(f"{scene_id}_contact.json")
        assert report["video_sha256"] == provenance["video_sha256"]
        assert report["status"] == "failed"
        assert report["verdict"]["violations"]
        assert not order_delivery._verdict_passed(report["verdict"], minimum_score=1)


def test_planning_instructions_distinguish_before_after_and_crop_without_waiving_quality(tmp_path):
    context, _, _ = _actual_context(tmp_path)
    for target in order_delivery.resolve_semantic_scene_targets(context):
        instructions = target["audit_instructions"]
        native = _payload(target)["native_illustration"]
        assert "final annotation is expected to be absent before its spoken cue" in instructions
        assert "do not require the result in every panel" in instructions
        assert "a lifted or resting pencil between strokes" in instructions
        assert "tight notebook crop may exclude" in instructions
        assert "Missing required objects/relations" in instructions
        assert "generic text card, flat icon, or disconnected diagram" in instructions
        assert "Smoothness between samples remains unknown" in instructions
        assert native["framing_scope"] == "visible_crop_with_scene_continuity"
        assert "local pigment shading and irregular ink contours" in native["materials"]


def test_actual_spoken_cues_are_recorded_data_not_invented_animation_times(tmp_path):
    context, manifest, _ = _actual_context(tmp_path)
    targets = order_delivery.resolve_semantic_scene_targets(context)
    for target, scene in zip(targets, manifest.scenes, strict=True):
        timing = _payload(target)["native_illustration"]["speech_timing"]
        assert timing["status"] == "recorded_alignment"
        assert timing["source"] == "ProjectManifest.caption_words"
        assert timing["clock"] == "absolute_seconds_in_final_mp4"
        assert timing["words"] == [word.model_dump(mode="json") for word in scene.caption_words]
        assert timing["scene_start_sec"] == scene.timing.start_sec
        assert timing["scene_end_sec"] == scene.timing.end_sec
        assert "not measured visual event times" in timing["limitations"]
        assert "animation_start_sec" not in timing
    by_scene = {scene.scene_id: {word.text.rstrip(".,"): word.start_sec
                                for word in scene.caption_words} for scene in manifest.scenes}
    # Real bad replies demanded the later result in these pre-cue samples.
    assert 307 / 30 < by_scene["s02"]["future"] == 10.386
    assert 471 / 30 < by_scene["s03"]["emergency"] == 16.789
    assert 512 / 30 < by_scene["s03"]["reduces"] == 17.669 < 554 / 30
    assert 908 / 30 < by_scene["s05"]["impact"] == 31.597 < 983 / 30


@pytest.mark.parametrize("damage", ["missing", "text_mismatch", "outside_scene", "out_of_order"])
def test_missing_or_inconsistent_speech_timing_stays_unknown(tmp_path, damage):
    context, manifest, path = _actual_context(tmp_path)
    scene = manifest.scenes[0]
    if damage == "missing":
        scene.caption_words = []
    elif damage == "text_mismatch":
        scene.caption_words[0] = scene.caption_words[0].model_copy(update={"text": "Invented"})
    elif damage == "outside_scene":
        scene.caption_words[0] = scene.caption_words[0].model_copy(update={
            "start_sec": scene.timing.end_sec + 1, "end_sec": scene.timing.end_sec + 2})
    else:
        scene.caption_words = list(reversed(scene.caption_words))
    save_project_manifest(path, manifest)
    timing = _payload(order_delivery.resolve_semantic_scene_targets(context)[0])["native_illustration"]["speech_timing"]
    assert timing["status"] == "UNKNOWN"
    assert timing["words"] == []
    assert timing["reason"]
    assert "fallback" not in timing


def test_other_profiles_keep_original_instructions_and_contract(tmp_path):
    context, _, _ = _context(tmp_path, selected=None, action=None)
    target = order_delivery.resolve_semantic_scene_targets(context)[0]
    assert "audit_instructions" not in target
    assert "native_illustration" not in _payload(target)
    assert hashlib.sha256(order_delivery.SCENE_AUDIT_INSTRUCTIONS.encode()).hexdigest() == (
        _read("provenance.json")["default_scene_audit_instructions_sha256"])


def test_replaying_all_three_actual_failed_replies_cannot_turn_qa_green(tmp_path, monkeypatch):
    context, _, _ = _actual_context(tmp_path)
    calls, stages = [], {}

    def extract(_video, output, **_kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), "navy").save(output)
        return output

    def audit(_settings, path, *, required_visual_contract):
        scene_id = path.stem.removesuffix("_contact")
        calls.append((scene_id, required_visual_contract))
        return _read(f"{scene_id}_contact.json")["verdict"]

    class Executor:
        def __init__(self):
            self.context = context
            self.job_id='isolated-v047-audit'
            self.store = SimpleNamespace(settings=build_test_settings(tmp_path / "settings"),get=lambda _job:{'checkpoints':[]})

        async def run(self, stage, inputs, operation, **_kwargs):
            stages[stage] = inputs
            return (await operation()).payload

    monkeypatch.setattr(order_delivery.visual_semantic_audit, "extract_storyboard_contact_sheet", extract)
    monkeypatch.setattr(order_delivery.visual_semantic_audit, "audit_visual_contract_compliance", audit)
    monkeypatch.setattr(order_delivery.visual_semantic_audit, "audit_visual_semantics",
                        lambda *_a, **_k: {"status": "pass", "passed": True})
    result = asyncio.run(order_delivery.produce_semantic_qa(Executor()))
    assert result["status"] == "failed"
    assert [scene_id for scene_id, _ in calls] == ["s02", "s03", "s05"]
    for scene_id, prompt in calls:
        report = result["reports"][scene_id]
        assert report["status"] == "failed"
        assert report["verdict"] == _read(f"{scene_id}_contact.json")["verdict"]
        assert "final annotation is expected to be absent before its spoken cue" in prompt
        assert "speech_timing" in stages[f"semantic/{scene_id}"]["contract"]
    assert result["human_review"] == "not_performed"


@pytest.mark.parametrize("verdict", [
    {"status": "pass", "confidence": 10},
    {"status": "pass", "contract_satisfied": False, "confidence": 10, "violations": []},
    {"status": "pass", "contract_satisfied": True, "confidence": 10, "violations": ["Required action absent"]},
    {"status": "UNKNOWN", "contract_satisfied": True, "confidence": 10, "violations": []},
])
def test_schema_only_or_unresolved_verdict_does_not_pass(verdict):
    assert not order_delivery._verdict_passed(verdict, minimum_score=1)
