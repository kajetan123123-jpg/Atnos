from __future__ import annotations

from dataclasses import dataclass
import multiprocessing
import os
from pathlib import Path
import queue
import re
import time
import unicodedata

import imageio_ffmpeg
from moviepy import AudioFileClip, ColorClip, CompositeVideoClip, ImageClip, TextClip, VideoFileClip, concatenate_videoclips

from atnos.core.config import Settings
from atnos.services.hardware_guard_service import HardwareGuardService
from atnos.services.output_artifact_service import ManagedArtifactDescriptor, OutputArtifactService


@dataclass(frozen=True, slots=True)
class RenderArtifactQa:
    duration_sec: float
    width: int
    height: int
    has_audio: bool
    size_bytes: int
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "duration_sec": self.duration_sec,
            "width": self.width,
            "height": self.height,
            "has_audio": self.has_audio,
            "size_bytes": self.size_bytes,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class RenderArtifactResult:
    descriptor: ManagedArtifactDescriptor
    qa: RenderArtifactQa
    watchdog_timeout_s: int
    render_duration_sec: float
    engine: str = "moviepy-ffmpeg-windows"
    watchdog_recovered: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "descriptor": {
                "filename": self.descriptor.filename,
                "storage_path": self.descriptor.storage_path,
                "media_type": self.descriptor.media_type,
                "file_extension": self.descriptor.file_extension,
                "size_bytes": self.descriptor.size_bytes,
            },
            "qa": self.qa.to_dict(),
            "watchdog_timeout_s": self.watchdog_timeout_s,
            "render_duration_sec": self.render_duration_sec,
            "engine": self.engine,
            "watchdog_recovered": self.watchdog_recovered,
        }


@dataclass(frozen=True, slots=True)
class _RenderWorkerPayload:
    frame_paths: tuple[str, ...]
    audio_path: str
    subtitles_path: str | None
    output_path: str
    render_profile: str


def _render_short_worker(
    payload: _RenderWorkerPayload,
    result_queue,
) -> None:
    try:
        _render_short_sync(
            frame_paths=[Path(item) for item in payload.frame_paths],
            audio_path=Path(payload.audio_path),
            subtitles_path=Path(payload.subtitles_path) if payload.subtitles_path else None,
            output_path=Path(payload.output_path),
            render_profile=payload.render_profile,
        )
        result_queue.put({"ok": True})
    except Exception as exc:  # pragma: no cover - executed in child process
        result_queue.put({"ok": False, "error": str(exc)})


