from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from atnos.core.config import get_settings, load_runtime_env_files
from atnos.production.media_tools import resolve_ffmpeg, resolve_ffprobe
from atnos.production.project_manifest import deterministic_hash, file_sha256
from atnos.production.scene_checkpoint import SceneCheckpointStore
from atnos.production.steps.video_generator import _is_probeable_video


def _input_hash(scene_id: str) -> str:
    return deterministic_hash(
        {
            "contract": "wealth-blueprint-v1-resume-recovery",
            "scene_id": scene_id,
            "renderer_version": "video-generator-v1",
        }
    )


def _render_probeable_clip(path: Path, *, index: int) -> dict[str, Any]:
    ffmpeg = resolve_ffmpeg()
    ffprobe = resolve_ffprobe()
    if not ffmpeg or not ffprobe:
        raise RuntimeError("FFmpeg and ffprobe are required for the resume recovery verification.")
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size=320x568:rate=30:duration={1.0 + index * 0.03:.2f}",
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-y",
        str(path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    if completed.returncode != 0 or not _is_probeable_video(path):
        raise RuntimeError(f"Could not create a probeable scene clip: {completed.stderr[-1000:]}")
    probe = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,r_frame_rate",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if probe.returncode != 0:
        raise RuntimeError(f"ffprobe rejected {path}: {probe.stderr[-1000:]}")
    return json.loads(probe.stdout)


def _complete_scene(store: SceneCheckpointStore, root: Path, scene_id: str, *, index: int) -> dict[str, Any]:
    artifact = root / "clips" / f"{scene_id}.mp4"
    probe = _render_probeable_clip(artifact, index=index)
    result = {
        "scene_id": scene_id,
        "file": str(artifact),
        "status": "generated",
        "ffprobe": probe,
    }
    store.complete(
        scene_id,
        input_hash=_input_hash(scene_id),
        artifact_path=artifact,
        result=result,
        artifact_validator=_is_probeable_video,
    )
    return result


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(descriptor)
    temporary_path = Path(temporary)
    try:
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _run_crash_worker(output_dir: Path) -> None:
    """Renderuje trzy sceny i kończy proces awaryjnie po rozpoczęciu czwartej."""
    scene_ids = [f"scene_{index:02d}" for index in range(1, 6)]
    store = SceneCheckpointStore(
        output_dir / "scene_checkpoint.json",
        job_id="wealth-v1-resume-evidence",
        scene_ids=scene_ids,
    )
    for index, scene_id in enumerate(scene_ids[:4], start=1):
        cached = store.begin(
            scene_id,
            input_hash=_input_hash(scene_id),
            artifact_validator=_is_probeable_video,
        )
        if cached is not None:
            raise RuntimeError(f"Unexpected cache hit during crash worker run: {scene_id}")
        if scene_id == "scene_04":
            _atomic_write_json(
                output_dir / "crash_worker_marker.json",
                {
                    "scene_id": scene_id,
                    "pid": os.getpid(),
                    "created_at": datetime.now(UTC).isoformat(),
                },
            )
            os._exit(86)
        _complete_scene(store, output_dir, scene_id, index=index)
    raise RuntimeError("Crash worker did not terminate at scene_04.")


def _launch_crash_worker(output_dir: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    source_root = str(Path(__file__).resolve().parents[1] / "src")
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(part for part in (source_root, existing_pythonpath) if part)
    return subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--crash-worker", str(output_dir)],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
        env=env,
    )


def verify_resume_recovery(output_dir: Path) -> dict[str, Any]:
    scene_ids = [f"scene_{index:02d}" for index in range(1, 6)]
    checkpoint_path = output_dir / "scene_checkpoint.json"
    crash_worker = _launch_crash_worker(output_dir)
    crash_marker_path = output_dir / "crash_worker_marker.json"
    if crash_worker.returncode != 86 or not crash_marker_path.exists():
        raise RuntimeError(
            "Crash worker did not produce the expected process failure evidence: "
            f"returncode={crash_worker.returncode}, stderr={crash_worker.stderr[-1000:]}"
        )
    initial_generation_calls = scene_ids[:4]

    before = {
        scene_id: {
            "sha256": file_sha256(output_dir / "clips" / f"{scene_id}.mp4"),
            "mtime_ns": (output_dir / "clips" / f"{scene_id}.mp4").stat().st_mtime_ns,
        }
        for scene_id in scene_ids[:3]
    }

    restarted_store = SceneCheckpointStore(
        checkpoint_path,
        job_id="wealth-v1-resume-evidence",
        scene_ids=scene_ids,
    )
    restart_generation_calls: list[str] = []
    cached_scenes: list[str] = []
    for index, scene_id in enumerate(scene_ids, start=1):
        cached = restarted_store.begin(
            scene_id,
            input_hash=_input_hash(scene_id),
            artifact_validator=_is_probeable_video,
        )
        if cached is not None:
            cached_scenes.append(scene_id)
            continue
        restart_generation_calls.append(scene_id)
        _complete_scene(restarted_store, output_dir, scene_id, index=index)

    after = {
        scene_id: {
            "sha256": file_sha256(output_dir / "clips" / f"{scene_id}.mp4"),
            "mtime_ns": (output_dir / "clips" / f"{scene_id}.mp4").stat().st_mtime_ns,
        }
        for scene_id in scene_ids[:3]
    }
    states = {scene.scene_id: scene.model_dump(mode="json") for scene in restarted_store.manifest.scenes}

    tampered_path = output_dir / "tampered_scene_checkpoint.json"
    tampered_path.write_text(checkpoint_path.read_text(encoding="utf-8"), encoding="utf-8")
    tampered_path.write_text(
        tampered_path.read_text(encoding="utf-8").replace('"DONE"', '"FAILED"', 1),
        encoding="utf-8",
    )
    tamper_rejected = False
    tamper_error = ""
    try:
        SceneCheckpointStore(
            tampered_path,
            job_id="wealth-v1-resume-evidence",
            scene_ids=scene_ids,
        )
    except ValueError as exc:
        tamper_rejected = "hash mismatch" in str(exc)
        tamper_error = str(exc)

    checks = {
        "worker_process_crashed_at_scene_04": crash_worker.returncode == 86 and crash_marker_path.exists(),
        "initial_stopped_on_scene_04": initial_generation_calls == scene_ids[:4],
        "scenes_01_03_cached_after_restart": cached_scenes == scene_ids[:3],
        "scenes_01_03_unchanged": before == after,
        "scene_04_retried_scene_05_continued": restart_generation_calls == scene_ids[3:],
        "scene_04_attempt_is_two": states["scene_04"]["attempt"] == 2,
        "scene_05_attempt_is_one": states["scene_05"]["attempt"] == 1,
        "all_scene_artifacts_probeable": all(
            _is_probeable_video(output_dir / "clips" / f"{scene_id}.mp4") for scene_id in scene_ids
        ),
        "tampered_checkpoint_rejected": tamper_rejected,
    }
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "verification": "wealth_blueprint_v1_resume_recovery",
        "created_at": datetime.now(UTC).isoformat(),
        "status": "verified" if all(checks.values()) else "failed",
        "checks": checks,
        "initial_generation_calls": initial_generation_calls,
        "crash_worker": {
            "returncode": crash_worker.returncode,
            "marker_path": str(crash_marker_path),
            "marker": json.loads(crash_marker_path.read_text(encoding="utf-8")),
            "stderr": crash_worker.stderr[-1000:],
        },
        "cached_after_restart": cached_scenes,
        "restart_generation_calls": restart_generation_calls,
        "completed_scene_evidence_before_restart": before,
        "completed_scene_evidence_after_restart": after,
        "scene_states": states,
        "checkpoint_path": str(checkpoint_path),
        "checkpoint_sha256": file_sha256(checkpoint_path),
        "tamper_error": tamper_error,
    }
    report["report_hash"] = deterministic_hash(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify durable Wealth Blueprint scene recovery using real MP4 files.")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--crash-worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    load_runtime_env_files()
    if args.crash_worker is not None:
        _run_crash_worker(args.crash_worker)
        return 99
    settings = get_settings()
    output_dir = args.output_dir or (
        Path(settings.data_dir)
        / "production"
        / "evidence"
        / f"wealth_v1_resume_recovery_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    report = verify_resume_recovery(output_dir)
    report_path = output_dir / "resume_recovery_report.json"
    _atomic_write_json(report_path, report)
    latest_path = Path(settings.data_dir) / "production" / "evidence" / "wealth_v1_resume_recovery_latest.json"
    _atomic_write_json(latest_path, report)
    print(json.dumps({**report, "report_path": str(report_path), "latest_path": str(latest_path)}, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
