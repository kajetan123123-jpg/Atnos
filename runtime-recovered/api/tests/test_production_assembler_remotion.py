from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
import subprocess

import pytest

from atnos.core.config import Settings
from atnos.production.models import ApprovedTopic, ProductionContext
from atnos.production.project_manifest import file_sha256, load_project_manifest
from atnos.production.steps import assembler


def _context(tmp_path: Path) -> ProductionContext:
    audio_path = tmp_path / "narration.wav"
    raw_video_path = tmp_path / "legacy.mp4"
    audio_path.write_bytes(b"audio-fixture")
    raw_video_path.write_bytes(b"legacy-video-fixture")
    return ProductionContext(
        job_id="prod-remotion-test",
        account_id="youtube:wealth-blueprint",
        topic=ApprovedTopic(
            id="topic-1",
            account_id="youtube:wealth-blueprint",
            title="A source-backed money habit",
            description="",
            total_score=80,
        ),
        artifact_dir=tmp_path,
        artifacts={
            "script": {
                "estimated_duration_s": 8,
                "segments": [
                    {
                        "id": "s01",
                        "type": "hook",
                        "text": "Build a money system.",
                        "duration_target_sec": 8,
                    }
                ],
            },
            "voice": {
                "output_file": str(audio_path),
                "timestamps": [
                    {
                        "segment_id": "s01",
                        "start_sec": 0,
                        "end_sec": 8,
                        "duration_sec": 8,
                        "text": "Build a money system.",
                    }
                ],
            },
            "visuals": {
                "scenes": [
                    {
                        "scene_id": "s01",
                        "duration_s": 8,
                        "headline": "Build a money system",
                        "visual_prompt": "A deterministic finance hook",
                    }
                ]
            },
            "video": {"video_raw_path": str(raw_video_path)},
        },
    )


def test_wealth_blueprint_uses_remotion_then_audio_only_mux(tmp_path: Path, monkeypatch) -> None:
    context = _context(tmp_path)
    settings = Settings(app_env="test", app_root=tmp_path, data_dir=tmp_path / "data")
    remotion_path = tmp_path / "assets" / "renderer_silent.mp4"
    calls: list[str] = []

    monkeypatch.setattr(assembler, "ensure_fonts", lambda: tmp_path / "fonts")
    def fake_render(manifest_path: Path, output_path: Path) -> dict:
        calls.append("remotion")
        assert manifest_path == tmp_path / "project_manifest.json"
        manifest = load_project_manifest(manifest_path)
        assert manifest.channel == "wealth_blueprint"
        assert manifest.renderer_version == assembler.REMOTION_RENDERER_VERSION
        assert output_path == remotion_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"verified-remotion-output")
        output_hash = file_sha256(output_path)
        return {
            "ok": True,
            "renderer_version": assembler.REMOTION_RENDERER_VERSION,
            "manifest_content_hash": manifest.content_hash,
            "output_path": str(output_path),
            "output_sha256": output_hash,
            "probe": {"ok": True},
            "renderer": {
                "renderer_version": assembler.REMOTION_RENDERER_VERSION,
                "manifest_content_hash": manifest.content_hash,
                "output": str(output_path.resolve()),
                "output_sha256": output_hash,
            },
        }

    def fake_mux(**kwargs) -> dict:
        calls.append("mux")
        assert kwargs["raw_video_path"] == remotion_path
        output = Path(kwargs["output_path"])
        output.write_bytes(b"final-video")
        raw = Path(kwargs["raw_video_path"])
        audio = Path(kwargs["audio_path"])
        return {
            "ok": True,
            "probe": {"ok": True},
            "video_mode": "stream_copy",
            "raw_video_sha256": file_sha256(raw),
            "audio_input_sha256": file_sha256(audio),
            "final_output_sha256": file_sha256(output),
            "raw_video_stream_sha256": "a" * 64,
            "final_video_stream_sha256": "a" * 64,
            "video_stream_copy_verified": True,
        }

    monkeypatch.setattr(assembler, "render_project_manifest", fake_render)
    monkeypatch.setattr(assembler, "_mux_remotion_video", fake_mux)
    monkeypatch.setattr(
        assembler,
        "_render_final_video",
        lambda **kwargs: pytest.fail("Wealth Blueprint must not fall back to the legacy renderer"),
    )

    result = asyncio.run(assembler.run(settings, context))

    assert calls == ["remotion", "mux"]
    assert result.payload["status"] == "rendered"
    assert result.payload["renderer_version"] == assembler.REMOTION_RENDERER_VERSION
    assert result.payload["subtitle_mode"] == "remotion_kinetic"
    assert result.payload["raw_video_file"] == str(remotion_path)
    assert result.payload["assembly_provenance"]["verified"] is True
    assert result.payload["ffmpeg_plan"]["strategy"] == "remotion_visual_ffmpeg_audio_mux"


