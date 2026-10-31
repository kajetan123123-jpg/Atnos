from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from atnos.production.steps.assembler import (
    _ass_filter,
    _ffmpeg_exe,
    _probe_duration,
    _probe_final_video,
    _read_srt_segments,
    ensure_fonts,
    generate_ass_subtitles,
)


@dataclass(frozen=True)
class RemasterScene:
    scene_id: str
    clip_path: Path
    source_duration_s: float
    target_duration_s: float


def load_remaster_scenes(video_manifest_path: Path, timestamps_path: Path) -> list[RemasterScene]:
    video_manifest = _read_json(video_manifest_path)
    timestamps = _read_json(timestamps_path).get("timestamps")
    clips = video_manifest.get("clips")
    if not isinstance(clips, list) or not isinstance(timestamps, list):
        raise ValueError("Video manifest and voice timestamps must contain scene lists")
    if len(clips) != len(timestamps) or not clips:
        raise ValueError(f"Scene count mismatch: {len(clips)} clips and {len(timestamps)} voice segments")

    scenes: list[RemasterScene] = []
    for index, (clip, timestamp) in enumerate(zip(clips, timestamps, strict=True), start=1):
        clip_path = Path(str(clip.get("file") or ""))
        start = float(timestamp.get("start_sec") or 0.0)
        end = float(timestamp.get("end_sec") or 0.0)
        target_duration = end - start
        manifest_duration = float(clip.get("duration_s") or 0.0)
        if not clip_path.is_file():
            raise FileNotFoundError(f"Missing scene {index} clip: {clip_path}")
        measured_duration = _probe_duration(clip_path)
        source_duration = measured_duration if measured_duration > 0 else manifest_duration
        if source_duration <= 0 or target_duration <= 0:
            raise ValueError(f"Scene {index} has a non-positive duration")
        scenes.append(
            RemasterScene(
                scene_id=str(timestamp.get("segment_id") or clip.get("scene_id") or index),
                clip_path=clip_path,
                source_duration_s=source_duration,
                target_duration_s=target_duration,
            )
        )
    return scenes


def build_visual_command(ffmpeg: str, scenes: list[RemasterScene], output_path: Path) -> list[str]:
    if not scenes:
        raise ValueError("At least one scene is required")
    command = [ffmpeg, "-y"]
    for scene in scenes:
        command.extend(["-i", str(scene.clip_path)])

    filters: list[str] = []
    labels: list[str] = []
    for index, scene in enumerate(scenes):
        speed = scene.target_duration_s / scene.source_duration_s
        label = f"v{index}"
        labels.append(f"[{label}]")
        filters.append(
            f"[{index}:v]trim=duration={scene.source_duration_s:.6f},"
            f"setpts=PTS-STARTPTS,setpts={speed:.9f}*PTS,"
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,fps=30,setsar=1,format=yuv420p[{label}]"
        )
    filters.append(f"{''.join(labels)}concat=n={len(scenes)}:v=1:a=0[joined]")
    filters.append("[joined]tpad=stop_mode=clone:stop_duration=1[outv]")
    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[outv]",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    return command


def build_stabilized_visual_command(ffmpeg: str, scenes: list[RemasterScene], output_path: Path) -> list[str]:
    """Build a CFR visual track from stable source frames instead of stretching unstable AI motion."""
    if not scenes:
        raise ValueError("At least one scene is required")
    command = [ffmpeg, "-y"]
    for scene in scenes:
        command.extend(["-i", str(scene.clip_path)])

    filters: list[str] = []
    labels: list[str] = []
    for index, scene in enumerate(scenes):
        frame_count = max(1, round(scene.target_duration_s * 30))
        # Avoid the first frames, where generated clips commonly contain exposure settling.
        sample_time = min(0.35, max(0.0, scene.source_duration_s / 3.0))
        zoom_denominator = max(1, frame_count - 1)
        label = f"v{index}"
        labels.append(f"[{label}]")
        filters.append(
            f"[{index}:v]trim=start={sample_time:.6f}:end={sample_time + (1 / 30):.6f},"
            "setpts=PTS-STARTPTS,"
            "scale=4320:7680:force_original_aspect_ratio=increase,crop=4320:7680,"
            f"zoompan=z='1+0.035*on/{zoom_denominator}':x='iw/2-(iw/zoom/2)':"
            f"y='ih/2-(ih/zoom/2)':d={frame_count}:s=1080x1920:fps=30,"
            f"setsar=1,format=yuv420p[{label}]"
        )
    filters.append(f"{''.join(labels)}concat=n={len(scenes)}:v=1:a=0[joined]")
    filters.append("[joined]fps=30,tpad=stop_mode=clone:stop_duration=1[outv]")
    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[outv]",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "30",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    return command


