"""Integration: produce_edit remotion branch builds ProjectManifest and packages edit.json.

Uses a lightweight fake executor — no live ElevenLabs / Remotion browser (those covered by ladder + video_job).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from atnos.production.channels.wealth_blueprint.manifest_adapters import order_artifacts_to_project_manifest
from atnos.production.project_manifest import ProjectManifest, save_project_manifest


class _FakeExecutor:
    def __init__(self, settings, artifact_dir: Path, artifacts: dict):
        self.settings = settings
        self.context = SimpleNamespace(artifact_dir=artifact_dir, artifacts=artifacts, job_id="order-remotion-unit")
        self.calls: list[str] = []
        self.inputs: dict[str, dict] = {}

    async def run(self, name, _inputs, fn):
        self.calls.append(name)
        self.inputs[name] = _inputs
        return await fn()


@pytest.mark.parametrize('placeholder_rejected', [False, True])
def test_produce_edit_branches_to_remotion(monkeypatch, tmp_path: Path, placeholder_rejected):
    from atnos.core.config import get_settings
    from atnos.production.channels.wealth_blueprint import remotion_order_edit as mod
    from atnos.production import remotion_quality_repair
    from atnos.production.channels.wealth_blueprint.scene_production_assets import (
        SceneAssetPlanResult,
        SceneStillAssignment,
    )

    settings = get_settings()
    still = tmp_path / "still.png"
    Image.new("RGB", (1080, 1920), (22, 30, 40)).save(still)
    manifest_repairs = []

    def apply_persisted_repair(executor, manifest):
        manifest_repairs.append(executor)
        # Exercise the integration boundary: the ordinary adapter's middle
        # ImageScene receives a presentation revision before render input is
        # saved. Durable validation of those revisions is tested separately.
        manifest.scenes[1].layout_id = "character_right_text_left"
        manifest.scenes[1].animation_presets = ["CHARACTER_ENTER_RIGHT", "TEXT_FADE"]
        return manifest

    monkeypatch.setattr(remotion_quality_repair, "apply_manifest_quality_repairs", apply_persisted_repair)

    def fake_health(*_a, **_k):
        return {"status": "ok", "blocked_dependencies": [], "checks": []}

    def fake_render(manifest_path, output):
        repaired = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        assert repaired["scenes"][1]["layout_id"] == "character_right_text_left"
        Path(output).write_bytes(b"\x00" * 8000)
        return {"ok": True, "output_path": str(output)}

    def fake_mux(*, raw_video_path, audio_path, output_path, target_lufs=-14.0):
        Path(output_path).write_bytes(b"\x00" * 9000)
        return {"ok": True, "reason": ""}

    def fake_qc(**_kwargs):
        return {"ok": True, "checks": [], "failed_count": 0}

    def fake_probe(_path):
        return {
            "available": True,
            "width": 1080,
            "height": 1920,
            "video_codec": "h264",
            "audio_codec": "aac",
            "fps": 30,
            "duration_s": 8.0,
        }

    def fake_assets(*, plan, settings, artifact_dir, visuals, allow_generate=False):
        scenes = plan.get("scenes") or []
        assignments = {}
        for scene in scenes:
            scene_id = str(scene.get("scene_id") or scene.get("id") or "s01")
            staged = Path(artifact_dir) / "assets" / f"{scene_id}.png"
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(still.read_bytes())
            assignments[scene_id] = SceneStillAssignment(
                scene_id=scene_id,
                path=staged,
                asset_id=f"asset-{scene_id}",
                production_role="character_cutout",
                channel="wealth_blueprint",
                version="1",
                preserve_alpha=True,
                fit="contain",
                source_path=str(still),
                function="host",
            )
        return SceneAssetPlanResult(status="ok", assignments=assignments)

    monkeypatch.setattr(mod, "run_wealth_blueprint_video_engine_health", fake_health)
    monkeypatch.setattr(mod, "resolve_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(mod, "render_project_manifest", fake_render)
    monkeypatch.setattr(mod, "_mux_remotion_video", fake_mux)
    monkeypatch.setattr(mod, "run_wealth_blueprint_qc", fake_qc)
    monkeypatch.setattr(mod, "probe_media", fake_probe)
    monkeypatch.setattr(mod, "resolve_scene_production_assets", fake_assets)
    # This fixture is deliberately a solid dummy; exercise both decisions of the
    # raster gate while testing edit orchestration, not its image classifier.
    from atnos.production.channels.wealth_blueprint import geometric_assets
    monkeypatch.setattr(geometric_assets, "looks_like_geometric_raster", lambda _path: placeholder_rejected)
    monkeypatch.setattr(
        mod,
        "_mix_voice_with_sound",
        lambda **_k: (_k["voice_path"], {"wired": True, "executed": False, "reason": "unit"}),
    )

    voice = tmp_path / "voice.wav"
    voice.write_bytes(b"RIFF" + b"\x00" * 100)
    artifacts = {
        "project_profile": {"production": {"renderer": "remotion", "safe_area": [72, 96, 1008, 1700]}},
        "remotion_options": {"prefer_order_adapter": True, "enable_sound": False},
        "script": {
            "segments": [
                {"id": "s01", "text": "Your paycheck feels bigger this year."},
                {"id": "s02", "text": "But wealth is what you keep."},
                {"id": "s03", "text": "Save first, then spend without guilt."},
            ]
        },
        "voice": {
            "output_file": str(voice),
            "sha256": "abc",
            "duration_seconds": 8.0,
            "timestamps": [
                {"segment_id": "s01", "text": "Your paycheck feels bigger this year.", "start_sec": 0.0, "end_sec": 2.5},
                {"segment_id": "s02", "text": "But wealth is what you keep.", "start_sec": 2.5, "end_sec": 5.0},
                {"segment_id": "s03", "text": "Save first, then spend without guilt.", "start_sec": 5.0, "end_sec": 8.0},
            ],
            "word_timestamps": [],
        },
        "visuals": {"scenes": [], "assets": {}},
        "captions": {"groups": []},
    }
    executor = _FakeExecutor(settings, tmp_path, artifacts)
    if placeholder_rejected:
        with pytest.raises(mod.RemotionRequiredError, match='geometric_placeholder_blocked_in_film'):
            asyncio.run(mod.produce_edit_remotion(executor))
        assert not (tmp_path / 'project_manifest.json').exists()
        assert not manifest_repairs
        return
    payload = asyncio.run(mod.produce_edit_remotion(executor))
    assert payload["renderer"] == "remotion"
    assert payload.get("editorial_fallback") is False
    assert payload.get("clips") == []
    assert payload.get("semantic_scene_source") == "project_manifest_mp4"
    assert payload.get("semantic_scene_windows")
    assert Path(payload["output_file"]).is_file()
    assert Path(payload["manifest_path"]).is_file()
    assert "remotion_render" in executor.calls
    assert len(executor.inputs["remotion_render"]["renderer_source_fingerprint"]) == 64
    assert artifacts["edit"]["renderer"] == "remotion"
    assert manifest_repairs == [executor]


def test_order_adapter_contract_for_remotion_input(tmp_path: Path):
    still = tmp_path / "still.png"
    Image.new("RGB", (1080, 1920), (10, 10, 10)).save(still)
    manifest = order_artifacts_to_project_manifest(
        job_id="x",
        topic_title="Keep more of your raise",
        script={
            "segments": [
                {"id": "a", "text": "One."},
                {"id": "b", "text": "Two."},
                {"id": "c", "text": "Three."},
            ]
        },
        voice={
            "timestamps": [
                {"segment_id": "a", "start_sec": 0, "end_sec": 1},
                {"segment_id": "b", "start_sec": 1, "end_sec": 2},
                {"segment_id": "c", "start_sec": 2, "end_sec": 3},
            ]
        },
        visuals={"scenes": [{}, {}, {}]},
        selected_image=still,
    )
    assert isinstance(manifest, ProjectManifest)
    path = tmp_path / "m.json"
    save_project_manifest(path, manifest)
    assert json.loads(path.read_text(encoding="utf-8"))["scenes"]