def test_wealth_blueprint_remotion_failure_blocks_without_legacy_fallback(tmp_path: Path, monkeypatch) -> None:
    context = _context(tmp_path)
    settings = Settings(app_env="test", app_root=tmp_path, data_dir=tmp_path / "data")
    monkeypatch.setattr(assembler, "ensure_fonts", lambda: tmp_path / "fonts")
    monkeypatch.setattr(
        assembler,
        "render_project_manifest",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("renderer contract rejected scene")),
    )
    monkeypatch.setattr(
        assembler,
        "_render_final_video",
        lambda **kwargs: pytest.fail("A Remotion error must not activate the legacy fallback"),
    )

    with pytest.raises(RuntimeError, match="renderer contract rejected scene"):
        asyncio.run(assembler.run(settings, context))


def test_non_wealth_channel_keeps_legacy_renderer(tmp_path: Path, monkeypatch) -> None:
    context = _context(tmp_path)
    context.account_id = "youtube:dark-chronicles"
    context.topic = replace(context.topic, account_id="youtube:dark-chronicles")
    context.artifacts["script"]["channel_profile"] = {"key": "dark_chronicles"}
    settings = Settings(app_env="test", app_root=tmp_path, data_dir=tmp_path / "data")
    monkeypatch.setattr(assembler, "ensure_fonts", lambda: tmp_path / "fonts")
    monkeypatch.setattr(
        assembler,
        "render_project_manifest",
        lambda *args, **kwargs: pytest.fail("Non-Wealth channels must not use the V1 renderer"),
    )
    monkeypatch.setattr(
        assembler,
        "_render_final_video",
        lambda **kwargs: {"ok": True, "probe": {"ok": True}},
    )

    result = asyncio.run(assembler.run(settings, context))

    assert result.payload["renderer_version"] == "legacy-ffmpeg-v1"
    assert result.payload["subtitle_mode"] == "ass_utf8_montserrat"
    assert result.payload["remotion_render_path"] is None


def test_wealth_motion_graphic_uses_rendered_video_instead_of_image_manifest(tmp_path: Path, monkeypatch) -> None:
    context = _context(tmp_path)
    context.artifacts["video"]["backend"] = {"mode": "motion_graphic"}
    settings = Settings(app_env="test", app_root=tmp_path, data_dir=tmp_path / "data")
    monkeypatch.setattr(assembler, "ensure_fonts", lambda: tmp_path / "fonts")
    monkeypatch.setattr(
        assembler,
        "render_project_manifest",
        lambda *args, **kwargs: pytest.fail("Motion-graphic clips must not be rebuilt as ImageScenes"),
    )
    monkeypatch.setattr(
        assembler,
        "_render_final_video",
        lambda **kwargs: {"ok": True, "probe": {"ok": True}},
    )

    result = asyncio.run(assembler.run(settings, context))

    assert result.payload["renderer_version"] == "legacy-ffmpeg-v1"
    assert result.payload["raw_video_file"] == context.artifacts["video"]["video_raw_path"]
    assert result.payload["subtitle_mode"] == "ass_utf8_montserrat"


def test_remotion_mux_rejects_output_shorter_than_narration(tmp_path: Path, monkeypatch) -> None:
    raw_video = tmp_path / "silent.mp4"
    audio = tmp_path / "narration.wav"
    output = tmp_path / "final.mp4"
    raw_video.write_bytes(b"video")
    audio.write_bytes(b"audio")

    def fake_run(*args, **kwargs):
        output.write_bytes(b"x" * 5000)
        return subprocess.CompletedProcess(args[0], 0, "", "")

    monkeypatch.setattr(assembler, "_ffmpeg_exe", lambda: "ffmpeg")
    monkeypatch.setattr(assembler, "_probe_duration", lambda path: 10.0)
    monkeypatch.setattr(assembler.subprocess, "run", fake_run)
    monkeypatch.setattr(
        assembler,
        "_probe_final_video",
        lambda path: {
            "ok": True,
            "checks": {"technical": True},
            "duration_s": 8.0,
            "reason": "",
        },
    )

    result = assembler._mux_remotion_video(
        raw_video_path=raw_video,
        audio_path=audio,
        output_path=output,
    )

    assert result["ok"] is False
    assert result["probe"]["checks"]["duration_matches_audio"] is False
    assert not output.exists()
