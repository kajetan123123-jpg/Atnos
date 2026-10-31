"""Order artifacts → ProjectManifest → Remotion → MP4 (REMOTION_REQUIRED, no sound)."""

from __future__ import annotations

import asyncio
import json
import math
import wave
from array import array
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from atnos.core.config import get_settings
from atnos.production.channels.wealth_blueprint.manifest_adapters import order_artifacts_to_project_manifest
from atnos.production.channels.wealth_blueprint.remotion_mode import REMOTION_REQUIRED
from atnos.production.channels.wealth_blueprint.remotion_order_edit import (
    RemotionRequiredError,
    produce_edit_remotion,
)
from atnos.production.channels.wealth_blueprint.renderer_boundary import RENDERER_BOUNDARY
from atnos.production.project_manifest import save_project_manifest
from atnos.production.qa_gate import probe_media
from atnos.production.remotion_renderer import render_project_manifest
from atnos.production.steps.assembler import _mux_remotion_video
from atnos.production.steps.order_media import produce_edit


EVIDENCE_ROOT = Path(r"E:\Atnos\App\data\production\wealth_blueprint\canonical-migration-phase1")


def _tone(path: Path, seconds: float = 6.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rate = 24_000
    n = max(1, int(seconds * rate))
    samples = array("h")
    for i in range(n):
        env = min(1.0, i / 400) * min(1.0, (n - i) / 800)
        samples.append(int(6500 * env * math.sin(2 * math.pi * 170 * (i / rate))))
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(samples.tobytes())


def _order_fixture(tmp_path: Path, *, duration: float = 6.0) -> dict:
    voice = tmp_path / "voice.wav"
    _tone(voice, duration)
    still = tmp_path / "still.png"
    settings = get_settings()
    refs = settings.wealth_blueprint_channel_references_dir
    source = None
    if refs:
        hits = sorted(Path(refs).glob("**/*.png"))
        if hits:
            source = hits[0]
    if source is None and settings.app_root:
        candidate = Path(settings.app_root) / "config" / "channel_references" / "20260916" / "image-1.png"
        if candidate.is_file():
            source = candidate
    if source is None:
        Image.new("RGB", (1080, 1920), (20, 28, 36)).save(still)
    else:
        Image.open(source).convert("RGB").resize((1080, 1920)).save(still)

    third = duration / 3.0
    return {
        "project_profile": {
            "channel_name": "Wealth Blueprint",
            "production": {"renderer": "remotion", "safe_area": [72, 96, 1008, 1700]},
        },
        "remotion_options": {"prefer_order_adapter": True, "enable_sound": False},
        "script": {
            "topic": "Keep more of your raise",
            "segments": [
                {"id": "s01", "text": "Your paycheck feels bigger this year."},
                {"id": "s02", "text": "But wealth is what you keep."},
                {"id": "s03", "text": "Save first, then spend without guilt."},
            ],
        },
        "voice": {
            "output_file": str(voice),
            "sha256": "fixture",
            "duration_seconds": duration,
            "timestamps": [
                {
                    "segment_id": "s01",
                    "text": "Your paycheck feels bigger this year.",
                    "start_sec": 0.0,
                    "end_sec": third,
                },
                {
                    "segment_id": "s02",
                    "text": "But wealth is what you keep.",
                    "start_sec": third,
                    "end_sec": third * 2,
                },
                {
                    "segment_id": "s03",
                    "text": "Save first, then spend without guilt.",
                    "start_sec": third * 2,
                    "end_sec": duration,
                },
            ],
            "word_timestamps": [],
        },
        "visuals": {
            "scenes": [
                {"layout_id": "fullscreen_statement", "headline": "Raise"},
                {"layout_id": "quote_or_fact", "headline": "Keep"},
                {"layout_id": "fullscreen_statement", "headline": "Save"},
            ],
            "assets": {"still": {"staged_path": str(still), "sha256": "x"}},
        },
        "captions": {"groups": []},
    }


class _FakeExecutor:
    def __init__(self, settings, artifact_dir: Path, artifacts: dict):
        self.settings = settings
        self.context = SimpleNamespace(artifact_dir=artifact_dir, artifacts=artifacts, job_id=artifact_dir.name)
        self.calls: list[str] = []

    async def run(self, name, _inputs, fn):
        self.calls.append(name)
        return await fn()


def test_order_artifacts_to_manifest_to_remotion_mp4(tmp_path: Path):
    """Integration: Production Order fixture → adapter → Remotion → MP4."""
    evidence = EVIDENCE_ROOT / "order_remotion_positive"
    evidence.mkdir(parents=True, exist_ok=True)
    work = tmp_path / "order_pos"
    work.mkdir()
    artifacts = _order_fixture(work, duration=6.0)
    settings = get_settings()

    still = Path(artifacts["visuals"]["assets"]["still"]["staged_path"])
    manifest = order_artifacts_to_project_manifest(
        job_id="canonical-order-001",
        topic_title="Keep more of your raise",
        script=artifacts["script"],
        voice=artifacts["voice"],
        visuals=artifacts["visuals"],
        selected_image=still,
    )
    manifest_path = evidence / "project_manifest.json"
    save_project_manifest(manifest_path, manifest)
    # also keep a copy in work
    save_project_manifest(work / "project_manifest.json", manifest)

    silent = evidence / "silent.mp4"
    remotion = render_project_manifest(manifest_path, silent)
    (evidence / "remotion_result.json").write_text(json.dumps(remotion, indent=2, default=str) + "\n", encoding="utf-8")
    assert remotion.get("ok"), remotion

    voice = Path(artifacts["voice"]["output_file"])
    final = evidence / "final.mp4"
    mux = _mux_remotion_video(raw_video_path=silent, audio_path=voice, output_path=final, target_lufs=-14.0)
    (evidence / "mux.json").write_text(json.dumps(mux, indent=2, default=str) + "\n", encoding="utf-8")
    assert mux.get("ok"), mux

    assert final.is_file()
    assert final.stat().st_size > 50_000
    probe = probe_media(final)
    (evidence / "ffprobe.json").write_text(json.dumps(probe, indent=2) + "\n", encoding="utf-8")
    assert probe.get("width") == 1080
    assert probe.get("height") == 1920
    assert probe.get("video_codec") == "h264"
    assert abs(float(probe.get("fps") or 0) - 30.0) <= 0.05
    assert abs(float(probe.get("duration_s") or 0) - 6.0) <= 0.85
    assert probe.get("audio_codec")


def test_produce_edit_remotion_required_fail_closed_on_invalid_manifest(tmp_path: Path, monkeypatch):
    """REMOTION_REQUIRED: invalid ProjectManifest must FAIL the order — no editorial fallback."""
    evidence = EVIDENCE_ROOT / "order_remotion_negative"
    evidence.mkdir(parents=True, exist_ok=True)
    work = tmp_path / "order_neg"
    work.mkdir()
    artifacts = _order_fixture(work, duration=5.0)
    settings = get_settings()
    executor = _FakeExecutor(settings, work, artifacts)

    editorial_called = {"value": False}

    def boom_editorial(*_a, **_k):
        editorial_called["value"] = True
        raise AssertionError("editorial path must not run under REMOTION_REQUIRED")

    monkeypatch.setattr(
        "atnos.production.editorial_film.render_scene_clip",
        boom_editorial,
    )

    # Force adapter to raise → RemotionRequiredError
    def bad_adapter(**_kwargs):
        raise ValueError("intentional invalid order artifacts")

    monkeypatch.setattr(
        "atnos.production.channels.wealth_blueprint.remotion_order_edit.order_artifacts_to_project_manifest",
        bad_adapter,
    )
    monkeypatch.setattr(
        "atnos.production.channels.wealth_blueprint.remotion_order_edit.scene_plan_to_project_manifest",
        bad_adapter,
    )

    with pytest.raises(RemotionRequiredError) as excinfo:
        asyncio.run(produce_edit(executor))

    assert REMOTION_REQUIRED in str(excinfo.value)
    assert editorial_called["value"] is False
    (evidence / "error.txt").write_text(str(excinfo.value) + "\n", encoding="utf-8")
    (evidence / "preserved_artifacts.json").write_text(
        json.dumps({"calls": executor.calls, "has_edit": "edit" in artifacts}, indent=2) + "\n",
        encoding="utf-8",
    )
    assert "edit" not in artifacts


def test_produce_edit_remotion_path_end_to_end_via_executor(tmp_path: Path):
    """produce_edit_remotion with real Remotion (short fixture, no sound)."""
    work = tmp_path / "order_e2e_branch"
    work.mkdir()
    artifacts = _order_fixture(work, duration=5.0)
    settings = get_settings()
    executor = _FakeExecutor(settings, work, artifacts)
    payload = asyncio.run(produce_edit_remotion(executor))
    assert payload["renderer"] == "remotion"
    assert payload["remotion_mode"] == REMOTION_REQUIRED
    assert payload["renderer_boundary"] == RENDERER_BOUNDARY
    assert payload["editorial_fallback"] is False
    final = Path(payload["output_file"])
    assert final.is_file() and final.stat().st_size > 40_000
    probe = probe_media(final)
    assert probe.get("width") == 1080 and probe.get("height") == 1920
    assert probe.get("video_codec") == "h264"
    assert Path(payload["manifest_path"]).is_file()