class RenderService:
    VIDEO_SIZE = (1080, 1920)
    MIN_OUTPUT_SIZE_BYTES = 48 * 1024
    MIN_DURATION_SEC = 3.0

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.artifacts = OutputArtifactService(settings)
        self.font_regular_path = self._find_font("Vera.ttf")
        self.font_bold_path = self._find_font("VeraBd.ttf")

    def render_short(
        self,
        *,
        frame_paths: list[Path],
        audio_path: Path,
        conversation_id: int | None,
        subtitles_path: Path | None = None,
        render_profile: str = "quality",
    ) -> RenderArtifactResult:
        if not frame_paths:
            raise RuntimeError("No frames were provided for video rendering.")
        if not audio_path.exists():
            raise RuntimeError("Narration audio was not created.")

        os.environ.setdefault("IMAGEIO_FFMPEG_EXE", imageio_ffmpeg.get_ffmpeg_exe())
        output_path = self.artifacts.create_output_path(conversation_id=conversation_id, extension=".mp4")
        worker_payload = _RenderWorkerPayload(
            frame_paths=tuple(path.as_posix() for path in frame_paths),
            audio_path=audio_path.as_posix(),
            subtitles_path=subtitles_path.as_posix() if subtitles_path is not None else None,
            output_path=output_path.as_posix(),
            render_profile=render_profile,
        )

        context = multiprocessing.get_context("spawn")
        result_queue = context.Queue()
        process = context.Process(
            target=_render_short_worker,
            args=(worker_payload, result_queue),
            daemon=True,
        )

        started = time.monotonic()
        process.start()
        process.join(timeout=self.settings.render_watchdog_timeout_s)
        render_duration_sec = round(time.monotonic() - started, 3)

        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
            recovered = self._recover_timed_out_render(
                output_path=output_path,
                render_duration_sec=render_duration_sec,
            )
            if recovered is not None:
                return recovered
            self._cleanup_partial_output(output_path)
            raise RuntimeError(
                f"Render watchdog stopped a stuck render after {self.settings.render_watchdog_timeout_s} seconds."
            )

        result_payload = self._consume_worker_payload(result_queue)
        if process.exitcode not in {0, None}:
            self._cleanup_partial_output(output_path)
            raise RuntimeError(
                str(result_payload.get("error"))
                if isinstance(result_payload, dict) and result_payload.get("error")
                else f"Render process exited with code {process.exitcode}."
            )
        if isinstance(result_payload, dict) and result_payload.get("ok") is False:
            self._cleanup_partial_output(output_path)
            raise RuntimeError(str(result_payload.get("error") or "Render worker failed."))

        descriptor = self.artifacts.finalize_output(
            absolute_path=output_path,
            filename="atnos-short.mp4",
            media_type="video/mp4",
        )
        qa = self._validate_render_output(descriptor.absolute_path)
        return RenderArtifactResult(
            descriptor=descriptor,
            qa=qa,
            watchdog_timeout_s=self.settings.render_watchdog_timeout_s,
            render_duration_sec=render_duration_sec,
        )

    def _recover_timed_out_render(
        self,
        *,
        output_path: Path,
        render_duration_sec: float,
    ) -> RenderArtifactResult | None:
        if not output_path.exists() or not output_path.is_file():
            return None

        try:
            descriptor = self.artifacts.finalize_output(
                absolute_path=output_path,
                filename="atnos-short.mp4",
                media_type="video/mp4",
            )
            qa = self._validate_render_output(
                descriptor.absolute_path,
                extra_warnings=(
                    f"Render exceeded the {self.settings.render_watchdog_timeout_s}s watchdog but a valid output was recovered after the worker was stopped.",
                ),
            )
        except Exception:
            return None

        return RenderArtifactResult(
            descriptor=descriptor,
            qa=qa,
            watchdog_timeout_s=self.settings.render_watchdog_timeout_s,
            render_duration_sec=render_duration_sec,
            watchdog_recovered=True,
        )

    def _validate_render_output(
        self,
        output_path: Path,
        *,
        extra_warnings: tuple[str, ...] = (),
    ) -> RenderArtifactQa:
        if not output_path.exists() or not output_path.is_file():
            raise RuntimeError("Render output file was not created.")

        size_bytes = output_path.stat().st_size
        if size_bytes < self.MIN_OUTPUT_SIZE_BYTES:
            raise RuntimeError("Rendered output is unexpectedly small and looks incomplete.")

        video_clip = VideoFileClip(str(output_path))
        try:
            duration = float(video_clip.duration or 0.0)
            width = int(video_clip.w or 0)
            height = int(video_clip.h or 0)
            has_audio = bool(
                video_clip.audio is not None
                and float(video_clip.audio.duration or 0.0) > 0.1
            )
        finally:
            video_clip.close()

        if duration <= 0.5:
            raise RuntimeError("Rendered output has no usable duration.")
        if width <= 0 or height <= 0:
            raise RuntimeError("Rendered output has invalid dimensions.")

        warnings: list[str] = list(extra_warnings)
        if width != self.VIDEO_SIZE[0] or height != self.VIDEO_SIZE[1]:
            warnings.append(
                f"Expected {self.VIDEO_SIZE[0]}x{self.VIDEO_SIZE[1]}, got {width}x{height}."
            )
        if duration < self.MIN_DURATION_SEC:
            warnings.append("Rendered short is unusually short.")
        if not has_audio:
            warnings.append("Rendered short has no detectable audio track.")

        return RenderArtifactQa(
            duration_sec=round(duration, 3),
            width=width,
            height=height,
            has_audio=has_audio,
            size_bytes=size_bytes,
            warnings=tuple(warnings),
        )

    @staticmethod
    def _consume_worker_payload(result_queue) -> dict[str, object]:
        try:
            payload = result_queue.get_nowait()
        except queue.Empty:
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _cleanup_partial_output(output_path: Path) -> None:
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _build_motion_frame_clip(*, frame_path: Path, duration: float, index: int):
        scale = 1.1
        clip = ImageClip(str(frame_path)).with_duration(duration)
        clip = clip.resized(width=int(RenderService.VIDEO_SIZE[0] * scale))
        max_x = max(0, clip.w - RenderService.VIDEO_SIZE[0])
        max_y = max(0, clip.h - RenderService.VIDEO_SIZE[1])
        start_x = 0 if index % 2 == 0 else max_x
        end_x = max_x if index % 2 == 0 else 0
        start_y = int(max_y * (0.15 if index % 2 == 0 else 0.6))
        end_y = int(max_y * (0.6 if index % 2 == 0 else 0.15))
        motion_clip = clip.with_position(
            lambda t: (
                -RenderService._lerp(start_x, end_x, t / max(duration, 0.001)),
                -RenderService._lerp(start_y, end_y, t / max(duration, 0.001)),
            )
        )

        vignette = ColorClip(size=RenderService.VIDEO_SIZE, color=(0, 0, 0)).with_opacity(0.08).with_duration(duration)
        return CompositeVideoClip([motion_clip, vignette], size=RenderService.VIDEO_SIZE).with_duration(duration)

    def _build_subtitle_layers(self, *, subtitles_path: Path, total_duration: float):
        layers = []
        for start, end, text in self._parse_srt(subtitles_path):
            if start >= total_duration:
                continue
            safe_text = self._display_text(text).upper()
            if not safe_text:
                continue
            text_clip = TextClip(
                font=str(self.font_bold_path) if self.font_bold_path is not None else None,
                text=safe_text,
                font_size=56,
                size=(920, None),
                method="caption",
                color="white",
                stroke_color="#0b1020",
                stroke_width=5,
                text_align="center",
                duration=max(0.1, min(end, total_duration) - start),
            ).with_start(start).with_position(("center", 1600))
            background = ColorClip(
                size=(980, max(110, text_clip.h + 28)),
                color=(10, 14, 24),
            ).with_opacity(0.66).with_start(start).with_duration(max(0.1, min(end, total_duration) - start)).with_position(("center", 1588))
            layers.extend([background, text_clip])
        return layers

    @staticmethod
    def _parse_srt(path: Path) -> list[tuple[float, float, str]]:
        content = path.read_text(encoding="utf-8", errors="ignore")
        blocks = re.split(r"\r?\n\r?\n", content.strip())
        segments: list[tuple[float, float, str]] = []
        for block in blocks:
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            if len(lines) < 3:
                continue
            timing = lines[1]
            if "-->" not in timing:
                continue
            start_raw, end_raw = [part.strip() for part in timing.split("-->", maxsplit=1)]
            text = " ".join(lines[2:])
            segments.append((RenderService._parse_srt_time(start_raw), RenderService._parse_srt_time(end_raw), text))
        return segments

    @staticmethod
    def _parse_srt_time(value: str) -> float:
        match = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})", value.strip())
        if match is None:
            return 0.0
        hours, minutes, seconds, millis = (int(part) for part in match.groups())
        return hours * 3600 + minutes * 60 + seconds + millis / 1000

    @staticmethod
    def _display_text(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value or "")
        ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
        return re.sub(r"\s+", " ", ascii_only).strip()

    @staticmethod
    def _find_font(name: str) -> Path | None:
        reportlab_font = Path(__import__("reportlab").__file__).resolve().parent / "fonts" / name
        if reportlab_font.exists():
            return reportlab_font
        return None

    @staticmethod
    def _lerp(start: int, end: int, factor: float) -> int:
        factor = max(0.0, min(1.0, factor))
        return int(start + (end - start) * factor)