def build_clean_final_command(
    ffmpeg: str,
    *,
    silent_video: Path,
    narration: Path,
    subtitles: Path,
    fonts_dir: Path,
    output_path: Path,
) -> list[str]:
    """Encode once without re-mastering an already normalized narration."""
    video_filter = (
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,fps=30,format=yuv420p,"
        f"{_ass_filter(subtitles, fonts_dir)}"
    )
    return [
        ffmpeg,
        "-y",
        "-i",
        str(silent_video),
        "-i",
        str(narration),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-vf",
        video_filter,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-af",
        "aresample=48000",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def remaster_existing_short(
    *,
    source_job_dir: Path,
    voice_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    ffmpeg = _ffmpeg_exe()
    if not ffmpeg:
        raise RuntimeError("FFmpeg is unavailable")

    video_manifest_path = source_job_dir / "video_manifest.json"
    timestamps_path = voice_dir / "audio_timestamps.json"
    audio_path = voice_dir / "audio" / "narration.wav"
    subtitles_path = voice_dir / "audio" / "subtitles.srt"
    for required in (video_manifest_path, timestamps_path, audio_path, subtitles_path):
        if not required.is_file():
            raise FileNotFoundError(f"Missing remaster input: {required}")

    scenes = load_remaster_scenes(video_manifest_path, timestamps_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = output_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    copied_subtitles = assets_dir / "subtitles.srt"
    shutil.copyfile(subtitles_path, copied_subtitles)
    ass_path = assets_dir / "subtitles.ass"
    generate_ass_subtitles(_read_srt_segments(copied_subtitles), ass_path)

    silent_path = assets_dir / "visual_master_silent.mp4"
    visual_command = build_stabilized_visual_command(ffmpeg, scenes, silent_path)
    visual_process = subprocess.run(visual_command, capture_output=True, text=True, timeout=900, check=False)
    if visual_process.returncode != 0 or not silent_path.is_file():
        raise RuntimeError(f"Visual remaster failed: {visual_process.stderr[-1600:]}")

    final_path = assets_dir / "final_short.mp4"
    fonts_dir = ensure_fonts()
    final_command = build_clean_final_command(
        ffmpeg,
        silent_video=silent_path,
        narration=audio_path,
        subtitles=ass_path,
        fonts_dir=fonts_dir,
        output_path=final_path,
    )
    final_process = subprocess.run(final_command, capture_output=True, text=True, timeout=900, check=False)
    final_probe = _probe_final_video(final_path)
    if final_process.returncode != 0 or not final_probe.get("ok"):
        raise RuntimeError(f"Final render failed: {final_process.stderr[-1600:]}")
    render = {
        "ok": True,
        "command": final_command,
        "probe": final_probe,
        "audio_policy": "preserve_normalized_narration_no_second_mastering",
    }

    expected_duration = sum(scene.target_duration_s for scene in scenes)
    probe = _probe_final_video(final_path)
    duration_delta = abs(float(probe.get("duration_s") or 0.0) - expected_duration)
    black_frames = _black_frame_intervals(ffmpeg, final_path)
    contact_sheet = assets_dir / "qa_contact_sheet.jpg"
    _write_contact_sheet(ffmpeg, final_path, contact_sheet)
    checks = {
        "upload_format": bool(probe.get("ok")),
        "duration_matches_voice": duration_delta <= 0.2,
        "no_black_frames": not black_frames,
        "scene_count_matches_voice": len(scenes) == len(_read_json(timestamps_path).get("timestamps") or []),
        "no_transition_filters": "xfade" not in " ".join(visual_command) and "fade=" not in " ".join(visual_command),
        "stable_frame_motion": "zoompan=" in " ".join(visual_command),
    }
    report = {
        "status": "verified" if all(checks.values()) else "failed",
        "source_job": str(source_job_dir),
        "voice_source": str(audio_path),
        "output_file": str(final_path),
        "contact_sheet": str(contact_sheet),
        "scene_count": len(scenes),
        "expected_duration_s": round(expected_duration, 3),
        "actual_duration_s": round(float(probe.get("duration_s") or 0.0), 3),
        "checks": checks,
        "black_frame_intervals": black_frames,
        "transition_policy": "stable_frame_motion_with_hard_cuts_no_crossfade_no_flash",
        "scene_timing": [
            {
                "scene_id": scene.scene_id,
                "clip": str(scene.clip_path),
                "source_duration_s": scene.source_duration_s,
                "target_duration_s": scene.target_duration_s,
            }
            for scene in scenes
        ],
        "probe": probe,
        "render": render,
    }
    report_path = output_dir / "remaster_qa.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _black_frame_intervals(ffmpeg: str, video_path: Path) -> list[str]:
    command = [ffmpeg, "-hide_banner", "-i", str(video_path), "-vf", "blackdetect=d=0.12:pix_th=0.08", "-an", "-f", "null", "-"]
    process = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
    return re.findall(r"black_start:[^\r\n]+", process.stderr)


def _write_contact_sheet(ffmpeg: str, video_path: Path, output_path: Path) -> None:
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-vf",
        "fps=1/4,scale=270:480:flags=lanczos,tile=4x3",
        "-frames:v",
        "1",
        str(output_path),
    ]
    process = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
    if process.returncode != 0:
        output_path.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return payload
