from __future__ import annotations

import subprocess
import time
import wave
from pathlib import Path
import shutil

import imageio_ffmpeg
from moviepy import VideoFileClip

from atnos.core.config import Settings
from atnos.services.render_service import RenderService
from content.caption_style import ensure_caption_fonts, generate_ass_from_srt, subtitle_filter_for_path


class ShortRenderService:
    VIDEO_SIZE = (1080, 1920)

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.service = RenderService(settings)

    def render(
        self,
        *,
        frame_paths: list[Path],
        audio_path: Path,
        subtitles_path: Path,
        output_dir: Path,
    ) -> tuple[Path, dict[str, object]]:
        try:
            return self._render_fast_ffmpeg(
                frame_paths=frame_paths,
                audio_path=audio_path,
                subtitles_path=subtitles_path,
                output_dir=output_dir,
            )
        except Exception:
            # Keep the older MoviePy renderer as a compatibility fallback.
            # The upload quality gate will block recovered watchdog renders.
            pass

        result = self.service.render_short(
            frame_paths=frame_paths,
            audio_path=audio_path,
            subtitles_path=subtitles_path,
            conversation_id=None,
            render_profile="quality",
        )
        target = output_dir / "final.mp4"
        shutil.copyfile(result.descriptor.absolute_path, target)
        return target, result.to_dict()

    def _render_fast_ffmpeg(
        self,
        *,
        frame_paths: list[Path],
        audio_path: Path,
        subtitles_path: Path,
        output_dir: Path,
    ) -> tuple[Path, dict[str, object]]:
        if not frame_paths:
            raise RuntimeError("No frames were provided for video rendering.")
        if not audio_path.exists():
            raise RuntimeError("Narration audio was not created.")
        if not subtitles_path.exists():
            raise RuntimeError("Subtitles were not created.")

        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / "final.mp4"
        target.unlink(missing_ok=True)
        render_subtitles = self._prepare_subtitles_for_render(
            subtitles_path=subtitles_path,
            output_dir=output_dir,
            download_fonts=self.settings.app_env != "test",
        )
        audio_duration = self._media_duration(audio_path) or 30.0
        if len(frame_paths) == 1:
            command = self.build_single_frame_motion_ffmpeg_command(
                ffmpeg_exe=self._resolve_ffmpeg_exe(),
                image_path=frame_paths[0],
                audio_path=audio_path,
                subtitles_path=render_subtitles,
                output_path=target,
                duration_s=audio_duration,
            )
        else:
            command = self.build_ken_burns_slideshow_ffmpeg_command(
                ffmpeg_exe=self._resolve_ffmpeg_exe(),
                frame_paths=frame_paths,
                frame_durations=self._frame_durations(audio_duration, len(frame_paths)),
                audio_path=audio_path,
                subtitles_path=render_subtitles,
                output_path=target,
            )
        started = time.monotonic()
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=max(60, min(int(self.settings.render_watchdog_timeout_s), 240)),
            check=False,
        )
        render_duration_sec = round(time.monotonic() - started, 3)
        if completed.returncode != 0:
            raise RuntimeError(
                "Fast FFmpeg short render failed: "
                + (completed.stderr or completed.stdout or f"exit code {completed.returncode}")[-2000:]
            )
        qa = self._inspect_output(target)
        metadata = {
            "descriptor": {
                "filename": target.name,
                "storage_path": str(target),
                "media_type": "video/mp4",
                "file_extension": ".mp4",
                "size_bytes": target.stat().st_size if target.exists() else 0,
            },
            "qa": qa,
            "watchdog_timeout_s": 0,
            "render_duration_sec": render_duration_sec,
            "engine": "ffmpeg-ken-burns-ass-subtitles",
            "captions": {
                "source": str(subtitles_path),
                "render_file": str(render_subtitles),
                "format": render_subtitles.suffix.lstrip(".").lower(),
            },
            "watchdog_recovered": False,
        }
        return target, metadata

    @classmethod
    def build_single_frame_motion_ffmpeg_command(
        cls,
        *,
        ffmpeg_exe: str,
        image_path: Path,
        audio_path: Path,
        subtitles_path: Path,
        output_path: Path,
        duration_s: float,
    ) -> list[str]:
        subtitle_filter = cls._subtitles_filter(subtitles_path)
        motion_filter = cls._ken_burns_filter(index=0, duration_s=duration_s, fps=30, scene_count=1)
        video_filter = (
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,"
            f"{motion_filter},"
            f"{subtitle_filter},format=yuv420p"
        )
        return [
            ffmpeg_exe,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-loop",
            "1",
            "-i",
            str(image_path),
            "-i",
            str(audio_path),
            "-vf",
            video_filter,
            "-t",
            f"{duration_s:.3f}",
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

    @classmethod
    def build_fast_ffmpeg_command(
        cls,
        *,
        ffmpeg_exe: str,
        image_path: Path,
        audio_path: Path,
        subtitles_path: Path,
        output_path: Path,
    ) -> list[str]:
        subtitle_filter = cls._subtitles_filter(subtitles_path)
        video_filter = (
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,format=yuv420p,"
            f"{subtitle_filter}"
        )
        return [
            ffmpeg_exe,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-loop",
            "1",
            "-framerate",
            "30",
            "-i",
            str(image_path),
            "-i",
            str(audio_path),
            "-vf",
            video_filter,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-tune",
            "stillimage",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

    @classmethod
    def build_fast_slideshow_ffmpeg_command(
        cls,
        *,
        ffmpeg_exe: str,
        concat_file: Path,
        audio_path: Path,
        subtitles_path: Path,
        output_path: Path,
    ) -> list[str]:
        subtitle_filter = cls._subtitles_filter(subtitles_path)
        video_filter = (
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,format=yuv420p,"
            f"{subtitle_filter}"
        )
        return [
            ffmpeg_exe,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-i",
            str(audio_path),
            "-vf",
            video_filter,
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

    @classmethod
    def build_ken_burns_slideshow_ffmpeg_command(
        cls,
        *,
        ffmpeg_exe: str,
        frame_paths: list[Path],
        frame_durations: list[float],
        audio_path: Path,
        subtitles_path: Path,
        output_path: Path,
    ) -> list[str]:
        if len(frame_paths) < 2:
            raise RuntimeError("Ken Burns slideshow requires at least two frames.")
        if len(frame_paths) != len(frame_durations):
            raise RuntimeError("Frame path count must match frame duration count.")

        command = [ffmpeg_exe, "-y", "-hide_banner", "-loglevel", "error"]
        for frame_path in frame_paths:
            command.extend(["-loop", "1", "-i", str(frame_path)])
        command.extend(["-i", str(audio_path)])

        filters: list[str] = []
        for index, duration in enumerate(frame_durations):
            motion_filter = cls._ken_burns_filter(
                index=index,
                duration_s=duration,
                fps=30,
                scene_count=len(frame_paths),
            )
            filters.append(
                f"[{index}:v]"
                "scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920,"
                f"{motion_filter},"
                f"trim=duration={duration:.3f},setpts=PTS-STARTPTS[v{index}]"
            )

        fade_duration = 0.3
        current_label = "[v0]"
        cumulative = frame_durations[0]
        for index in range(1, len(frame_paths)):
            output_label = f"[xf{index}]" if index < len(frame_paths) - 1 else "[vxfaded]"
            offset = max(0.0, cumulative - fade_duration)
            filters.append(
                f"{current_label}[v{index}]"
                f"xfade=transition=fade:duration={fade_duration}:offset={offset:.3f}{output_label}"
            )
            current_label = output_label
            cumulative = cumulative + frame_durations[index] - fade_duration

        subtitle_filter = cls._subtitles_filter(subtitles_path)
        filters.append(f"{current_label}{subtitle_filter},format=yuv420p[outv]")
        command.extend(
            [
                "-filter_complex",
                ";".join(filters),
                "-map",
                "[outv]",
                "-map",
                f"{len(frame_paths)}:a",
                "-r",
                "30",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
        return command

    def _resolve_ffmpeg_exe(self) -> str:
        configured = (self.settings.ffmpeg_exe or "").strip()
        if configured:
            return configured
        return imageio_ffmpeg.get_ffmpeg_exe()

    @classmethod
    def _write_concat_file(cls, *, frame_paths: list[Path], audio_path: Path, output_dir: Path) -> Path:
        if len(frame_paths) < 2:
            raise RuntimeError("Slideshow render requires at least two frames.")
        duration = cls._wav_duration(audio_path) or 30.0
        per_frame = max(1.4, duration / len(frame_paths))
        concat_file = output_dir / "frames_concat.txt"
        lines: list[str] = []
        for frame_path in frame_paths:
            escaped = frame_path.resolve().as_posix().replace("'", r"'\''")
            lines.append(f"file '{escaped}'")
            lines.append(f"duration {per_frame:.3f}")
        escaped_last = frame_paths[-1].resolve().as_posix().replace("'", r"'\''")
        lines.append(f"file '{escaped_last}'")
        concat_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return concat_file

    @classmethod
    def _frame_durations(cls, total_duration_s: float, frame_count: int) -> list[float]:
        per_frame = max(1.4, total_duration_s / max(1, frame_count))
        return [per_frame for _ in range(frame_count)]

    @classmethod
    def _ken_burns_filter(cls, *, index: int, duration_s: float, fps: int, scene_count: int) -> str:
        total_frames = max(1, int(duration_s * fps))
        variants = (
            f"zoompan=z='min(zoom+0.0015,1.3)':x='iw/2-(iw/zoom/2)+on*(iw/zoom/360)':y='ih/2-(ih/zoom/2)':d={total_frames}:s=1080x1920:fps={fps}",
            f"zoompan=z='if(lte(zoom,1.0),1.3,max(zoom-0.0015,1.0))':x='iw/2-(iw/zoom/2)-on*(iw/zoom/480)':y='ih/2-(ih/zoom/2)':d={total_frames}:s=1080x1920:fps={fps}",
            f"zoompan=z='min(zoom+0.001,1.25)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={total_frames}:s=1080x1920:fps={fps}",
            f"zoompan=z='1.15':x='iw/2-(iw/zoom/2)':y='max(0,ih/2-(ih/zoom/2)-on*(ih/zoom/600))':d={total_frames}:s=1080x1920:fps={fps}",
            f"zoompan=z='1.15':x='iw/2-(iw/zoom/2)':y='min(ih*(1-1/zoom),ih/2-(ih/zoom/2)+on*(ih/zoom/600))':d={total_frames}:s=1080x1920:fps={fps}",
            f"zoompan=z='min(zoom+0.0012,1.2)':x='max(0,iw/2-(iw/zoom/2)-on*(iw/zoom/700))':y='max(0,ih/2-(ih/zoom/2)-on*(ih/zoom/700))':d={total_frames}:s=1080x1920:fps={fps}",
        )
        filters = [variants[index % len(variants)], "setsar=1"]
        if index == 0:
            filters.append("fade=t=in:st=0:d=0.5")
        if index == scene_count - 1:
            fade_start = max(0.0, duration_s - 0.5)
            filters.append(f"fade=t=out:st={fade_start:.3f}:d=0.5")
        return ",".join(filters)

    @staticmethod
    def _wav_duration(path: Path) -> float | None:
        try:
            with wave.open(str(path), "rb") as handle:
                rate = handle.getframerate()
                frames = handle.getnframes()
                if rate <= 0:
                    return None
                return frames / float(rate)
        except Exception:
            return None

    def _media_duration(self, path: Path) -> float | None:
        duration = self._wav_duration(path)
        if duration is not None:
            return duration
        return None

    @staticmethod
    def _prepare_subtitles_for_render(
        *,
        subtitles_path: Path,
        output_dir: Path,
        download_fonts: bool = True,
    ) -> Path:
        if subtitles_path.suffix.casefold() == ".ass":
            return subtitles_path
        fonts_dir = ensure_caption_fonts(download_missing=download_fonts)
        ass_path = output_dir / "subtitles.ass"
        try:
            return generate_ass_from_srt(srt_path=subtitles_path, output_path=ass_path, fonts_dir=fonts_dir)
        except Exception:
            return subtitles_path

    @classmethod
    def _subtitles_filter(cls, subtitles_path: Path) -> str:
        return subtitle_filter_for_path(subtitles_path)

    @staticmethod
    def _escape_filter_path(path: Path) -> str:
        value = path.resolve().as_posix()
        value = value.replace("\\", "/")
        value = value.replace(":", r"\:")
        value = value.replace("'", r"\'")
        return value

    @staticmethod
    def _inspect_output(path: Path) -> dict[str, object]:
        if not path.exists() or path.stat().st_size <= 0:
            raise RuntimeError("Rendered output is missing or empty.")
        clip = VideoFileClip(str(path))
        try:
            duration = float(clip.duration or 0.0)
            width = int(clip.w or 0)
            height = int(clip.h or 0)
            has_audio = bool(clip.audio is not None and float(clip.audio.duration or 0.0) > 0.1)
        finally:
            clip.close()
        warnings: list[str] = []
        if (width, height) != ShortRenderService.VIDEO_SIZE:
            warnings.append(f"Expected 1080x1920, got {width}x{height}.")
        if duration < 3.0:
            warnings.append("Rendered short is unusually short.")
        if not has_audio:
            warnings.append("Rendered short has no detectable audio track.")
        return {
            "duration_sec": round(duration, 3),
            "width": width,
            "height": height,
            "has_audio": has_audio,
            "size_bytes": path.stat().st_size,
            "warnings": warnings,
        }
