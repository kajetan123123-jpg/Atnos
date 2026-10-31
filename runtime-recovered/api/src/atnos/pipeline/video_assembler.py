from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

from atnos.pipeline.scene_renderer import SceneResult
from atnos.production.media_tools import resolve_ffmpeg, resolve_ffprobe

logger = logging.getLogger(__name__)


class AssemblyError(RuntimeError):
    """Final Shorts assembly failed strict media validation."""


class VideoAssembler:
    """Assemble rendered scenes, narration and optional SRT subtitles into short.mp4."""

    def assemble(
        self,
        scenes: list[SceneResult],
        narration_path: Path,
        subtitles_path: Path | None,
        job_dir: Path,
    ) -> Path:
        """Build final short.mp4 through FFmpeg and validate with ffprobe."""
        if not scenes:
            raise AssemblyError("No scenes were provided for assembly.")
        ffmpeg = resolve_ffmpeg()
        if not ffmpeg:
            raise AssemblyError("FFmpeg is unavailable.")
        clips_dir = job_dir / "clips"
        clips_dir.mkdir(parents=True, exist_ok=True)
        clip_paths: list[Path] = []
        for index, scene in enumerate(scenes):
            clip_path = clips_dir / f"scene_{index:02d}.mp4"
            self._scene_to_clip(ffmpeg, scene, clip_path, clips_dir / f"scene_{index:02d}_frames")
            clip_paths.append(clip_path)

        assembled_noaudio = job_dir / "assembled_noaudio.mp4"
        self._concat_clips(ffmpeg, clip_paths, assembled_noaudio)
        assembled_audio = job_dir / "assembled_audio.mp4"
        self._add_audio(ffmpeg, assembled_noaudio, narration_path, assembled_audio)
        final_path = job_dir / "short.mp4"
        if subtitles_path is not None and subtitles_path.exists():
            self._burn_subtitles(ffmpeg, assembled_audio, subtitles_path, final_path)
        else:
            shutil.copyfile(assembled_audio, final_path)
        probe = self._verify(final_path)
        if not probe["ok"]:
            raise AssemblyError(str(probe.get("reason") or "Final MP4 failed ffprobe validation."))
        return final_path

    def _scene_to_clip(self, ffmpeg: str, scene: SceneResult, output_path: Path, temp_dir: Path) -> None:
        duration = max(0.5, float(scene.duration_seconds or 4.0))
        if scene.video_path is not None and scene.video_path.exists():
            cmd = [
                ffmpeg,
                "-y",
                "-stream_loop",
                "-1",
                "-t",
                str(duration),
                "-i",
                str(scene.video_path),
                "-vf",
                "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,format=yuv420p",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "22",
                "-an",
                str(output_path),
            ]
        elif len(scene.frames) <= 1:
            frame = scene.frames[0] if scene.frames else None
            if frame is None or not frame.exists():
                raise AssemblyError(f"Scene {scene.scene_id} has no frame file.")
            frames = max(1, int(round(30 * duration)))
            cmd = [
                ffmpeg,
                "-y",
                "-loop",
                "1",
                "-t",
                str(duration),
                "-i",
                str(frame),
                "-vf",
                (
                    "scale=1080:1920:force_original_aspect_ratio=decrease,"
                    "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,"
                    f"zoompan=z='min(zoom+0.0008,1.25)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1080x1920:fps=30,"
                    "format=yuv420p"
                ),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "22",
                "-pix_fmt",
                "yuv420p",
                "-r",
                "30",
                str(output_path),
            ]
        else:
            temp_dir.mkdir(parents=True, exist_ok=True)
            for frame_index, frame in enumerate(scene.frames):
                shutil.copyfile(frame, temp_dir / f"frame_{frame_index:04d}{frame.suffix.lower() or '.png'}")
            first_suffix = scene.frames[0].suffix.lower() or ".png"
            cmd = [
                ffmpeg,
                "-y",
                "-r",
                "8",
                "-i",
                str(temp_dir / f"frame_%04d{first_suffix}"),
                "-vf",
                "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,format=yuv420p",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "22",
                "-pix_fmt",
                "yuv420p",
                str(output_path),
            ]
        self._run_ffmpeg(cmd, output_path, timeout=180)

    def _concat_clips(self, ffmpeg: str, clip_paths: list[Path], output_path: Path) -> None:
        concat_file = output_path.with_suffix(".concat.txt")
        concat_file.write_text(
            "\n".join(f"file '{_concat_path(path)}'" for path in clip_paths),
            encoding="utf-8",
        )
        cmd = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(output_path)]
        self._run_ffmpeg(cmd, output_path, timeout=120)

    def _add_audio(self, ffmpeg: str, video_path: Path, audio_path: Path, output_path: Path) -> None:
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-b:a",
            "192k",
            "-shortest",
            str(output_path),
        ]
        self._run_ffmpeg(cmd, output_path, timeout=120)

    def _burn_subtitles(self, ffmpeg: str, video_path: Path, srt_path: Path, output_path: Path) -> None:
        style = (
            "FontName=Arial,FontSize=22,PrimaryColour=&HFFFFFF,OutlineColour=&H000000,"
            "BackColour=&H80000000,Bold=1,Outline=2,Shadow=1,Alignment=2,MarginV=80"
        )
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(video_path),
            "-vf",
            f"subtitles='{_filter_path(srt_path)}':force_style='{style}'",
            "-c:a",
            "copy",
            str(output_path),
        ]
        self._run_ffmpeg(cmd, output_path, timeout=120)

    def _run_ffmpeg(self, cmd: list[str], output_path: Path, *, timeout: int) -> None:
        logger.debug("Running FFmpeg: %s", " ".join(cmd))
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise AssemblyError(str(exc)) from exc
        if proc.returncode != 0:
            raise AssemblyError(proc.stderr[-1200:] or f"ffmpeg exited with {proc.returncode}")
        if not output_path.exists() or output_path.stat().st_size <= 1024:
            raise AssemblyError(f"FFmpeg did not create a valid output file: {output_path}")

    def _verify(self, path: Path) -> dict[str, Any]:
        ffprobe = resolve_ffprobe(resolve_ffmpeg())
        if not ffprobe:
            return {"ok": False, "reason": "ffprobe unavailable"}
        try:
            proc = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-print_format",
                    "json",
                    "-show_format",
                    "-show_streams",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "reason": str(exc)}
        if proc.returncode != 0 or not proc.stdout.strip():
            return {"ok": False, "reason": proc.stderr[-800:] or f"ffprobe exited with {proc.returncode}"}
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            return {"ok": False, "reason": f"ffprobe JSON error: {exc}"}
        streams = data.get("streams") if isinstance(data.get("streams"), list) else []
        video = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
        audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
        duration = _float((data.get("format") or {}).get("duration"))
        width = _int(video.get("width"))
        height = _int(video.get("height"))
        ok = bool(video) and bool(audio) and width == 1080 and height == 1920 and duration > 4
        return {
            "ok": ok,
            "width": width,
            "height": height,
            "duration_s": duration,
            "has_audio": bool(audio),
            "reason": "" if ok else "short.mp4 must have 1080x1920 video, audio stream and duration >4s",
        }


def _concat_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", "'\\''")


def _filter_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