def _render_short_sync(
    *,
    frame_paths: list[Path],
    audio_path: Path,
    subtitles_path: Path | None,
    output_path: Path,
    render_profile: str,
) -> None:
    os.environ.setdefault("IMAGEIO_FFMPEG_EXE", imageio_ffmpeg.get_ffmpeg_exe())

    audio_clip = AudioFileClip(str(audio_path))
    clips = []
    subtitle_layers = []
    base_video = None
    final_video = None
    try:
        per_frame = max(1.4, audio_clip.duration / len(frame_paths))
        clips = [
            _build_motion_frame_clip(frame_path=frame, duration=per_frame, index=index)
            for index, frame in enumerate(frame_paths, start=1)
        ]
        base_video = concatenate_videoclips(clips, method="compose").with_audio(audio_clip)

        if subtitles_path is not None and subtitles_path.exists():
            subtitle_layers = _build_subtitle_layers(
                subtitles_path=subtitles_path,
                total_duration=audio_clip.duration,
            )

        layers = [base_video, *subtitle_layers]
        final_video = CompositeVideoClip(layers, size=RenderService.VIDEO_SIZE).with_audio(audio_clip).with_duration(audio_clip.duration)
        _write_short_video(final_video, output_path, render_profile=render_profile)
    finally:
        for layer in subtitle_layers:
            try:
                layer.close()
            except Exception:
                pass
        for clip in clips:
            try:
                clip.close()
            except Exception:
                pass
        if base_video is not None:
            try:
                base_video.close()
            except Exception:
                pass
        if final_video is not None:
            try:
                final_video.close()
            except Exception:
                pass
        try:
            audio_clip.close()
        except Exception:
            pass


