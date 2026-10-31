from __future__ import annotations

from pathlib import Path
import subprocess
import time

from atnos.core.config import Settings
from content.render_ffmpeg import ShortRenderService


class MotionShortRenderService:
    """FFmpeg Ken Burns motion renderer for faceless Shorts scene packs."""

    FPS = 30

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def render_motion(
        self,
        *,
        frame_paths: list[Path],
        audio_path: Path,
        subtitles_path: Path,
        output_dir: Path,
    ) -> tuple[Path, dict[str, object]]:
        if len(frame_paths) < 2:
            return ShortRenderService(self.settings).render(
                frame_paths=frame_paths,
                audio_path=audio_path,
                subtitles_path=subtitles_path,
                output_dir=output_dir,
            )
        if not audio_path.exists():
            raise RuntimeError("Narration audio was not created.")
        if not subtitles_path.exists():
            raise RuntimeError("Subtitles were not created.")

        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / "final.mp4"
        target.unlink(missing_ok=True)
        render_subtitles = ShortRenderService._prepare_subtitles_for_render(
            subtitles_path=subtitles_path,
            output_dir=output_dir,
            download_fonts=self.settings.app_env != "test",
        )
        command = self.build_motion_ffmpeg_command(
            ffmpeg_exe=ShortRenderService(self.settings)._resolve_ffmpeg_exe(),
            frame_paths=frame_paths,
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
                "Motion FFmpeg short render failed: "
                + (completed.stderr or completed.stdout or f"exit code {completed.returncode}")[-2000:]
            )
        qa = ShortRenderService._inspect_output(target)
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
            "engine": "ffmpeg-ken-burns-motion-subtitles",
            "captions": {
                "source": str(subtitles_path),
                "render_file": str(render_subtitles),
                "format": render_subtitles.suffix.lstrip(".").lower(),
            },
            "motion": {
                "fps": self.FPS,
                "scene_count": len(frame_paths),
                "style": "alternating_push_pan_zoom",
            },
            "watchdog_recovered": False,
        }
        return target, metadata

    @classmethod
    def build_motion_ffmpeg_command(
        cls,
        *,
        ffmpeg_exe: str,
        frame_paths: list[Path],
        audio_path: Path,
        subtitles_path: Path,
        output_path: Path,
    ) -> list[str]:
        if len(frame_paths) < 2:
            raise RuntimeError("Motion render requires at least two scene frames.")
        duration = ShortRenderService._wav_duration(audio_path) or 30.0
        per_scene_frames = max(45, int((duration / len(frame_paths)) * cls.FPS))
        command = [ffmpeg_exe, "-y", "-hide_banner", "-loglevel", "error"]
        for frame_path in frame_paths:
            command.extend(["-i", str(frame_path)])
        command.extend(["-i", str(audio_path)])

        filters: list[str] = []
        labels: list[str] = []
        for index, _frame_path in enumerate(frame_paths):
            labels.append(f"[v{index}]")
            filters.append(cls._scene_filter(index=index, frames=per_scene_frames))
        concat_inputs = "".join(labels)
        subtitle_filter = ShortRenderService._subtitles_filter(subtitles_path)
        filters.append(
            f"{concat_inputs}concat=n={len(frame_paths)}:v=1:a=0,"
            f"{subtitle_filter},format=yuv420p[outv]"
        )
        command.extend(
            [
                "-filter_complex",
                ";".join(filters),
                "-map",
                "[outv]",
                "-map",
                f"{len(frame_paths)}:a",
                "-r",
                str(cls.FPS),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
        return command

    @classmethod
    def _scene_filter(cls, *, index: int, frames: int) -> str:
        zoom_expr = f"min(1.0+0.075*on/{frames},1.075)"
        if index % 4 == 0:
            x_expr = "iw/2-(iw/zoom/2)"
            y_expr = "ih/2-(ih/zoom/2)"
        elif index % 4 == 1:
            x_expr = f"(iw-iw/zoom)*on/{frames}"
            y_expr = "ih/2-(ih/zoom/2)"
        elif index % 4 == 2:
            x_expr = f"(iw-iw/zoom)*(1-on/{frames})"
            y_expr = "ih/2-(ih/zoom/2)"
        else:
            x_expr = "iw/2-(iw/zoom/2)"
            y_expr = f"(ih-ih/zoom)*on/{frames}"
        return (
            f"[{index}:v]"
            "scale=1200:2134:force_original_aspect_ratio=increase,"
            "crop=1200:2134,"
            f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':d={frames}:s=1080x1920:fps={cls.FPS},"
            "setsar=1"
            f"[v{index}]"
        )
