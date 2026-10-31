from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from atnos.production.project_manifest import deterministic_hash, file_sha256


CHECKPOINT_SCHEMA_VERSION = "1.0"


class SceneCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: str
    status: Literal["WAITING", "RUNNING", "DONE", "CACHED", "FAILED"] = "WAITING"
    attempt: int = Field(default=0, ge=0)
    input_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    artifact_path: str | None = None
    artifact_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class SceneCheckpointManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = CHECKPOINT_SCHEMA_VERSION
    job_id: str
    scenes: list[SceneCheckpoint]
    updated_at: str
    checkpoint_hash: str = Field(default="", pattern=r"^(?:|[0-9a-f]{64})$")

    @model_validator(mode="after")
    def validate_unique_scene_ids(self) -> "SceneCheckpointManifest":
        scene_ids = [scene.scene_id for scene in self.scenes]
        if len(scene_ids) != len(set(scene_ids)):
            raise ValueError("scene checkpoint IDs must be unique")
        return self


class SceneCheckpointStore:
    """Atomowy checkpoint scen pozwalający wznowić ten sam krok wideo po awarii."""

    def __init__(self, path: Path, *, job_id: str, scene_ids: list[str]) -> None:
        self.path = path
        self.job_id = job_id
        if path.is_file():
            self._manifest = self._load()
            if self._manifest.job_id != job_id:
                raise ValueError(f"Scene checkpoint belongs to {self._manifest.job_id}, not {job_id}.")
            existing = {scene.scene_id for scene in self._manifest.scenes}
            for scene_id in scene_ids:
                if scene_id not in existing:
                    self._manifest.scenes.append(SceneCheckpoint(scene_id=scene_id))
            self._save()
        else:
            self._manifest = SceneCheckpointManifest(
                job_id=job_id,
                scenes=[SceneCheckpoint(scene_id=scene_id) for scene_id in scene_ids],
                updated_at=_now(),
            )
            self._save()

    @property
    def manifest(self) -> SceneCheckpointManifest:
        return self._manifest.model_copy(deep=True)

    def begin(
        self,
        scene_id: str,
        *,
        input_hash: str,
        artifact_validator: Callable[[Path], bool] | None = None,
    ) -> dict[str, Any] | None:
        scene = self._scene(scene_id)
        reusable_result = self.reusable_result(
            scene_id,
            input_hash=input_hash,
            artifact_validator=artifact_validator,
        )
        if reusable_result is not None:
            scene.status = "CACHED"
            scene.error = None
            self._save()
            return reusable_result

        if scene.status == "RUNNING":
            scene.error = "Interrupted before scene completion; retrying from the last durable checkpoint."
        scene.status = "RUNNING"
        scene.attempt += 1
        scene.input_hash = input_hash
        scene.artifact_path = None
        scene.artifact_hash = None
        scene.result = {}
        self._save()
        return None

    def reusable_result(
        self,
        scene_id: str,
        *,
        input_hash: str,
        artifact_validator: Callable[[Path], bool] | None = None,
    ) -> dict[str, Any] | None:
        """Zwraca trwały wynik bez zmiany statusu ani licznika prób sceny."""
        scene = self._scene(scene_id)
        path = Path(scene.artifact_path) if scene.artifact_path else None
        reusable = (
            scene.status in {"DONE", "CACHED"}
            and scene.input_hash == input_hash
            and path is not None
            and path.is_file()
            and scene.artifact_hash == file_sha256(path)
            and (artifact_validator is None or artifact_validator(path))
        )
        if reusable:
            return dict(scene.result)
        return None

    def complete(
        self,
        scene_id: str,
        *,
        input_hash: str,
        artifact_path: Path,
        result: dict[str, Any],
        artifact_validator: Callable[[Path], bool] | None = None,
    ) -> SceneCheckpoint:
        scene = self._scene(scene_id)
        if scene.status != "RUNNING" or scene.input_hash != input_hash:
            raise RuntimeError(f"Scene {scene_id} was not started with the current input hash.")
        if not artifact_path.is_file():
            raise FileNotFoundError(artifact_path)
        if artifact_validator is not None and not artifact_validator(artifact_path):
            raise ValueError(f"Scene artifact did not pass validation: {artifact_path}")
        artifact_hash = file_sha256(artifact_path)
        if artifact_hash is None:
            raise ValueError(f"Scene artifact has no SHA-256: {artifact_path}")
        scene.status = "DONE"
        scene.artifact_path = str(artifact_path)
        scene.artifact_hash = artifact_hash
        scene.result = dict(result)
        scene.error = None
        self._save()
        return scene.model_copy(deep=True)

    def fail(self, scene_id: str, error: str) -> SceneCheckpoint:
        scene = self._scene(scene_id)
        scene.status = "FAILED"
        scene.error = str(error)[:2000]
        self._save()
        return scene.model_copy(deep=True)

    def fail_running(self, error: str) -> list[str]:
        failed: list[str] = []
        for scene in self._manifest.scenes:
            if scene.status == "RUNNING":
                scene.status = "FAILED"
                scene.error = str(error)[:2000]
                failed.append(scene.scene_id)
        if failed:
            self._save()
        return failed

    def _scene(self, scene_id: str) -> SceneCheckpoint:
        for scene in self._manifest.scenes:
            if scene.scene_id == scene_id:
                return scene
        raise KeyError(f"Unknown scene checkpoint: {scene_id}")

    def _load(self) -> SceneCheckpointManifest:
        manifest = SceneCheckpointManifest.model_validate_json(self.path.read_text(encoding="utf-8"))
        if manifest.checkpoint_hash != _checkpoint_hash(manifest):
            raise ValueError(f"Scene checkpoint hash mismatch: {self.path}")
        return manifest

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._manifest.updated_at = _now()
        self._manifest.checkpoint_hash = _checkpoint_hash(self._manifest)
        temp_path = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        temp_path.write_text(
            json.dumps(self._manifest.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp_path.replace(self.path)


def mark_scene_checkpoint_interrupted(path: Path, error: str) -> list[str]:
    if not path.is_file():
        return []
    raw = SceneCheckpointManifest.model_validate_json(path.read_text(encoding="utf-8"))
    store = SceneCheckpointStore(path, job_id=raw.job_id, scene_ids=[scene.scene_id for scene in raw.scenes])
    return store.fail_running(error)


def scene_generation_input_hash(
    scene: dict[str, Any],
    *,
    segment: int,
    total: int,
    backend: dict[str, Any],
    workflow_name: str,
    channel_profile: str,
    renderer_version: str = "video-generator-v1",
) -> str:
    backend_contract = {
        key: value
        for key, value in backend.items()
        if key in {"mode", "model", "checkpoint", "sampler", "scheduler"}
    }
    return deterministic_hash(
        {
            "scene": scene,
            "segment": segment,
            "total": total,
            "backend": backend_contract,
            "workflow_name": workflow_name,
            "channel_profile": channel_profile,
            "renderer_version": renderer_version,
        }
    )


def _checkpoint_hash(manifest: SceneCheckpointManifest) -> str:
    payload = manifest.model_dump(mode="json")
    payload.pop("checkpoint_hash", None)
    payload.pop("updated_at", None)
    return deterministic_hash(payload)


def _now() -> str:
    return datetime.now(UTC).isoformat()
