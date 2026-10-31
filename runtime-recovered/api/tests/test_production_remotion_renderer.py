from __future__ import annotations

import json
import hashlib
from pathlib import Path
import subprocess
import sys

import psutil

import pytest

import atnos.production.remotion_renderer as remotion_renderer


def test_real_node_receipt_source_lists_match_python_verifier():
    import re
    root=Path(__file__).resolve().parents[2]/'remotion-renderer'
    for filename in ('render.ts','render-browser.ts'):
        source=(root/'scripts'/filename).read_text(encoding='utf-8')
        match=re.search(r'const (?:styleFiles|sourceFiles) = \[([\s\S]*?)\];',source)
        assert match,filename
        assert set(re.findall(r"'([^']+)'",match.group(1)))==set(remotion_renderer._RENDERER_SOURCE_FILES),filename


@pytest.mark.parametrize('relative', ['src/EditorialMotionScene.tsx',
    'src/EditorialPlanningScene.tsx', 'src/editorial-motion.ts'])
def test_editorial_scene_change_invalidates_receipt_source_identity(tmp_path,monkeypatch,relative):
    package=tmp_path/'renderer'
    for name in remotion_renderer._RENDERER_SOURCE_FILES:
        path=package/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('original source',encoding='utf-8')
    monkeypatch.setattr(remotion_renderer,'_renderer_package_root',lambda:package)
    before=remotion_renderer._renderer_source_hashes()
    (package/relative).write_text('changed scene implementation',encoding='utf-8')
    after=remotion_renderer._renderer_source_hashes()
    assert relative in before
    assert before[relative]!=after[relative]
    assert {key for key in before if before[key]!=after[key]}=={relative}

from atnos.production.project_manifest import (
    AssetProvenance,
    ProjectManifest,
    QAAction,
    QAResult,
    SceneAsset,
    SceneManifest,
    SceneType,
    TimeRange,
    deterministic_hash,
    file_sha256,
)
from atnos.production.remotion_renderer import (
    REMOTION_RENDERER_VERSION,
    RemotionRendererError,
    _parse_result_marker,
    _resolve_remotion_compositor,
    _resolve_node_and_tsx,
    _renderer_font_assets,
    _renderer_source_hashes,
    _validate_result_marker,
    _validate_renderer_contract,
    probe_silent_render,
    probe_remotion_runtime,
)


def test_resolve_node_and_tsx_uses_native_executables(tmp_path: Path, monkeypatch) -> None:
    package = tmp_path / "renderer"
    tsx = package / "node_modules" / "tsx" / "dist" / "cli.mjs"
    tsx.parent.mkdir(parents=True)
    tsx.write_text("", encoding="utf-8")
    node = tmp_path / "node.exe"
    node.write_bytes(b"node")
    monkeypatch.setenv("ATNOS_NODE", str(node))

    assert _resolve_node_and_tsx(package) == (node.resolve(), tsx.resolve())


def test_renderer_timeout_stops_child_process_tree(tmp_path: Path) -> None:
    """A timed-out Node analogue must not leave an encoder/browser child alive."""
    pid_file = tmp_path / "render-processes.txt"
    parent_script = (
        "import os, pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "pathlib.Path(sys.argv[1]).write_text(f'{os.getpid()}\\n{child.pid}', encoding='utf-8')\n"
        "time.sleep(60)\n"
    )
    parent_pid = child_pid = None
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            remotion_renderer._run_render_command(
                [sys.executable, "-c", parent_script, str(pid_file)],
                cwd=tmp_path,
                env=dict(remotion_renderer.os.environ),
                inactivity_timeout_s=2,
            )
        parent_pid, child_pid = (int(value) for value in pid_file.read_text(encoding="utf-8").splitlines())
        assert not psutil.pid_exists(parent_pid)
        assert not psutil.pid_exists(child_pid)
    finally:
        for pid in (parent_pid, child_pid):
            if pid is not None and psutil.pid_exists(pid):
                psutil.Process(pid).kill()


