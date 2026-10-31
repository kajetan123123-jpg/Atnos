from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Literal

import httpx
import imageio_ffmpeg

from atnos.core.config import Settings
from atnos.services.hardware_guard_service import HardwareFeatureUnavailableError, HardwareGuardService
from atnos.services.output_artifact_service import ManagedArtifactDescriptor, OutputArtifactService


@dataclass(frozen=True, slots=True)
class Wav2LipInstallState:
    custom_node_dir: Path | None
    python_executable: Path | None
    runner_script: Path | None
    checkpoint_path: Path | None
    face_detector_path: Path | None
    exists: bool


@dataclass(frozen=True, slots=True)
class Wav2LipArtifactResult:
    descriptor: ManagedArtifactDescriptor
    fps: int
    face_detect_batch: int
    mode: Literal["sequential", "repetitive"]
    engine: str = "wav2lip-comfyui-runtime"

    def to_dict(self) -> dict[str, object]:
        return {
            "descriptor": {
                "filename": self.descriptor.filename,
                "storage_path": self.descriptor.storage_path,
                "media_type": self.descriptor.media_type,
                "file_extension": self.descriptor.file_extension,
                "size_bytes": self.descriptor.size_bytes,
            },
            "fps": self.fps,
            "face_detect_batch": self.face_detect_batch,
            "mode": self.mode,
            "engine": self.engine,
        }


class Wav2LipRuntimeError(RuntimeError):
    pass


