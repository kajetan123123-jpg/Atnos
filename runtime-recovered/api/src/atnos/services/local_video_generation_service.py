from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import logging
from pathlib import Path
import subprocess
import sys
from typing import Any, Literal
from uuid import uuid4

import imageio

from atnos.core.config import Settings

logger = logging.getLogger(__name__)

VideoBackend = Literal["ltx-video", "wan-video"]


@dataclass(frozen=True, slots=True)
class VideoGenerationReadiness:
    video_generation: bool
    reason: str | None = None
    detail: str | None = None
    backend: VideoBackend | None = None
    model_dir: Path | None = None
    dependencies_installed: bool = False
    model_downloaded: bool = False


@dataclass(frozen=True, slots=True)
class VideoGenerationResult:
    output_path: Path
    backend: VideoBackend
    width: int
    height: int
    duration_sec: int
    fps: int
    frame_count: int
    vram_total_gb: float | None


class VideoGenerationError(RuntimeError):
    pass


class LocalVideoGenerationService:
    REQUIRED_PACKAGES = ("torch", "diffusers", "transformers", "accelerate", "ftfy")

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def ensure_startup_ready(self) -> VideoGenerationReadiness:
        """Probe readiness without installing packages or downloading model files.

        Startup must stay fast and deterministic. Heavy dependency installation
        and Hugging Face model downloads are intentionally left to explicit
        generation/setup flows.
        """
        try:
            missing_dependencies = self.missing_dependencies()
            if missing_dependencies:
                return VideoGenerationReadiness(
                    video_generation=False,
                    reason="Video generation dependencies are not installed",
                    detail=(
                        "Missing packages: "
                        f"{', '.join(missing_dependencies)}. "
                        "Startup does not install video dependencies automatically."
                    ),
                    dependencies_installed=False,
                )

            for backend in ("ltx-video", "wan-video"):
                model_dir = self._model_dir(backend)
                if not self._model_dir_ready(model_dir):
                    continue
                return VideoGenerationReadiness(
                    video_generation=True,
                    backend=backend,
                    model_dir=model_dir,
                    dependencies_installed=True,
                    model_downloaded=False,
                    detail=(
                        "LTX-Video unavailable; Wan Video ready as fallback."
                        if backend == "wan-video"
                        else None
                    ),
                )

            return VideoGenerationReadiness(
                video_generation=False,
                reason="Video generation model is not downloaded",
                detail=(
                    "No local video model directory contains model_index.json. "
                    "Run an explicit video generation/setup action to download models; "
                    "startup never downloads model files."
                ),
                dependencies_installed=True,
                model_downloaded=False,
            )
        except Exception as exc:
            logger.exception("Video generation startup check failed")
            return VideoGenerationReadiness(
                video_generation=False,
                reason="Video generation startup failed",
                detail=str(exc),
            )

    def missing_dependencies(self) -> tuple[str, ...]:
        return tuple(package for package in self.REQUIRED_PACKAGES if importlib.util.find_spec(package) is None)

    def ensure_dependencies(self) -> bool:
        missing = list(self.missing_dependencies())
        if not missing:
            return False
        command = [sys.executable, "-m", "pip", "install", *missing]
        logger.warning("Installing missing video generation dependencies: %s", ", ".join(missing))
        result = subprocess.run(command, capture_output=True, text=True, timeout=60 * 30, check=False)
        if result.returncode != 0:
            raise VideoGenerationError(
                "Video generation dependency install failed: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        importlib.invalidate_caches()
        still_missing = [package for package in self.REQUIRED_PACKAGES if importlib.util.find_spec(package) is None]
        if still_missing:
            raise VideoGenerationError(f"Video generation dependencies still missing: {', '.join(still_missing)}")
        return True

    def ensure_model_ready(self, backend: VideoBackend) -> VideoGenerationReadiness:
        repo_id = self._repo_id(backend)
        model_dir = self._model_dir(backend)
        if self._model_dir_ready(model_dir):
            return VideoGenerationReadiness(video_generation=True, backend=backend, model_dir=model_dir)
        try:
            from huggingface_hub import snapshot_download

            model_dir.mkdir(parents=True, exist_ok=True)
            snapshot_download(repo_id=repo_id, local_dir=str(model_dir), resume_download=True)
        except Exception as exc:
            return VideoGenerationReadiness(
                video_generation=False,
                reason="Video model download failed",
                detail=f"{repo_id}: {exc}",
                backend=backend,
                model_dir=model_dir,
            )
        if not self._model_dir_ready(model_dir):
            return VideoGenerationReadiness(
                video_generation=False,
                reason="Video model download failed",
                detail=f"{repo_id} did not produce a usable diffusers model directory.",
                backend=backend,
                model_dir=model_dir,
            )
        return VideoGenerationReadiness(
            video_generation=True,
            backend=backend,
            model_dir=model_dir,
            model_downloaded=True,
        )

    def generate_video(
        self,
        *,
        prompt: str,
        output_path: Path,
        duration_sec: int = 8,
        fps: int = 24,
        width: int = 512,
        height: int = 512,
    ) -> VideoGenerationResult:
        normalized_prompt = " ".join(str(prompt or "").split())
        if not normalized_prompt:
            raise VideoGenerationError("Video prompt is empty.")
        self.ensure_dependencies()
        plan = self._resolve_generation_plan(
            duration_sec=duration_sec,
            fps=fps,
            width=width,
            height=height,
        )
        errors: list[str] = []
        for backend in ("ltx-video", "wan-video"):
            readiness = self.ensure_model_ready(backend)
            if not readiness.video_generation:
                errors.append(f"{backend}: {readiness.detail or readiness.reason}")
                continue
            for attempt in range(2):
                try:
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    if output_path.exists():
                        output_path.unlink()
                    self._generate_with_backend(
                        backend=backend,
                        model_dir=readiness.model_dir or self._model_dir(backend),
                        prompt=normalized_prompt,
                        output_path=output_path,
                        duration_sec=plan["duration_sec"],
                        fps=plan["fps"],
                        width=plan["width"],
                        height=plan["height"],
                        frame_count=plan["frame_count"],
                    )
                    if self._verify_mp4(output_path):
                        return VideoGenerationResult(
                            output_path=output_path.resolve(),
                            backend=backend,
                            width=plan["width"],
                            height=plan["height"],
                            duration_sec=plan["duration_sec"],
                            fps=plan["fps"],
                            frame_count=plan["frame_count"],
                            vram_total_gb=plan["vram_total_gb"],
                        )
                    errors.append(f"{backend}: generated MP4 failed verification on attempt {attempt + 1}")
                except Exception as exc:
                    errors.append(f"{backend}: {type(exc).__name__}: {exc}")
                    break
        raise VideoGenerationError(
            "Local video generation failed. "
            "No text-script fallback was used. "
            f"Failures: {' | '.join(errors) if errors else 'unknown failure'}"
        )

    def _generate_with_backend(
        self,
        *,
        backend: VideoBackend,
        model_dir: Path,
        prompt: str,
        output_path: Path,
        duration_sec: int,
        fps: int,
        width: int,
        height: int,
        frame_count: int,
    ) -> None:
        import torch
        from diffusers.utils import export_to_video

        device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.bfloat16 if device == "cuda" else torch.float32
        if backend == "ltx-video":
            from diffusers import LTXPipeline

            pipeline = LTXPipeline.from_pretrained(str(model_dir), torch_dtype=dtype)
        else:
            from diffusers import WanPipeline

            pipeline = WanPipeline.from_pretrained(str(model_dir), torch_dtype=dtype)
        if hasattr(pipeline, "enable_model_cpu_offload") and device == "cuda":
            pipeline.enable_model_cpu_offload()
        else:
            pipeline.to(device)
        generator = torch.Generator(device=device).manual_seed(int(uuid4().int % 2_147_483_647))
        call_kwargs: dict[str, object] = {
            "prompt": prompt,
            "negative_prompt": self.settings.video_generation_negative_prompt,
            "width": width,
            "height": height,
            "num_frames": frame_count,
            "num_inference_steps": max(1, int(self.settings.video_generation_steps)),
            "guidance_scale": float(self.settings.video_generation_guidance_scale),
            "generator": generator,
        }
        if backend == "ltx-video":
            call_kwargs["frame_rate"] = fps
        try:
            output = pipeline(**call_kwargs)
        except TypeError:
            call_kwargs.pop("frame_rate", None)
            output = pipeline(**call_kwargs)
        frames = output.frames[0]
        export_to_video(frames, str(output_path), fps=fps)

    def _resolve_generation_plan(self, *, duration_sec: int, fps: int, width: int, height: int) -> dict[str, Any]:
        vram_total_gb = self._vram_total_gb()
        resolved_width = self._align_to_32(max(128, int(width)))
        resolved_height = self._align_to_32(max(128, int(height)))
        resolved_duration = max(1, int(duration_sec))
        if vram_total_gb is not None and vram_total_gb < 8:
            resolved_width = 256
            resolved_height = 256
            resolved_duration = min(resolved_duration, 4)
        elif vram_total_gb is not None and vram_total_gb < 12:
            resolved_width = min(resolved_width, 512)
            resolved_height = min(resolved_height, 512)
            resolved_duration = min(resolved_duration, 6)
        resolved_fps = max(1, int(fps))
        frame_count = max(1, resolved_duration * resolved_fps)
        return {
            "duration_sec": resolved_duration,
            "fps": resolved_fps,
            "width": resolved_width,
            "height": resolved_height,
            "frame_count": frame_count,
            "vram_total_gb": vram_total_gb,
        }

    @staticmethod
    def _verify_mp4(path: Path) -> bool:
        if not path.exists() or path.stat().st_size <= 0:
            return False
        try:
            reader = imageio.get_reader(str(path))
            try:
                metadata = reader.get_meta_data()
                return bool(metadata)
            finally:
                reader.close()
        except Exception:
            return False

    @staticmethod
    def _align_to_32(value: int) -> int:
        return max(32, int(value) - (int(value) % 32))

    @staticmethod
    def _vram_total_gb() -> float | None:
        try:
            import torch

            if not torch.cuda.is_available():
                return 0.0
            return float(torch.cuda.get_device_properties(0).total_memory) / (1024 ** 3)
        except Exception:
            return None

    def _repo_id(self, backend: VideoBackend) -> str:
        return self.settings.ltx_video_model_repo if backend == "ltx-video" else self.settings.wan_video_model_repo

    def _model_dir(self, backend: VideoBackend) -> Path:
        raw = self.settings.ltx_video_model_dir if backend == "ltx-video" else self.settings.wan_video_model_dir
        if raw is None:
            return (self.settings.data_dir / "models" / "video" / backend).resolve()
        return raw.resolve()

    @staticmethod
    def _model_dir_ready(model_dir: Path) -> bool:
        return model_dir.exists() and (model_dir / "model_index.json").exists()