def test_renderer_progress_extends_runtime_beyond_inactivity_window(tmp_path: Path) -> None:
    script = (
        "import sys, time\n"
        "for frame in range(1, 5):\n"
        " print(f'ATNOS_REMOTION_PROGRESS={frame}/4', file=sys.stderr, flush=True)\n"
        " time.sleep(0.2)\n"
    )
    progress_log = tmp_path / "render-progress.log"
    result = remotion_renderer._run_render_command(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=dict(remotion_renderer.os.environ),
        inactivity_timeout_s=0.35,
        progress_log_path=progress_log,
    )
    assert result.returncode == 0
    assert "ATNOS_REMOTION_PROGRESS=4/4" in result.stderr
    assert progress_log.is_file()
    assert "ATNOS_REMOTION_PROGRESS=1/4" in progress_log.read_text(encoding="utf-8")


def test_resolve_node_and_tsx_rejects_missing_native_node(tmp_path: Path, monkeypatch) -> None:
    package = tmp_path / "renderer"
    tsx = package / "node_modules" / "tsx" / "dist" / "cli.mjs"
    tsx.parent.mkdir(parents=True)
    tsx.write_text("", encoding="utf-8")
    monkeypatch.delenv("ATNOS_NODE", raising=False)
    monkeypatch.setattr(remotion_renderer.shutil, "which", lambda *_args: None)
    monkeypatch.setattr(remotion_renderer.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setattr(remotion_renderer, "_canonical_node_path", lambda: tmp_path / "missing-node.exe")
    monkeypatch.setattr(
        remotion_renderer,
        "_resolve_pnpm",
        lambda: (_ for _ in ()).throw(RemotionRendererError("missing")),
    )

    with pytest.raises(RemotionRendererError, match="Node.js executable is unavailable"):
        _resolve_node_and_tsx(package)


def _fake_remotion_runtime(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    package = tmp_path / "renderer"
    tsx = package / "node_modules" / "tsx" / "dist" / "cli.mjs"
    tsx.parent.mkdir(parents=True)
    tsx.write_text("", encoding="utf-8")
    compositor = (
        package
        / "node_modules"
        / ".pnpm"
        / "@remotion+compositor-win32-x64-msvc@4.0.507"
        / "node_modules"
        / "@remotion"
        / "compositor-win32-x64-msvc"
        / "remotion.exe"
    )
    compositor.parent.mkdir(parents=True)
    compositor.write_bytes(b"binary")
    node = tmp_path / "node.exe"
    node.write_bytes(b"node")
    monkeypatch.setenv("ATNOS_NODE", str(node))
    monkeypatch.setenv("ATNOS_USE_NATIVE_REMOTION", "1")
    return package, compositor


def test_probe_remotion_runtime_skips_native_binary_by_default_on_windows(
    tmp_path: Path, monkeypatch
) -> None:
    package, _compositor = _fake_remotion_runtime(tmp_path, monkeypatch)
    monkeypatch.delenv("ATNOS_USE_NATIVE_REMOTION", raising=False)
    monkeypatch.setattr(remotion_renderer.os, "name", "nt")

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("native compositor must not be executed")

    monkeypatch.setattr(remotion_renderer.subprocess, "run", must_not_run)

    result = probe_remotion_runtime(package)

    assert result["status"] == "browser_compatibility"
    assert result["ready"] is False
    assert result["browser_compatibility_ready"] is True
    assert result["native_probe_skipped"] is True
    assert result["action"] == "none"


def test_probe_remotion_runtime_reports_ready(tmp_path: Path, monkeypatch) -> None:
    package, compositor = _fake_remotion_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(
        remotion_renderer.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "help", ""),
    )

    result = probe_remotion_runtime(package)

    assert result["status"] == "ready"
    assert result["ready"] is True
    assert Path(result["compositor"]) == compositor.resolve()
    assert _resolve_remotion_compositor(package) == compositor.resolve()


def test_probe_remotion_runtime_reports_windows_policy_block(tmp_path: Path, monkeypatch) -> None:
    package, compositor = _fake_remotion_runtime(tmp_path, monkeypatch)

    def blocked(*_args, **_kwargs):
        error = OSError(22, "Application control blocked this file")
        error.winerror = 4551
        raise error

    monkeypatch.setattr(remotion_renderer.subprocess, "run", blocked)

    result = probe_remotion_runtime(package)

    assert result["status"] == "blocked"
    assert result["ready"] is False
    assert result["winerror"] == 4551
    assert result["browser_compatibility_ready"] is True
    assert Path(result["compositor"]) == compositor.resolve()
    assert "Windows Application Control" in result["reason"]


def test_probe_remotion_runtime_timeout_uses_browser_compatibility(
    tmp_path: Path, monkeypatch
) -> None:
    package, compositor = _fake_remotion_runtime(tmp_path, monkeypatch)

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired([str(compositor), "--help"], timeout=10)

    monkeypatch.setattr(remotion_renderer.subprocess, "run", timeout)

    result = probe_remotion_runtime(package)

    assert result["status"] == "degraded"
    assert result["ready"] is False
    assert result["browser_compatibility_ready"] is True
    assert result["action"] == "Use the signed-browser compatibility renderer"


def _manifest(scene: SceneManifest) -> ProjectManifest:
    return ProjectManifest(
        manifest_id="manifest-job-1",
        job_id="job-1",
        project_id="wealth-blueprint",
        account_id="wealth-blueprint",
        topic_id="topic-1",
        topic_title="Compound interest",
        channel="wealth_blueprint",
        language_code="en",
        brand_profile_version="brand-v1-test",
        renderer_version=REMOTION_RENDERER_VERSION,
        workflow_version="workflow-v1",
        voice_version="preview",
        status="running",
        current_step="renderer",
        scenes=[scene],
        created_at="2026-08-10T00:00:00+00:00",
        updated_at="2026-08-10T00:00:00+00:00",
    )


def _scene(scene_type: SceneType = SceneType.HOOK) -> SceneManifest:
    return SceneManifest(
        scene_id="s01",
        scene_type=scene_type,
        timing=TimeRange(start_sec=0, end_sec=2),
        narration="Start with a plan.",
        caption="Start with a plan.",
        headline="Start with a plan",
        input_hash=deterministic_hash({"scene": 1}),
    )


def test_renderer_contract_accepts_valid_hook_scene() -> None:
    _validate_renderer_contract(_manifest(_scene()))


def test_renderer_contract_rejects_number_without_source_value() -> None:
    with pytest.raises(RemotionRendererError, match="source-backed number_value"):
        _validate_renderer_contract(_manifest(_scene(SceneType.NUMBER)))


def test_renderer_contract_rejects_chart_without_two_values() -> None:
    scene = _scene(SceneType.CHART).model_copy(update={"chart_values": [10.0]})
    with pytest.raises(RemotionRendererError, match="at least two"):
        _validate_renderer_contract(_manifest(scene))


def test_renderer_contract_requires_comparison_labels() -> None:
    incomplete = _scene(SceneType.CHART).model_copy(update={"comparison_left": "CONSISTENT CONTRIBUTIONS"})
    with pytest.raises(RemotionRendererError, match="two explicit labels"):
        _validate_renderer_contract(_manifest(incomplete))

    scene = _scene(SceneType.CHART).model_copy(
        update={
            "comparison_left": "CONSISTENT CONTRIBUTIONS",
            "comparison_right": "FINANCIAL HYPE",
            "chart_values": [10.0, 20.0],
        }
    )
    _validate_renderer_contract(_manifest(scene))


def test_renderer_contract_requires_real_image_asset(tmp_path: Path) -> None:
    missing = tmp_path / "missing.png"
    scene = _scene(SceneType.IMAGE).model_copy(
        update={
            "assets": [
                SceneAsset(
                    asset_id="s01-candidate-1",
                    role="image_candidate",
                    path=str(missing),
                    exists=True,
                    provenance=AssetProvenance(
                        provider="test",
                        input_hash=deterministic_hash({"asset": 1}),
                    ),
                )
            ]
        }
    )
    with pytest.raises(RemotionRendererError, match="exactly one QA-passed selected image"):
        _validate_renderer_contract(_manifest(scene))


def test_renderer_contract_rejects_existing_candidate_without_selection(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.png"
    candidate.write_bytes(b"candidate")
    scene = _scene(SceneType.IMAGE).model_copy(
        update={
            "assets": [
                SceneAsset(
                    asset_id="s01-candidate-1",
                    role="image_candidate",
                    path=str(candidate),
                    exists=True,
                    provenance=AssetProvenance(
                        provider="test",
                        input_hash=deterministic_hash({"asset": 1}),
                        artifact_hash=file_sha256(candidate),
                    ),
                    qa=QAResult(status="passed", action=QAAction.PASS, score=0.9),
                )
            ]
        }
    )

    with pytest.raises(RemotionRendererError, match="exactly one QA-passed selected image"):
        _validate_renderer_contract(_manifest(scene))


def test_renderer_contract_accepts_one_qa_passed_selected_image(tmp_path: Path) -> None:
    selected = tmp_path / "selected.png"
    selected.write_bytes(b"selected")
    scene = _scene(SceneType.IMAGE).model_copy(
        update={
            "assets": [
                SceneAsset(
                    asset_id="s01-selected",
                    role="selected_image",
                    path=str(selected),
                    exists=True,
                    provenance=AssetProvenance(
                        provider="test",
                        input_hash=deterministic_hash({"asset": 1}),
                        artifact_hash=file_sha256(selected),
                    ),
                    qa=QAResult(status="passed", action=QAAction.PASS, score=0.9),
                )
            ]
        }
    )

    _validate_renderer_contract(_manifest(scene))


def test_renderer_contract_rejects_selected_image_changed_after_selection(tmp_path: Path) -> None:
    selected = tmp_path / "selected.png"
    selected.write_bytes(b"selected")
    selected_hash = file_sha256(selected)
    scene = _scene(SceneType.IMAGE).model_copy(
        update={
            "assets": [
                SceneAsset(
                    asset_id="s01-selected",
                    role="selected_image",
                    path=str(selected),
                    exists=True,
                    provenance=AssetProvenance(
                        provider="test",
                        input_hash=deterministic_hash({"asset": 1}),
                        artifact_hash=selected_hash,
                    ),
                    qa=QAResult(status="passed", action=QAAction.PASS, score=0.9),
                )
            ]
        }
    )
    selected.write_bytes(b"mutated-after-selection")

    with pytest.raises(RemotionRendererError, match="selected image hash mismatch"):
        _validate_renderer_contract(_manifest(scene))


def test_renderer_result_marker_is_strict() -> None:
    marker = {
        "ok": True,
        "renderer_version": REMOTION_RENDERER_VERSION,
        "output": "output.mp4",
        "bytes": 5000,
        "fps": 30,
        "width": 1080,
        "height": 1920,
        "duration_in_frames": 60,
        "manifest_content_hash": "a" * 64,
        "output_sha256": "b" * 64,
        "renderer_source_hashes": {"src/index.ts": "c" * 64},
        "renderer_source_fingerprint": "d" * 64,
        "font_assets": {"regular": {"sha256": "e" * 64}},
    }
    parsed = _parse_result_marker(f"noise\nATNOS_REMOTION_RESULT={json.dumps(marker)}\n")
    assert parsed == marker
    with pytest.raises(RemotionRendererError, match="did not emit"):
        _parse_result_marker("ordinary output")
    with pytest.raises(RemotionRendererError, match="missing required fields"):
        _parse_result_marker('ATNOS_REMOTION_RESULT={"ok": true}')


def test_renderer_result_marker_must_match_output_and_manifest(tmp_path: Path) -> None:
    output = tmp_path / "output.mp4"
    output.write_bytes(b"verified-output")
    manifest = _manifest(_scene())
    manifest = manifest.model_copy(update={"content_hash": "a" * 64})
    marker = {
        "ok": True,
        "renderer_version": REMOTION_RENDERER_VERSION,
        "output": str(output),
        "bytes": output.stat().st_size,
        "fps": 30,
        "width": 1080,
        "height": 1920,
        "duration_in_frames": 60,
        "manifest_content_hash": manifest.content_hash,
        "output_sha256": file_sha256(output),
        **_valid_renderer_integrity(),
    }

    _validate_result_marker(marker, output, manifest)
    marker["output_sha256"] = "f" * 64
    with pytest.raises(RemotionRendererError, match="output_sha256"):
        _validate_result_marker(marker, output, manifest)
    marker["output_sha256"] = file_sha256(output)
    marker["renderer_source_hashes"] = {"src/index.ts": "c" * 64}
    with pytest.raises(RemotionRendererError, match="renderer_source_hashes"):
        _validate_result_marker(marker, output, manifest)


def test_renderer_result_marker_rejects_wrong_duration(tmp_path: Path) -> None:
    output = tmp_path / "output.mp4"
    output.write_bytes(b"verified-output")
    manifest = _manifest(_scene())
    marker = {
        "ok": True,
        "renderer_version": REMOTION_RENDERER_VERSION,
        "output": str(output),
        "bytes": output.stat().st_size,
        "fps": 30,
        "width": 1080,
        "height": 1920,
        "duration_in_frames": 61,
        "manifest_content_hash": manifest.content_hash,
        "output_sha256": file_sha256(output),
        **_valid_renderer_integrity(),
    }

    with pytest.raises(RemotionRendererError, match="duration_in_frames"):
        _validate_result_marker(marker, output, manifest)


def _valid_renderer_integrity() -> dict:
    source_hashes = _renderer_source_hashes()
    source_fingerprint = hashlib.sha256(
        json.dumps(source_hashes, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "renderer_source_hashes": source_hashes,
        "renderer_source_fingerprint": source_fingerprint,
        "font_assets": _renderer_font_assets(),
    }


def test_visual_probe_allows_audio_only_when_requested(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "final.mp4"
    output.write_bytes(b"final-video")
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "pix_fmt": "yuv420p",
                "width": 1080,
                "height": 1920,
                "avg_frame_rate": "30/1",
            },
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"duration": "45.0"},
    }

    class _Result:
        returncode = 0
        stdout = json.dumps(payload)
        stderr = ""

    monkeypatch.setattr("atnos.production.remotion_renderer.resolve_ffprobe", lambda *_args: "ffprobe")
    monkeypatch.setattr("atnos.production.remotion_renderer.subprocess.run", lambda *_args, **_kwargs: _Result())

    assert probe_silent_render(output)["ok"] is False
    final_probe = probe_silent_render(output, require_silent=False)
    assert final_probe["ok"] is True
    assert final_probe["has_audio"] is True


def test_visual_probe_returns_failure_when_ffprobe_times_out(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "timed-out.mp4"
    output.write_bytes(b"video")
    monkeypatch.setattr("atnos.production.remotion_renderer.resolve_ffprobe", lambda *_args: "ffprobe")

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=30)

    monkeypatch.setattr("atnos.production.remotion_renderer.subprocess.run", timeout)

    result = probe_silent_render(output)

    assert result["ok"] is False
    assert "timed out" in result["reason"].lower()
