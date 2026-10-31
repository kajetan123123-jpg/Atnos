from __future__ import annotations

from dataclasses import dataclass
import multiprocessing
import os
from pathlib import Path
import queue
import time

import imageio_ffmpeg
from moviepy import (
    AudioFileClip,
    ColorClip,
    CompositeVideoClip,
    ImageClip,
    TextClip,
    VideoFileClip,
)
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from atnos.core.config import Settings
from atnos.services.output_artifact_service import OutputArtifactService
from atnos.services.render_service import (
    RenderArtifactResult,
    RenderService,
    _build_subtitle_layers,
    resolve_video_export_attempts,
)


@dataclass(frozen=True, slots=True)
class _SyntheticMotionPayload:
    portrait_path: str
    talking_head_path: str
    audio_path: str
    subtitles_path: str | None
    cutaway_paths: tuple[str, ...]
    output_path: str
    style_label: str
    reel_label: str
    hook_text: str
    accent_hex: str
    target_duration_cap_sec: float | None
    target_duration_window_sec: tuple[float, float] | None
    hook_duration_cap_sec: float | None
    max_cutaways: int | None
    cutaway_display_duration_sec: float | None
    render_profile: str


def _render_synthetic_motion_worker(payload: _SyntheticMotionPayload, result_queue) -> None:
    try:
        _render_synthetic_motion_sync(payload)
        result_queue.put({"ok": True})
    except Exception as exc:  # pragma: no cover - child process
        result_queue.put({"ok": False, "error": str(exc)})