class Wav2LipRuntimeService:
    WAV2LIP_URL = "https://huggingface.co/Nekochu/Wav2Lip/resolve/main/wav2lip_gan.pth?download=true"
    WAV2LIP_SHA256 = "ca9ab7b7b812c0e80a6e70a5977c545a1e8a365a6c49d5e533023c034d7ac3d8"
    S3FD_URL = "https://www.adrianbulat.com/downloads/python-fan/s3fd-619a316812.pth"
    S3FD_SHA256 = "619a31681264d3f7f7fc7a16a42cbbe8b23f31a256f75a366e5a1bcd59b33543"
    REQUIRED_IMPORTS = ("cv2", "librosa", "pydub", "soundfile", "numba", "scipy", "tqdm")
    INSTALL_PACKAGES = ("librosa", "soundfile", "pydub", "opencv-python-headless", "numba")

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.artifacts = OutputArtifactService(settings)

    def describe_install(self) -> Wav2LipInstallState:
        custom_node_dir = self.resolve_custom_node_dir()
        comfy_install_dir = self.settings.comfyui_install_dir.resolve()
        python_executable = self._existing_file(comfy_install_dir / "python_embeded" / "python.exe")
        runner_script = self.resolve_runner_script()
        checkpoint_path = self._existing_file(self.settings.wav2lip_checkpoint_path)
        face_detector_path = self._existing_file(self.settings.wav2lip_face_detector_path)
        exists = bool(
            custom_node_dir is not None
            and (custom_node_dir / "Wav2Lip" / "wav2lip_node.py").exists()
            and python_executable is not None
        )
        return Wav2LipInstallState(
            custom_node_dir=custom_node_dir,
            python_executable=python_executable,
            runner_script=runner_script,
            checkpoint_path=checkpoint_path,
            face_detector_path=face_detector_path,
            exists=exists,
        )

    def resolve_runner_script(self) -> Path | None:
        api_root = Path(__file__).resolve().parents[3]
        candidates = (
            api_root / "scripts" / "wav2lip_runner.py",
            self.settings.app_root / "runtime-recovered" / "api" / "scripts" / "wav2lip_runner.py",
            self.settings.app_root / "scripts" / "wav2lip_runner.py",
        )
        seen: set[str] = set()
        for candidate in candidates:
            resolved = candidate.resolve()
            key = str(resolved).lower()
            if key in seen:
                continue
            seen.add(key)
            if resolved.exists() and resolved.is_file():
                return resolved
        return None

    def resolve_custom_node_dir(self) -> Path | None:
        candidates = (
            self.settings.wav2lip_custom_node_dir,
            self.settings.comfyui_install_dir / "ComfyUI" / "custom_nodes" / "ComfyUI_wav2lip",
            Path("E:/Atnos/Tools/Comfy-Desktop/ComfyUI-Installs/Atnos/ComfyUI/custom_nodes/ComfyUI_wav2lip"),
        )
        seen: set[str] = set()
        for candidate in candidates:
            if candidate is None:
                continue
            resolved = candidate.resolve()
            key = str(resolved).lower()
            if key in seen:
                continue
            seen.add(key)
            if resolved.exists() and (resolved / "Wav2Lip" / "wav2lip_node.py").exists():
                return resolved
        return None

    def dependency_status(self) -> dict[str, bool]:
        state = self.describe_install()
        if state.python_executable is None:
            return {name: False for name in self.REQUIRED_IMPORTS}
        code = (
            "import importlib.util, json; "
            f"mods={list(self.REQUIRED_IMPORTS)!r}; "
            "print(json.dumps({m: importlib.util.find_spec(m) is not None for m in mods}))"
        )
        command = subprocess.run(
            [str(state.python_executable), "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if command.returncode != 0:
            return {name: False for name in self.REQUIRED_IMPORTS}
        for line in reversed(command.stdout.splitlines()):
            payload = line.strip()
            if not payload.startswith("{"):
                continue
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return {name: bool(parsed.get(name)) for name in self.REQUIRED_IMPORTS}
        return {name: False for name in self.REQUIRED_IMPORTS}

    def ensure_ready(self) -> Wav2LipInstallState:
        try:
            HardwareGuardService.require_gpu("Wav2Lip runtime")
        except HardwareFeatureUnavailableError as exc:
            raise Wav2LipRuntimeError(str(exc)) from exc
        state = self.describe_install()
        if not state.exists or state.custom_node_dir is None or state.python_executable is None:
            raise Wav2LipRuntimeError("Wav2Lip custom node or embedded ComfyUI Python is missing.")
        if state.runner_script is None:
            raise Wav2LipRuntimeError("ATNOS Wav2Lip runner script is missing.")
        self.ensure_dependencies_installed(python_executable=state.python_executable)
        self.ensure_models_ready()
        return self.describe_install()

    def ensure_dependencies_installed(self, *, python_executable: Path | None = None) -> None:
        state = self.describe_install()
        python_path = python_executable or state.python_executable
        if python_path is None:
            raise Wav2LipRuntimeError("ComfyUI embedded Python is unavailable.")
        dependency_state = self.dependency_status()
        missing = [name for name, ok in dependency_state.items() if not ok]
        if not missing:
            return
        command = [
            str(python_path),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            *self.INSTALL_PACKAGES,
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        if result.returncode != 0:
            raise Wav2LipRuntimeError(
                result.stderr.strip() or result.stdout.strip() or "Wav2Lip dependency install failed."
            )

    def ensure_models_ready(self) -> None:
        self._download_if_needed(
            target_path=self.settings.wav2lip_checkpoint_path,
            url=self.WAV2LIP_URL,
            expected_sha256=self.WAV2LIP_SHA256,
        )
        self._download_if_needed(
            target_path=self.settings.wav2lip_face_detector_path,
            url=self.S3FD_URL,
            expected_sha256=self.S3FD_SHA256,
        )

    def generate_talking_head(
        self,
        *,
        portrait_path: Path,
        audio_path: Path,
        conversation_id: int | None,
        fps: int | None = None,
        face_detect_batch: int | None = None,
        mode: Literal["sequential", "repetitive"] = "repetitive",
    ) -> Wav2LipArtifactResult:
        portrait = portrait_path.resolve()
        audio = audio_path.resolve()
        if not portrait.exists() or not portrait.is_file():
            raise Wav2LipRuntimeError("Portrait image for Wav2Lip was not found.")
        if not audio.exists() or not audio.is_file():
            raise Wav2LipRuntimeError("Audio file for Wav2Lip was not found.")

        state = self.ensure_ready()
        assert state.custom_node_dir is not None
        assert state.python_executable is not None
        assert state.runner_script is not None

        output_path = self.artifacts.create_output_path(conversation_id=conversation_id, extension=".mp4")
        work_dir = (self.settings.workspace_staging_dir / "wav2lip" / output_path.stem).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)

        command = [
            str(state.python_executable),
            str(state.runner_script),
            "--custom-node-dir",
            str(state.custom_node_dir),
            "--checkpoint",
            str(self.settings.wav2lip_checkpoint_path.resolve()),
            "--image",
            str(portrait),
            "--audio",
            str(audio),
            "--output",
            str(output_path),
            "--ffmpeg",
            imageio_ffmpeg.get_ffmpeg_exe(),
            "--work-dir",
            str(work_dir),
            "--fps",
            str(int(fps or self.settings.wav2lip_fps)),
            "--face-detect-batch",
            str(int(face_detect_batch or self.settings.wav2lip_face_detect_batch)),
            "--mode",
            mode,
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=self.settings.wav2lip_timeout_s,
            check=False,
        )
        if result.returncode != 0:
            try:
                output_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise Wav2LipRuntimeError(
                result.stderr.strip() or result.stdout.strip() or "Wav2Lip generation failed."
            )

        descriptor = self.artifacts.finalize_output(
            absolute_path=output_path,
            filename="atnos-talking-head.mp4",
            media_type="video/mp4",
        )
        return Wav2LipArtifactResult(
            descriptor=descriptor,
            fps=int(fps or self.settings.wav2lip_fps),
            face_detect_batch=int(face_detect_batch or self.settings.wav2lip_face_detect_batch),
            mode=mode,
        )

    def verify_face_detectable(
        self,
        *,
        portrait_path: Path,
        face_detect_batch: int | None = None,
    ) -> None:
        portrait = portrait_path.resolve()
        if not portrait.exists() or not portrait.is_file():
            raise Wav2LipRuntimeError("Portrait image for Wav2Lip validation was not found.")

        state = self.ensure_ready()
        assert state.custom_node_dir is not None
        assert state.python_executable is not None
        assert state.runner_script is not None

        work_dir = (self.settings.workspace_staging_dir / "wav2lip" / f"verify-{portrait.stem}").resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        command = [
            str(state.python_executable),
            str(state.runner_script),
            "--custom-node-dir",
            str(state.custom_node_dir),
            "--checkpoint",
            str(self.settings.wav2lip_checkpoint_path.resolve()),
            "--image",
            str(portrait),
            "--work-dir",
            str(work_dir),
            "--face-detect-batch",
            str(int(face_detect_batch or self.settings.wav2lip_face_detect_batch)),
            "--device",
            "auto",
            "--verify-face-only",
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=min(120, self.settings.wav2lip_timeout_s),
            check=False,
        )
        if result.returncode != 0:
            raise Wav2LipRuntimeError(
                result.stderr.strip() or result.stdout.strip() or "Wav2Lip face validation failed."
            )

    def _download_if_needed(self, *, target_path: Path, url: str, expected_sha256: str) -> None:
        target = target_path.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.is_file() and self._sha256(target) == expected_sha256:
            return
        temp_path = target.with_suffix(target.suffix + ".download")
        with httpx.Client(follow_redirects=True, timeout=60.0) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                with temp_path.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        if chunk:
                            handle.write(chunk)
        computed = self._sha256(temp_path)
        if computed != expected_sha256:
            temp_path.unlink(missing_ok=True)
            raise Wav2LipRuntimeError(
                f"Downloaded Wav2Lip artifact checksum mismatch for {target.name}: {computed}"
            )
        temp_path.replace(target)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                if chunk:
                    digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _existing_file(path: Path) -> Path | None:
        return path.resolve() if path.exists() and path.is_file() else None