def _write_short_video(final_video, output_path: Path, *, render_profile: str) -> None:
    attempts = resolve_video_export_attempts(
        render_profile=render_profile,
        force_cpu_path=HardwareGuardService.is_gpu_forced_unavailable(),
    )
    last_error: Exception | None = None
    for codec, ffmpeg_params in attempts:
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            final_video.write_videofile(
                str(output_path),
                fps=24,
                codec=codec,
                audio_codec="aac",
                ffmpeg_params=ffmpeg_params,
                logger=None,
            )
            return
        except Exception as exc:
            last_error = exc
    normalized_profile = str(render_profile or "quality").strip().lower()
    raise RuntimeError(f"Render export failed for profile {normalized_profile}: {last_error}")


def _build_motion_frame_clip(*, frame_path: Path, duration: float, index: int):
    scale = 1.1
    clip = ImageClip(str(frame_path)).with_duration(duration)
    clip = clip.resized(width=int(RenderService.VIDEO_SIZE[0] * scale))
    max_x = max(0, clip.w - RenderService.VIDEO_SIZE[0])
    max_y = max(0, clip.h - RenderService.VIDEO_SIZE[1])
    start_x = 0 if index % 2 == 0 else max_x
    end_x = max_x if index % 2 == 0 else 0
    start_y = int(max_y * (0.15 if index % 2 == 0 else 0.6))
    end_y = int(max_y * (0.6 if index % 2 == 0 else 0.15))
    motion_clip = clip.with_position(
        lambda t: (
            -RenderService._lerp(start_x, end_x, t / max(duration, 0.001)),
            -RenderService._lerp(start_y, end_y, t / max(duration, 0.001)),
        )
    )

    vignette = ColorClip(size=RenderService.VIDEO_SIZE, color=(0, 0, 0)).with_opacity(0.08).with_duration(duration)
    return CompositeVideoClip([motion_clip, vignette], size=RenderService.VIDEO_SIZE).with_duration(duration)


def _build_subtitle_layers(*, subtitles_path: Path, total_duration: float):
    font_bold_path = RenderService._find_font("VeraBd.ttf")
    layers = []
    for start, end, text in RenderService._parse_srt(subtitles_path):
        if start >= total_duration:
            continue
        safe_text = RenderService._display_text(text).upper()
        if not safe_text:
            continue
        text_clip = TextClip(
            font=str(font_bold_path) if font_bold_path is not None else None,
            text=safe_text,
            font_size=56,
            size=(920, None),
            method="caption",
            color="white",
            stroke_color="#0b1020",
            stroke_width=5,
            text_align="center",
            duration=max(0.1, min(end, total_duration) - start),
        ).with_start(start).with_position(("center", 1600))
        background = ColorClip(
            size=(980, max(110, text_clip.h + 28)),
            color=(10, 14, 24),
        ).with_opacity(0.66).with_start(start).with_duration(max(0.1, min(end, total_duration) - start)).with_position(("center", 1588))
        layers.extend([background, text_clip])
    return layers


def resolve_video_export_attempts(
    *,
    render_profile: str,
    force_cpu_path: bool,
) -> tuple[tuple[str, list[str]], ...]:
    profile = str(render_profile or "quality").strip().lower()
    if profile == "quality":
        return (
            (
                "libx264",
                ["-preset", "medium", "-crf", "18", "-movflags", "+faststart", "-pix_fmt", "yuv420p"],
            ),
        )
    if profile == "balanced":
        if not force_cpu_path:
            return (
                ("h264_amf", ["-quality", "speed", "-usage", "transcoding", "-pix_fmt", "yuv420p"]),
                (
                    "libx264",
                    ["-preset", "fast", "-crf", "21", "-movflags", "+faststart", "-pix_fmt", "yuv420p"],
                ),
            )
        return (
            (
                "libx264",
                ["-preset", "fast", "-crf", "21", "-movflags", "+faststart", "-pix_fmt", "yuv420p"],
            ),
        )
    return (
        (
            "libx264",
            ["-preset", "ultrafast", "-crf", "24", "-pix_fmt", "yuv420p"],
        ),
    )