class SyntheticMotionCompositorService:
    VIDEO_SIZE = (1080, 1920)
    FOREGROUND_WIDTH = 820
    FOREGROUND_TOP = 420
    CARD_CORNER_RADIUS = 44
    ACCENT_HEX_BY_STYLE: dict[str, str] = {
        "editorial_premium": "#D6B36A",
        "cozy_girlfriend": "#E5A37A",
        "late_night_egirl": "#6AE1E8",
        "energetic_expert": "#73D98C",
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.artifacts = OutputArtifactService(settings)
        self.render_qc = RenderService(settings)

    def compose(
        self,
        *,
        portrait_path: Path,
        talking_head_path: Path,
        audio_path: Path,
        subtitles_path: Path | None,
        cutaway_paths: tuple[Path, ...] = (),
        conversation_id: int | None,
        style_key: str,
        style_label: str,
        reel_label: str,
        hook_text: str,
        target_duration_cap_sec: float | None = None,
        target_duration_window_sec: tuple[float, float] | None = None,
        hook_duration_cap_sec: float | None = None,
        max_cutaways: int | None = None,
        cutaway_display_duration_sec: float | None = None,
        render_profile: str = "quality",
    ) -> RenderArtifactResult:
        if not portrait_path.exists():
            raise RuntimeError("Synthetic motion compositor requires a portrait image.")
        if not talking_head_path.exists():
            raise RuntimeError("Synthetic motion compositor requires a talking-head video.")
        if not audio_path.exists():
            raise RuntimeError("Synthetic motion compositor requires a narration audio track.")

        os.environ.setdefault("IMAGEIO_FFMPEG_EXE", imageio_ffmpeg.get_ffmpeg_exe())
        output_path = self.artifacts.create_output_path(conversation_id=conversation_id, extension=".mp4")
        payload = _SyntheticMotionPayload(
            portrait_path=portrait_path.as_posix(),
            talking_head_path=talking_head_path.as_posix(),
            audio_path=audio_path.as_posix(),
            subtitles_path=subtitles_path.as_posix() if subtitles_path is not None else None,
            cutaway_paths=tuple(path.as_posix() for path in cutaway_paths if path.exists()),
            output_path=output_path.as_posix(),
            style_label=style_label,
            reel_label=reel_label,
            hook_text=hook_text,
            accent_hex=self.ACCENT_HEX_BY_STYLE.get(style_key, self.ACCENT_HEX_BY_STYLE["editorial_premium"]),
            target_duration_cap_sec=target_duration_cap_sec,
            target_duration_window_sec=target_duration_window_sec,
            hook_duration_cap_sec=hook_duration_cap_sec,
            max_cutaways=max_cutaways,
            cutaway_display_duration_sec=cutaway_display_duration_sec,
            render_profile=render_profile,
        )

        context = multiprocessing.get_context("spawn")
        result_queue = context.Queue()
        process = context.Process(
            target=_render_synthetic_motion_worker,
            args=(payload, result_queue),
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
                f"Synthetic motion watchdog stopped a stuck render after {self.settings.render_watchdog_timeout_s} seconds."
            )

        result_payload = self._consume_worker_payload(result_queue)
        if process.exitcode not in {0, None}:
            self._cleanup_partial_output(output_path)
            raise RuntimeError(
                str(result_payload.get("error"))
                if isinstance(result_payload, dict) and result_payload.get("error")
                else f"Synthetic motion process exited with code {process.exitcode}."
            )
        if isinstance(result_payload, dict) and result_payload.get("ok") is False:
            self._cleanup_partial_output(output_path)
            raise RuntimeError(str(result_payload.get("error") or "Synthetic motion worker failed."))

        descriptor = self.artifacts.finalize_output(
            absolute_path=output_path,
            filename="atnos-synthetic-reel.mp4",
            media_type="video/mp4",
        )
        qa = self.render_qc._validate_render_output(descriptor.absolute_path)
        return RenderArtifactResult(
            descriptor=descriptor,
            qa=qa,
            watchdog_timeout_s=self.settings.render_watchdog_timeout_s,
            render_duration_sec=render_duration_sec,
            engine="moviepy-synthetic-motion",
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
                filename="atnos-synthetic-reel.mp4",
                media_type="video/mp4",
            )
            qa = self.render_qc._validate_render_output(
                descriptor.absolute_path,
                extra_warnings=(
                    f"Synthetic motion render exceeded the {self.settings.render_watchdog_timeout_s}s watchdog but a valid output was recovered.",
                ),
            )
        except Exception:
            return None
        return RenderArtifactResult(
            descriptor=descriptor,
            qa=qa,
            watchdog_timeout_s=self.settings.render_watchdog_timeout_s,
            render_duration_sec=render_duration_sec,
            engine="moviepy-synthetic-motion",
            watchdog_recovered=True,
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


def _render_synthetic_motion_sync(payload: _SyntheticMotionPayload) -> None:
    os.environ.setdefault("IMAGEIO_FFMPEG_EXE", imageio_ffmpeg.get_ffmpeg_exe())
    portrait_path = Path(payload.portrait_path)
    talking_head_path = Path(payload.talking_head_path)
    audio_path = Path(payload.audio_path)
    subtitles_path = Path(payload.subtitles_path) if payload.subtitles_path else None
    cutaway_paths = [Path(item) for item in payload.cutaway_paths if item]
    output_path = Path(payload.output_path)
    background_path = output_path.with_name(f"{output_path.stem}-bg.png")

    _prepare_background_image(
        portrait_path=portrait_path,
        output_path=background_path,
        accent_hex=payload.accent_hex,
    )

    audio_clip = AudioFileClip(str(audio_path))
    background_clip = None
    talking_head_source = None
    talking_head_clip = None
    foreground_shadow = None
    foreground_card = None
    style_chip = None
    style_chip_bg = None
    hook_text = None
    hook_bg = None
    subtitle_layers = []
    cutaway_layers = []
    final_clip = None
    try:
        duration = max(0.1, float(audio_clip.duration or 0.0))
        background_clip = ImageClip(str(background_path)).with_duration(duration)

        talking_head_source = VideoFileClip(str(talking_head_path))
        source_duration = float(talking_head_source.duration or 0.0)
        if source_duration > 0:
            duration = min(duration, source_duration)
        if payload.target_duration_cap_sec is not None and payload.target_duration_cap_sec > 0:
            duration = min(duration, float(payload.target_duration_cap_sec))
        background_clip = background_clip.with_duration(duration)

        talking_head_clip = (
            talking_head_source
            .without_audio()
            .with_duration(duration)
            .resized(width=SyntheticMotionCompositorService.FOREGROUND_WIDTH)
            .with_position(("center", SyntheticMotionCompositorService.FOREGROUND_TOP))
        )

        shadow_size = (
            talking_head_clip.w + 60,
            talking_head_clip.h + 60,
        )
        foreground_shadow = (
            ColorClip(size=shadow_size, color=(0, 0, 0))
            .with_opacity(0.26)
            .with_duration(duration)
            .with_position(("center", SyntheticMotionCompositorService.FOREGROUND_TOP - 18))
        )
        foreground_card = (
            ColorClip(size=(talking_head_clip.w + 26, talking_head_clip.h + 26), color=(245, 246, 250))
            .with_opacity(0.1)
            .with_duration(duration)
            .with_position(("center", SyntheticMotionCompositorService.FOREGROUND_TOP - 6))
        )

        chip_duration = duration
        style_chip_bg = (
            ColorClip(size=(420, 56), color=_hex_to_rgb(payload.accent_hex))
            .with_opacity(0.92)
            .with_duration(chip_duration)
            .with_position((72, 88))
        )
        style_chip = TextClip(
            font=str(RenderService._find_font("VeraBd.ttf")) if RenderService._find_font("VeraBd.ttf") is not None else None,
            text=f"{RenderService._display_text(payload.style_label)} / {RenderService._display_text(payload.reel_label)}".upper(),
            font_size=28,
            color="#0b1020",
            duration=chip_duration,
        ).with_position((94, 98))

        hook_cap = payload.hook_duration_cap_sec if payload.hook_duration_cap_sec and payload.hook_duration_cap_sec > 0 else 5.2
        hook_duration = min(hook_cap, max(2.6, duration * 0.34))
        hook_label = RenderService._display_text(payload.hook_text)[:120]
        hook_text = TextClip(
            font=str(RenderService._find_font("VeraBd.ttf")) if RenderService._find_font("VeraBd.ttf") is not None else None,
            text=hook_label,
            font_size=54,
            size=(860, None),
            method="caption",
            color="white",
            stroke_color="#0b1020",
            stroke_width=4,
            text_align="left",
            duration=hook_duration,
        ).with_position((72, 180))
        hook_bg = (
            ColorClip(size=(920, max(160, hook_text.h + 34)), color=(11, 16, 32))
            .with_opacity(0.64)
            .with_duration(hook_duration)
            .with_position((52, 164))
        )

        if subtitles_path is not None and subtitles_path.exists():
            subtitle_layers = _build_subtitle_layers(
                subtitles_path=subtitles_path,
                total_duration=duration,
            )

        cutaway_layers = _build_cutaway_layers(
            cutaway_paths=cutaway_paths,
            total_duration=duration,
            max_cutaways=payload.max_cutaways,
            display_duration_sec=payload.cutaway_display_duration_sec,
        )

        layers = [
            background_clip,
            foreground_shadow,
            foreground_card,
            talking_head_clip,
            *cutaway_layers,
            style_chip_bg,
            style_chip,
            hook_bg,
            hook_text,
            *subtitle_layers,
        ]
        final_clip = CompositeVideoClip(layers, size=SyntheticMotionCompositorService.VIDEO_SIZE).with_audio(audio_clip).with_duration(duration)
        _write_synthetic_motion_video(final_clip, output_path, render_profile=payload.render_profile)
    finally:
        for clip in subtitle_layers:
            try:
                clip.close()
            except Exception:
                pass
        for clip in cutaway_layers:
            try:
                clip.close()
            except Exception:
                pass
        for clip in (hook_text, hook_bg, style_chip, style_chip_bg, foreground_card, foreground_shadow, talking_head_clip, talking_head_source, background_clip, final_clip):
            if clip is None:
                continue
            try:
                clip.close()
            except Exception:
                pass
        audio_clip.close()
        try:
            background_path.unlink(missing_ok=True)
        except OSError:
            pass


def _prepare_background_image(*, portrait_path: Path, output_path: Path, accent_hex: str) -> None:
    accent = _hex_to_rgb(accent_hex)
    portrait = Image.open(portrait_path).convert("RGB")
    fitted = ImageOps.fit(portrait, SyntheticMotionCompositorService.VIDEO_SIZE, method=Image.Resampling.LANCZOS)
    blurred = fitted.filter(ImageFilter.GaussianBlur(radius=28))
    darkened = ImageEnhance.Brightness(blurred).enhance(0.45)
    overlay = Image.new("RGB", SyntheticMotionCompositorService.VIDEO_SIZE, color=(10, 14, 24))
    blended = Image.blend(darkened, overlay, alpha=0.35)
    accent_band = Image.new("RGB", SyntheticMotionCompositorService.VIDEO_SIZE, color=accent)
    blended = Image.blend(blended, accent_band, alpha=0.08)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    blended.save(output_path, format="PNG")


def _write_synthetic_motion_video(final_clip, output_path: Path, *, render_profile: str) -> None:
    attempts = resolve_video_export_attempts(render_profile=render_profile, force_cpu_path=False)
    last_error: Exception | None = None
    for codec, ffmpeg_params in attempts:
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            final_clip.write_videofile(
                str(output_path),
                fps=30,
                codec=codec,
                audio_codec="aac",
                ffmpeg_params=ffmpeg_params,
                logger=None,
            )
            return
        except Exception as exc:
            last_error = exc
    normalized_profile = str(render_profile or "quality").strip().lower()
    raise RuntimeError(f"Synthetic motion export failed for profile {normalized_profile}: {last_error}")


def _build_cutaway_layers(
    *,
    cutaway_paths: list[Path],
    total_duration: float,
    max_cutaways: int | None = None,
    display_duration_sec: float | None = None,
) -> list:
    valid_paths = [path for path in cutaway_paths if path.exists() and path.is_file()]
    if not valid_paths or total_duration <= 4.0:
        return []

    layers: list = []
    limit = max(1, min(len(valid_paths), int(max_cutaways))) if isinstance(max_cutaways, int) and max_cutaways > 0 else min(len(valid_paths), 3)
    display_duration = (
        min(max(0.7, float(display_duration_sec)), max(0.7, total_duration - 0.35))
        if display_duration_sec is not None and display_duration_sec > 0
        else min(1.35, max(0.92, total_duration * 0.085))
    )
    fractions = (0.34, 0.56, 0.74)
    for index, path in enumerate(valid_paths[:limit]):
        start = min(
            max(0.2, total_duration - display_duration - 0.35),
            max(0.2, total_duration * fractions[min(index, len(fractions) - 1)]),
        )
        cutaway_clip = (
            ImageClip(str(path))
            .with_start(start)
            .with_duration(display_duration)
            .resized(height=SyntheticMotionCompositorService.VIDEO_SIZE[1])
            .with_position("center")
        )
        overlay = (
            ColorClip(size=SyntheticMotionCompositorService.VIDEO_SIZE, color=(6, 10, 18))
            .with_opacity(0.18)
            .with_start(start)
            .with_duration(display_duration)
            .with_position((0, 0))
        )
        layers.extend((cutaway_clip, overlay))
    return layers


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    raw = value.strip().lstrip("#")
    if len(raw) != 6:
        return (214, 179, 106)
    return tuple(int(raw[index : index + 2], 16) for index in (0, 2, 4))
