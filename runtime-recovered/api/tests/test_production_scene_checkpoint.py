from __future__ import annotations

from pathlib import Path

import pytest

from atnos.production.project_manifest import deterministic_hash, file_sha256
from atnos.production.scene_checkpoint import (
    SceneCheckpointStore,
    mark_scene_checkpoint_interrupted,
    scene_generation_input_hash,
)


def _valid_artifact(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _input_hash(scene_id: str) -> str:
    return deterministic_hash({"scene_id": scene_id, "prompt": f"prompt-{scene_id}", "seed": 42})


def _complete_scene(store: SceneCheckpointStore, root: Path, scene_id: str, payload: bytes) -> None:
    artifact = root / f"{scene_id}.mp4"
    artifact.write_bytes(payload)
    store.complete(
        scene_id,
        input_hash=_input_hash(scene_id),
        artifact_path=artifact,
        result={"scene_id": scene_id, "file": str(artifact), "status": "generated"},
        artifact_validator=_valid_artifact,
    )


def test_scene_04_crash_resumes_without_regenerating_01_03(tmp_path: Path) -> None:
    scene_ids = [f"scene_{index:02d}" for index in range(1, 6)]
    checkpoint_path = tmp_path / "scene_checkpoint.json"
    first_store = SceneCheckpointStore(checkpoint_path, job_id="prod-resume", scene_ids=scene_ids)
    first_calls: list[str] = []

    for scene_id in scene_ids:
        cached = first_store.begin(
            scene_id,
            input_hash=_input_hash(scene_id),
            artifact_validator=_valid_artifact,
        )
        assert cached is None
        first_calls.append(scene_id)
        if scene_id == "scene_04":
            break  # Symulowany hard crash: scena pozostaje RUNNING.
        _complete_scene(first_store, tmp_path, scene_id, scene_id.encode("ascii"))

    assert first_calls == ["scene_01", "scene_02", "scene_03", "scene_04"]
    before = {
        scene_id: (
            file_sha256(tmp_path / f"{scene_id}.mp4"),
            (tmp_path / f"{scene_id}.mp4").stat().st_mtime_ns,
        )
        for scene_id in scene_ids[:3]
    }

    restarted_store = SceneCheckpointStore(checkpoint_path, job_id="prod-resume", scene_ids=scene_ids)
    restart_generation_calls: list[str] = []
    for scene_id in scene_ids:
        cached = restarted_store.begin(
            scene_id,
            input_hash=_input_hash(scene_id),
            artifact_validator=_valid_artifact,
        )
        if cached is not None:
            continue
        restart_generation_calls.append(scene_id)
        _complete_scene(restarted_store, tmp_path, scene_id, f"retry-{scene_id}".encode("ascii"))

    assert restart_generation_calls == ["scene_04", "scene_05"]
    after = {
        scene_id: (
            file_sha256(tmp_path / f"{scene_id}.mp4"),
            (tmp_path / f"{scene_id}.mp4").stat().st_mtime_ns,
        )
        for scene_id in scene_ids[:3]
    }
    assert after == before
    states = {scene.scene_id: scene for scene in restarted_store.manifest.scenes}
    assert [states[scene_id].status for scene_id in scene_ids] == ["CACHED", "CACHED", "CACHED", "DONE", "DONE"]
    assert states["scene_04"].attempt == 2
    assert states["scene_05"].attempt == 1


def test_resume_rejects_tampered_completed_scene(tmp_path: Path) -> None:
    scene_ids = ["scene_01", "scene_02", "scene_03"]
    path = tmp_path / "checkpoint.json"
    store = SceneCheckpointStore(path, job_id="prod-tamper", scene_ids=scene_ids)
    for scene_id in scene_ids:
        assert store.begin(scene_id, input_hash=_input_hash(scene_id), artifact_validator=_valid_artifact) is None
        _complete_scene(store, tmp_path, scene_id, scene_id.encode("ascii"))

    (tmp_path / "scene_02.mp4").write_bytes(b"tampered")
    restarted = SceneCheckpointStore(path, job_id="prod-tamper", scene_ids=scene_ids)

    assert restarted.begin("scene_01", input_hash=_input_hash("scene_01"), artifact_validator=_valid_artifact)
    assert restarted.begin("scene_02", input_hash=_input_hash("scene_02"), artifact_validator=_valid_artifact) is None
    assert restarted.manifest.scenes[1].attempt == 2


def test_reusable_result_does_not_mutate_checkpoint_state(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    store = SceneCheckpointStore(path, job_id="prod-peek", scene_ids=["scene_01"])
    assert store.begin("scene_01", input_hash=_input_hash("scene_01"), artifact_validator=_valid_artifact) is None
    _complete_scene(store, tmp_path, "scene_01", b"durable")

    before = store.manifest.scenes[0]
    reusable = store.reusable_result(
        "scene_01",
        input_hash=_input_hash("scene_01"),
        artifact_validator=_valid_artifact,
    )
    after = store.manifest.scenes[0]

    assert reusable == {"scene_id": "scene_01", "file": str(tmp_path / "scene_01.mp4"), "status": "generated"}
    assert before.status == after.status == "DONE"
    assert before.attempt == after.attempt == 1


def test_regular_video_error_is_persisted_on_running_scene(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    store = SceneCheckpointStore(path, job_id="prod-error", scene_ids=["scene_01"])
    store.begin("scene_01", input_hash=_input_hash("scene_01"))

    failed = mark_scene_checkpoint_interrupted(path, "ComfyUI process exited with code 1")

    assert failed == ["scene_01"]
    reloaded = SceneCheckpointStore(path, job_id="prod-error", scene_ids=["scene_01"])
    state = reloaded.manifest.scenes[0]
    assert state.status == "FAILED"
    assert state.error == "ComfyUI process exited with code 1"


def test_checkpoint_rejects_wrong_job_and_tampering(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    SceneCheckpointStore(path, job_id="prod-one", scene_ids=["scene_01"])

    with pytest.raises(ValueError, match="belongs to"):
        SceneCheckpointStore(path, job_id="prod-two", scene_ids=["scene_01"])

    text = path.read_text(encoding="utf-8").replace('"WAITING"', '"FAILED"')
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        SceneCheckpointStore(path, job_id="prod-one", scene_ids=["scene_01"])


def test_scene_input_hash_changes_with_renderer_contract_version() -> None:
    kwargs = {
        "segment": 1,
        "total": 1,
        "backend": {"mode": "comfy_image_motion", "checkpoint": "model.safetensors"},
        "workflow_name": "default.json",
        "channel_profile": "wealth_blueprint",
    }

    first = scene_generation_input_hash({"headline": "SAVE FIRST"}, renderer_version="renderer-v1", **kwargs)
    second = scene_generation_input_hash({"headline": "SAVE FIRST"}, renderer_version="renderer-v2", **kwargs)

    assert first != second
