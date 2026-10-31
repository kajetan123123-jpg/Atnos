from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import uuid4

from atnos.core.config import Settings
from atnos.jarvis.core.config import JarvisConfigStore
from atnos.jarvis.core.ollama import JarvisOllamaClient
from atnos.jarvis.types import ToolResult
from atnos.services.comfyui_image_generation_service import ComfyUiImageGenerationError, ComfyUiImageGenerationService
from atnos.services.voice_transcription_service import VoiceTranscriptionService


class LocalImageGenerationTool:
    kind = "image_gen"
    provider_key = "comfyui_local"

    def __init__(self, settings: Settings, config_store: JarvisConfigStore) -> None:
        self.settings = settings
        self.config_store = config_store
        self.service = ComfyUiImageGenerationService(settings)

    async def is_available(self, **kwargs) -> bool:
        return await asyncio.to_thread(self.service.can_generate)

    async def run(self, **kwargs) -> ToolResult:
        output_path = self.config_store.paths.images_output_dir / f"{uuid4().hex}.png"
        try:
            result = await asyncio.to_thread(
                self.service.generate_background,
                prompt=kwargs["prompt"],
                output_path=output_path,
                width=int(kwargs.get("width", 1024)),
                height=int(kwargs.get("height", 1024)),
                seed=int(kwargs.get("seed", 42)),
                negative_prompt=kwargs.get("negative_prompt"),
            )
        except Exception as exc:
            if isinstance(exc, ComfyUiImageGenerationError):
                return ToolResult(success=False, reason=str(exc), provider_key=self.provider_key)
            return ToolResult(success=False, reason=f"local_image_generation_failed: {exc}", provider_key=self.provider_key)
        return ToolResult(success=True, provider_key=self.provider_key, artifacts={"image_path": result.output_path.as_posix()})

    def describe(self) -> dict[str, Any]:
        return {"kind": self.kind, "provider_key": self.provider_key}


class LocalTranscriptionTool:
    kind = "transcription"
    provider_key = "faster_whisper_local"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.service = VoiceTranscriptionService(settings)

    async def is_available(self, **kwargs) -> bool:
        return self.settings.voice_stt_engine == "faster-whisper-local" and bool((self.settings.voice_stt_model_path or "").strip())

    async def run(self, **kwargs) -> ToolResult:
        video_path = Path(kwargs["video_path"]).resolve()
        audio_bytes = await asyncio.to_thread(video_path.read_bytes)
        try:
            result = await self.service.transcribe(
                filename=video_path.name,
                content_type="video/mp4",
                audio_bytes=audio_bytes,
            )
        except Exception as exc:
            return ToolResult(success=False, reason=f"local_transcription_failed: {exc}", provider_key=self.provider_key)
        srt_path = video_path.with_suffix(".srt")
        await asyncio.to_thread(srt_path.write_text, self._build_minimal_srt(result.transcript), "utf-8")
        return ToolResult(
            success=True,
            provider_key=self.provider_key,
            artifacts={"srt_path": srt_path.as_posix()},
            plain_text=result.transcript,
        )

    @staticmethod
    def _build_minimal_srt(transcript: str) -> str:
        return f"1\n00:00:00,000 --> 00:00:30,000\n{transcript.strip()}\n"

    def describe(self) -> dict[str, Any]:
        return {"kind": self.kind, "provider_key": self.provider_key}


class LocalVideoEditTool:
    kind = "video_edit"
    provider_key = "ffmpeg_local"

    def __init__(self, settings: Settings, config_store: JarvisConfigStore) -> None:
        self.settings = settings
        self.config_store = config_store

    async def is_available(self, **kwargs) -> bool:
        try:
            import imageio_ffmpeg  # type: ignore

            return bool(imageio_ffmpeg.get_ffmpeg_exe())
        except Exception:
            return False

    async def run(self, **kwargs) -> ToolResult:
        import imageio_ffmpeg  # type: ignore

        video_path = Path(kwargs["video_path"]).resolve()
        srt_path = Path(kwargs["srt_path"]).resolve()
        output_path = (self.config_store.paths.root_dir / "tmp" / "atnos_outputs" / "videos" / f"{uuid4().hex}.mp4").resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        cut_start = float(kwargs.get("cut_start", 0.0))
        cut_end = float(kwargs.get("cut_end", 0.0))
        duration_args: list[str] = []
        if cut_end > cut_start:
            duration_args = ["-ss", str(cut_start), "-to", str(cut_end)]
        subtitle_filter = self._build_subtitle_filter(srt_path)
        command = [
            ffmpeg,
            "-y",
            *duration_args,
            "-i",
            video_path.as_posix(),
            "-vf",
            f"crop='min(iw,ih*9/16)':ih,scale=1080:1920,{subtitle_filter}",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            output_path.as_posix(),
        ]
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _stdout, stderr = await process.communicate()
        if process.returncode != 0:
            return ToolResult(success=False, reason=stderr.decode("utf-8", errors="ignore"), provider_key=self.provider_key)
        return ToolResult(success=True, provider_key=self.provider_key, artifacts={"video_path": output_path.as_posix()})

    @staticmethod
    def _build_subtitle_filter(srt_path: Path) -> str:
        escaped = srt_path.as_posix().replace(":", "\\:").replace("'", "\\'")
        return f"subtitles='{escaped}'"

    def describe(self) -> dict[str, Any]:
        return {"kind": self.kind, "provider_key": self.provider_key}


class LocalLlmTool:
    kind = "llm"
    provider_key = "ollama_local"

    def __init__(self, settings: Settings, config_store: JarvisConfigStore) -> None:
        self.settings = settings
        self.client = JarvisOllamaClient(settings, config_store)

    async def is_available(self, **kwargs) -> bool:
        try:
            await self.client.generate_text(prompt="Say OK in one word.")
            return True
        except Exception:
            return False

    async def run(self, **kwargs) -> ToolResult:
        try:
            response = await self.client.generate_text(
                prompt=str(kwargs.get("prompt") or ""),
                system=kwargs.get("system_prompt"),
                model=kwargs.get("model"),
            )
        except Exception as exc:
            return ToolResult(success=False, reason=f"local_llm_failed: {exc}", provider_key=self.provider_key)
        return ToolResult(success=True, provider_key=self.provider_key, plain_text=response)

    def describe(self) -> dict[str, Any]:
        return {"kind": self.kind, "provider_key": self.provider_key}
