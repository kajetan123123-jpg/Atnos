"""Production-hardening tests for Wealth Blueprint Video Engine health + BLOCKED_DEPENDENCY."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from atnos.production.channels.wealth_blueprint.health import (
    check_ffmpeg,
    check_whisper,
    run_wealth_blueprint_video_engine_health,
)
from atnos.production.channels.wealth_blueprint.video_job import BlockedDependencyError, WealthBlueprintVideoJob
from atnos.production.channels.wealth_blueprint.video_job_models import JobState, StageRecord
from atnos.production.channels.wealth_blueprint.video_qc import run_wealth_blueprint_qc


def test_blocked_dependency_error_message_is_explicit() -> None:
    err = BlockedDependencyError("tts", "F5 unreachable")
    assert "BLOCKED_DEPENDENCY:tts" in str(err)
    assert err.dependency == "tts"


def test_job_state_includes_blocked_status(tmp_path: Path) -> None:
    state = JobState(
        job_id="wb-test",
        status="BLOCKED_DEPENDENCY",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        artifact_dir=str(tmp_path),
        stages={"AUDIO": StageRecord(name="AUDIO", status="blocked", blocked_dependency="tts")},
        blocked_dependencies=["tts"],
    )
    payload = state.model_dump()
    assert payload["status"] == "BLOCKED_DEPENDENCY"
    assert payload["blocked_dependencies"] == ["tts"]
    restored = JobState.model_validate(payload)
    assert restored.stages["AUDIO"].blocked_dependency == "tts"


def test_check_ffmpeg_reports_structured_status() -> None:
    result = check_ffmpeg()
    assert result["name"] == "ffmpeg"
    assert result["status"] in {"ok", "BLOCKED_DEPENDENCY"}
    assert "detail" in result


def test_check_whisper_blocked_without_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SimpleNamespace(whisper_cpp_exe=None, whisper_model_path=None)
    monkeypatch.setattr(
        "atnos.production.channels.wealth_blueprint.health.shutil.which",
        lambda *_args, **_kwargs: None,
    )
    result = check_whisper(settings)  # type: ignore[arg-type]
    assert result["status"] == "BLOCKED_DEPENDENCY"


def test_health_matrix_does_not_require_comfy_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SimpleNamespace(
        ffmpeg_exe=None,
        remotion_browser_executable=None,
        atnos_node_exe=None,
        remotion_renderer_root=None,
        comfyui_base_url="http://127.0.0.1:9",
        comfyui_health_timeout_s=0.2,
        f5_tts_base_url="http://127.0.0.1:9/api/predict",
        whisper_cpp_exe=None,
        whisper_model_path=None,
        wealth_blueprint_require_whisper_alignment=False,
        wealth_blueprint_require_comfy_for_missing_assets=True,
        atnos_tools_dir=None,
        wealth_blueprint_channel_references_dir=None,
    )
    monkeypatch.setattr(
        "atnos.production.channels.wealth_blueprint.health.check_ffmpeg",
        lambda *_a, **_k: {"name": "ffmpeg", "status": "ok", "detail": "ok", "path_or_url": "ffmpeg"},
    )
    monkeypatch.setattr(
        "atnos.production.channels.wealth_blueprint.health.check_remotion",
        lambda *_a, **_k: {"name": "remotion", "status": "ok", "detail": "ok", "path_or_url": None},
    )
    monkeypatch.setattr(
        "atnos.production.channels.wealth_blueprint.health.check_tts",
        lambda *_a, **_k: {"name": "tts", "status": "ok", "detail": "ok", "path_or_url": "http://x"},
    )
    monkeypatch.setattr(
        "atnos.production.channels.wealth_blueprint.health.check_whisper",
        lambda *_a, **_k: {"name": "whisper", "status": "BLOCKED_DEPENDENCY", "detail": "missing", "path_or_url": None},
    )
    monkeypatch.setattr(
        "atnos.production.channels.wealth_blueprint.health.check_comfyui",
        lambda *_a, **_k: {"name": "comfyui", "status": "BLOCKED_DEPENDENCY", "detail": "down", "path_or_url": None},
    )
    report = run_wealth_blueprint_video_engine_health(settings)  # type: ignore[arg-type]
    assert report["status"] == "degraded"
    assert "comfyui" not in report["blocked_dependencies"]


def test_qc_looks_for_stages_render_preview_path(tmp_path: Path) -> None:
    stages = tmp_path / "stages" / "render_preview"
    stages.mkdir(parents=True)
    (stages / "remotion_result.json").write_text('{"ok": true, "status": "rendered"}\n', encoding="utf-8")
    video = tmp_path / "preview.mp4"
    video.write_bytes(b"\x00" * 5000)
    audio = tmp_path / "narration.wav"
    audio.write_bytes(b"RIFF" + b"\x00" * 100)
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"scenes": []}\n', encoding="utf-8")
    report = run_wealth_blueprint_qc(
        artifact_dir=tmp_path,
        video_path=video,
        audio_path=audio,
        manifest_path=manifest,
    )
    frame_check = next(c for c in report["checks"] if c["name"] == "frame_render_errors")
    assert frame_check["status"] == "pass"
    safe = next(c for c in report["checks"] if c["name"] == "elements_within_safe_zone")
    assert safe["status"] == "warn"


def test_resolve_selected_still_blocks_without_refs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SimpleNamespace(
        data_dir=tmp_path,
        wealth_blueprint_channel_references_dir=tmp_path / "empty_refs",
        app_root=tmp_path / "no_app",
        wealth_blueprint_require_whisper_alignment=False,
        wealth_blueprint_require_comfy_for_missing_assets=True,
        ffmpeg_exe=None,
        atnos_node_exe=None,
        remotion_browser_executable=None,
        atnos_tools_dir=None,
    )
    (tmp_path / "empty_refs").mkdir()
    job = WealthBlueprintVideoJob(settings, job_id="wb-still-test", artifact_root=tmp_path / "job")  # type: ignore[arg-type]
    with pytest.raises(BlockedDependencyError) as exc:
        job._resolve_selected_still()
    assert exc.value.dependency == "assets"
