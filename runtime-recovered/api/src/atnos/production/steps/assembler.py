from __future__ import annotations

import asyncio
import shutil
import subprocess
import urllib.request
import os
import json
from pathlib import Path
from typing import Any

from atnos.core.config import Settings
from atnos.core.paths import atnos_state_dir
from atnos.production.media_tools import resolve_ffprobe
from atnos.production.models import ProductionContext, StepResult
from atnos.production.project_manifest import (
    MANIFEST_FILENAME,
    deterministic_hash,
    file_sha256,
    persist_context_manifest,
    save_project_manifest,
)
from atnos.production.reference_profile import load_reference_shorts_profile
from atnos.production.remotion_renderer import REMOTION_RENDERER_VERSION, render_project_manifest
from atnos.production.steps.common import write_json

FONTS_DIR = atnos_state_dir() / "fonts"
REQUIRED_FONTS = {
    "Montserrat-Bold.ttf": "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-Bold.ttf",
    "Montserrat-ExtraBold.ttf": "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-ExtraBold.ttf",
}
ASS_SUBTITLE_FONT_SIZE = 132
ASS_SUBTITLE_MAX_WORDS = 4
ASS_SUBTITLE_MAX_DURATION_S = 1.5
ASS_SUBTITLE_MIN_DURATION_S = 0.28
AUDIO_MIX_FILTER_TEMPLATE = (
    "highpass=f=80,"
    "equalizer=f=3000:t=q:w=1.2:g=2,"
    "acompressor=threshold=0.1:ratio=3:attack=5:release=50,"
    "loudnorm=I={target_lufs}:TP=-1.5:LRA=11"
)


async def run(settings: Settings, context: ProductionContext) -> StepResult:
    script = context.artifacts["script"]
    visuals = context.artifacts["visuals"]
    video = context.artifacts["video"]
    assets_dir = context.artifact_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    final_video = assets_dir / "final_short.mp4"
    voice_subtitle_path = context.artifacts["voice"].get("subtitle_path")
    subtitle_path = assets_dir / "subtitles.srt"
    subtitle_segments: list[dict[str, Any]] | None = None
    if voice_subtitle_path and str(voice_subtitle_path).endswith(".srt"):
        source_subtitles = Path(str(voice_subtitle_path))
        if source_subtitles.exists():
            shutil.copyfile(source_subtitles, subtitle_path)
            subtitle_segments = _read_srt_segments(subtitle_path)
        else:
            _write_srt(script.get("segments", []), subtitle_path)
    else:
        _write_srt(script.get("segments", []), subtitle_path)
    fonts_dir = ensure_fonts()
    reference_profile = load_reference_shorts_profile(settings)
    target_lufs = reference_profile.target_lufs()
    ass_subtitle_path = assets_dir / "subtitles.ass"
    generate_ass_subtitles(
        subtitle_segments or _subtitle_segments(script.get("segments", []), context.artifacts["voice"].get("timestamps")),
        ass_subtitle_path,
    )
    manifest = await asyncio.to_thread(
        persist_context_manifest,
        context,
        current_step="assembly",
        status="rendering",
    )
    remotion_render: dict[str, Any] | None = None
    video_backend = str((video.get("backend") or {}).get("mode") or "").strip().lower()
    use_remotion_renderer = manifest.channel == "wealth_blueprint" and video_backend != "motion_graphic"
    if use_remotion_renderer:
        manifest = await asyncio.to_thread(
            save_project_manifest,
            context.artifact_dir / MANIFEST_FILENAME,
            manifest.model_copy(update={"renderer_version": REMOTION_RENDERER_VERSION}),
        )
        remotion_path = assets_dir / "renderer_silent.mp4"
        remotion_render = await asyncio.to_thread(
            render_project_manifest,
            context.artifact_dir / MANIFEST_FILENAME,
            remotion_path,
        )
        render_status = await asyncio.to_thread(
            _mux_remotion_video,
            raw_video_path=remotion_path,
            audio_path=context.artifacts["voice"]["output_file"],
            output_path=final_video,
            target_lufs=target_lufs,
        )
        if not render_status["ok"]:
            raise RuntimeError(f"Remotion final mux failed: {render_status.get('reason') or 'unknown error'}")
    else:
        render_status = await asyncio.to_thread(
            _render_final_video,
            raw_video_path=video["video_raw_path"],
            audio_path=context.artifacts["voice"]["output_file"],
            output_path=final_video,
            subtitle_path=ass_subtitle_path,
            fonts_dir=fonts_dir,
            target_lufs=target_lufs,
        )
    renderer_version = REMOTION_RENDERER_VERSION if remotion_render else "legacy-ffmpeg-v1"
    assembly_provenance = (
        _build_remotion_assembly_provenance(
            remotion_render=remotion_render,
            render_status=render_status,
            audio_path=Path(context.artifacts["voice"]["output_file"]),
            final_path=final_video,
        )
        if remotion_render
        else None
    )
    if remotion_render and not assembly_provenance["verified"]:
        final_video.unlink(missing_ok=True)
        raise RuntimeError("Remotion assembly provenance verification failed")
    payload = {
        "status": "rendered" if render_status["ok"] else "render_failed",
        "format": {"width": 1080, "height": 1920, "fps": 30, "max_duration_s": 60},
        "audio_file": context.artifacts["voice"]["output_file"],
        "raw_video_file": remotion_render["output_path"] if remotion_render else video["video_raw_path"],
        "remotion_render_path": remotion_render["output_path"] if remotion_render else None,
        "renderer_version": renderer_version,
        "renderer": remotion_render,
        "assembly_provenance": assembly_provenance,
        "output_file": str(final_video),
        "subtitle_file": str(subtitle_path),
        "burned_subtitle_file": str(ass_subtitle_path),
        "subtitle_fonts_dir": str(fonts_dir),
        "subtitle_mode": "remotion_kinetic" if remotion_render else "ass_utf8_montserrat",
        "subtitle_style": {
            "font": "Montserrat ExtraBold",
            "font_size": ASS_SUBTITLE_FONT_SIZE,
            "max_words_per_cue": ASS_SUBTITLE_MAX_WORDS,
            "max_duration_s": ASS_SUBTITLE_MAX_DURATION_S,
            "position": "lower_third_center",
            "fade_ms": {"in": 80, "out": 60},
            "outline_px": 6,
            "frame_px": {"width": 1080, "height": 1920},
            "safe_area_px": {"left": 72, "right": 72, "top": 96, "bottom": 220},
        },
        "subtitle_source": context.artifacts["voice"].get("subtitle_status", {}),
        "motion": "remotion_manifest_v1" if remotion_render else "ltx_video_or_placeholder",
        "reference_profile": reference_profile.to_payload(),
        "ffmpeg_plan": {
            "strategy": "remotion_visual_ffmpeg_audio_mux" if remotion_render else "single_complex_filtergraph",
            "inputs": [
                remotion_render["output_path"] if remotion_render else video["video_raw_path"],
                context.artifacts["voice"]["output_file"],
            ],
            "filters": (
                [AUDIO_MIX_FILTER_TEMPLATE.format(target_lufs=target_lufs)]
                if remotion_render
                else [
                    "scale=1080:1920:force_original_aspect_ratio=increase",
                    "crop=1080:1920",
                    "fps=30",
                    "subtitles=generated_ass_subtitles",
                    AUDIO_MIX_FILTER_TEMPLATE.format(target_lufs=target_lufs),
                ]
            ),
            "output_codec": {
                "video": "copy(h264)" if remotion_render else "libx264",
                "audio": "aac",
                "pix_fmt": "yuv420p",
            },
        },
        "timeline": [
            {
                "scene_id": scene["scene_id"],
                "duration_s": scene["duration_s"],
                "headline": scene["headline"],
                "visual_prompt": scene["visual_prompt"],
            }
            for scene in visuals.get("scenes", [])
        ],
        "estimated_duration_s": script.get("estimated_duration_s", 45),
        "render_required": not render_status["ok"],
        "render": render_status,
    }
    return write_json(context.artifact_dir / "assembly_manifest.json", payload)


def _mux_remotion_video(
    *,
    raw_video_path: str | Path,
    audio_path: str | Path,
    output_path: str | Path,
    target_lufs: float = -16.0,
) -> dict[str, Any]:
    """Mux a verified Remotion visual with normalized narration without altering its frames."""
    ffmpeg = _ffmpeg_exe()
    if not ffmpeg:
        return {"ok": False, "reason": "ffmpeg unavailable"}
    output = Path(output_path)
    output.unlink(missing_ok=True)
    video_duration = _probe_duration(Path(raw_video_path))
    audio_duration = _probe_duration(Path(audio_path))
    # -shortest makes the muxed timeline follow the shorter stream; validate against that.
    expected_duration = min(d for d in (video_duration, audio_duration) if d > 0) if (video_duration > 0 or audio_duration > 0) else 0.0
    timeout_s = max(180, min(900, int((max(audio_duration, video_duration) or 60.0) * 6 + 120)))
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(raw_video_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-af",
        AUDIO_MIX_FILTER_TEMPLATE.format(target_lufs=target_lufs),
        "-ar",
        "48000",
        "-ac",
        "2",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output),
    ]
    try:
        process = subprocess.run(command, capture_output=True, text=True, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired as exc:
        output.unlink(missing_ok=True)
        return {"ok": False, "reason": f"ffmpeg timed out after {timeout_s}s: {exc}"}
    except (OSError, subprocess.SubprocessError) as exc:
        output.unlink(missing_ok=True)
        return {"ok": False, "reason": str(exc)}
    probe = _probe_final_video(output)
    if probe.get("checks") and expected_duration > 0:
        output_duration = float(probe.get("duration_s") or 0.0)
        duration_delta = output_duration - expected_duration
        duration_matches_audio = -0.15 <= duration_delta <= 0.75
        probe["checks"]["duration_matches_audio"] = duration_matches_audio
        probe["audio_duration_s"] = round(audio_duration, 3)
        probe["video_duration_s"] = round(video_duration, 3)
        probe["expected_duration_s"] = round(expected_duration, 3)
        probe["duration_delta_s"] = round(duration_delta, 3)
        probe["ok"] = all(bool(value) for value in probe["checks"].values())
        if not probe["ok"]:
            probe["reason"] = "final MP4 failed upload-readiness probe"
    raw_video_hash = file_sha256(Path(raw_video_path))
    audio_input_hash = file_sha256(Path(audio_path))
    final_output_hash = file_sha256(output)
    raw_stream_hash = _video_stream_sha256(Path(raw_video_path))
    final_stream_hash = _video_stream_sha256(output)
    stream_copy_verified = bool(raw_stream_hash) and raw_stream_hash == final_stream_hash
    ok = (
        process.returncode == 0
        and output.is_file()
        and output.stat().st_size > 4096
        and probe["ok"]
        and stream_copy_verified
    )
    if not ok:
        output.unlink(missing_ok=True)
    return {
        "ok": ok,
        "ffmpeg": ffmpeg,
        "command": command,
        "audio_filter": AUDIO_MIX_FILTER_TEMPLATE.format(target_lufs=target_lufs),
        "probe": probe,
        "timeout_s": timeout_s,
        "video_mode": "stream_copy",
        "raw_video_sha256": raw_video_hash,
        "audio_input_sha256": audio_input_hash,
        "final_output_sha256": final_output_hash,
        "raw_video_stream_sha256": raw_stream_hash,
        "final_video_stream_sha256": final_stream_hash,
        "video_stream_copy_verified": stream_copy_verified,
        "reason": "" if ok else _render_failure_reason(process, probe),
    }


def _build_remotion_assembly_provenance(
    *,
    remotion_render: dict[str, Any],
    render_status: dict[str, Any],
    audio_path: Path,
    final_path: Path,
) -> dict[str, Any]:
    raw_path = Path(str(remotion_render.get("output_path") or "")).expanduser().resolve()
    final_resolved = final_path.expanduser().resolve()
    marker = (
        remotion_render.get("renderer")
        if isinstance(remotion_render.get("renderer"), dict)
        else {}
    )
    core = {
        "strategy": "remotion_visual_ffmpeg_audio_mux",
        "renderer_version": str(remotion_render.get("renderer_version") or ""),
        "renderer_manifest_content_hash": str(
            remotion_render.get("manifest_content_hash") or ""
        ),
        "raw_video_path": str(raw_path),
        "raw_video_sha256": file_sha256(raw_path),
        "raw_renderer_sha256": str(remotion_render.get("output_sha256") or ""),
        "audio_input_path": str(audio_path.expanduser().resolve()),
        "audio_input_sha256": file_sha256(audio_path),
        "final_output_path": str(final_resolved),
        "final_output_sha256": file_sha256(final_resolved),
        "raw_video_stream_sha256": str(render_status.get("raw_video_stream_sha256") or ""),
        "final_video_stream_sha256": str(render_status.get("final_video_stream_sha256") or ""),
        "video_stream_copy_verified": bool(render_status.get("video_stream_copy_verified")),
    }
    verified = (
        bool(core["raw_video_sha256"])
        and core["raw_video_sha256"] == core["raw_renderer_sha256"]
        and core["raw_video_sha256"] == marker.get("output_sha256")
        and core["raw_video_path"]
        == str(Path(str(marker.get("output") or "")).expanduser().resolve())
        and core["renderer_version"] == marker.get("renderer_version")
        and core["renderer_manifest_content_hash"] == marker.get("manifest_content_hash")
        and core["raw_video_sha256"] == render_status.get("raw_video_sha256")
        and bool(core["audio_input_sha256"])
        and core["audio_input_sha256"] == render_status.get("audio_input_sha256")
        and bool(core["final_output_sha256"])
        and core["final_output_sha256"] == render_status.get("final_output_sha256")
        and bool(core["raw_video_stream_sha256"])
        and core["raw_video_stream_sha256"] == core["final_video_stream_sha256"]
        and core["video_stream_copy_verified"]
    )
    return {
        **core,
        "chain_hash": deterministic_hash(core),
        "verified": verified,
    }


def verify_remotion_assembly_provenance(
    assembly: dict[str, Any],
    final_path: Path,
) -> dict[str, Any]:
    provenance = assembly.get("assembly_provenance")
    if not isinstance(provenance, dict):
        return {"verified": False, "reason": "assembly_provenance missing"}
    core = {key: value for key, value in provenance.items() if key not in {"chain_hash", "verified"}}
    raw_path = Path(str(core.get("raw_video_path") or "")).expanduser().resolve()
    audio_path = Path(str(core.get("audio_input_path") or "")).expanduser().resolve()
    expected_final = Path(str(core.get("final_output_path") or "")).expanduser().resolve()
    checks = {
        "chain_hash": provenance.get("chain_hash") == deterministic_hash(core),
        "raw_video_file": file_sha256(raw_path) == core.get("raw_video_sha256"),
        "renderer_output": core.get("raw_video_sha256") == core.get("raw_renderer_sha256"),
        "audio_input_file": file_sha256(audio_path) == core.get("audio_input_sha256"),
        "final_output_path": expected_final == final_path.expanduser().resolve(),
        "final_output_file": file_sha256(final_path) == core.get("final_output_sha256"),
        "raw_video_stream": _video_stream_sha256(raw_path)
        == core.get("raw_video_stream_sha256"),
        "final_video_stream": _video_stream_sha256(final_path)
        == core.get("final_video_stream_sha256"),
        "video_stream_copy": bool(core.get("raw_video_stream_sha256"))
        and core.get("raw_video_stream_sha256") == core.get("final_video_stream_sha256")
        and core.get("video_stream_copy_verified") is True,
    }
    failed = [name for name, passed in checks.items() if not passed]
    return {
        "verified": not failed,
        "reason": "" if not failed else f"failed checks: {', '.join(failed)}",
        "checks": checks,
        "chain_hash": provenance.get("chain_hash"),
    }


def _video_stream_sha256(path: Path) -> str | None:
    ffmpeg = _ffmpeg_exe()
    if not ffmpeg or not path.is_file():
        return None
    try:
        process = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-c",
                "copy",
                "-f",
                "hash",
                "-hash",
                "sha256",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if process.returncode != 0:
        return None
    line = next(
        (item.strip() for item in process.stdout.splitlines() if item.startswith("SHA256=")),
        "",
    )
    digest = line.partition("=")[2].strip().casefold()
    return digest if len(digest) == 64 and all(char in "0123456789abcdef" for char in digest) else None


def _render_final_video(
    *,
    raw_video_path: str,
    audio_path: str,
    output_path,
    subtitle_path: Path | None = None,
    fonts_dir: Path | None = None,
    target_lufs: float = -16.0,
) -> dict:
    ffmpeg = _ffmpeg_exe()
    if not ffmpeg:
        return {"ok": False, "reason": "ffmpeg unavailable"}
    output_path = Path(output_path)
    try:
        output_path.unlink(missing_ok=True)
    except OSError:
        pass
    video_filter = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,format=yuv420p"
    if subtitle_path is not None and subtitle_path.exists():
        video_filter += f",{_ass_filter(subtitle_path, fonts_dir)}"
    cmd = [
        ffmpeg,
        "-y",
        "-stream_loop",
        "-1",
        "-i",
        str(raw_video_path),
        "-i",
        str(audio_path),
        "-vf",
        video_filter,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-c:a",
        "aac",
        "-af",
        AUDIO_MIX_FILTER_TEMPLATE.format(target_lufs=target_lufs),
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
    audio_duration = _probe_duration(Path(str(audio_path)))
    timeout_s = max(180, min(900, int((audio_duration or 60.0) * 6 + 120)))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired as exc:
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass
        return {"ok": False, "reason": f"ffmpeg timed out after {timeout_s}s: {exc}"}
    except (OSError, subprocess.SubprocessError) as exc:
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass
        return {"ok": False, "reason": str(exc)}
    probe = _probe_final_video(Path(output_path))
    ok = proc.returncode == 0 and output_path.exists() and output_path.stat().st_size > 4096 and probe["ok"]
    return {
        "ok": ok,
        "ffmpeg": ffmpeg,
        "command": cmd,
        "video_filter": video_filter,
        "audio_filter": AUDIO_MIX_FILTER_TEMPLATE.format(target_lufs=target_lufs),
        "probe": probe,
        "timeout_s": timeout_s,
        "subtitle_filter": _ass_filter(subtitle_path, fonts_dir) if subtitle_path is not None else "",
        "reason": "" if ok else _render_failure_reason(proc, probe),
    }


def _probe_duration(path: Path) -> float:
    ffprobe = resolve_ffprobe(_ffmpeg_exe())
    if not ffprobe or not path.exists():
        return 0.0
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return 0.0
    if proc.returncode != 0:
        return 0.0
    try:
        return max(0.0, float(proc.stdout.strip()))
    except (TypeError, ValueError):
        return 0.0


def _ffmpeg_exe() -> str | None:
    from atnos.production.media_tools import resolve_ffmpeg

    return resolve_ffmpeg()


def _probe_final_video(path: Path) -> dict[str, Any]:
    """Validate the final upload file, not just file existence."""
    if not path.exists() or path.stat().st_size <= 4096:
        return {"ok": False, "reason": "final MP4 missing or too small"}
    ffprobe = resolve_ffprobe(_ffmpeg_exe())
    if not ffprobe:
        return {"ok": False, "reason": "ffprobe unavailable"}
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "reason": str(exc)}
    if proc.returncode != 0 or not proc.stdout.strip():
        return {"ok": False, "reason": proc.stderr[-1000:] if proc.stderr else f"ffprobe exit {proc.returncode}"}
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return {"ok": False, "reason": f"ffprobe returned invalid JSON: {exc}"}
    streams = data.get("streams") if isinstance(data.get("streams"), list) else []
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
    width = _int(video.get("width"))
    height = _int(video.get("height"))
    fps = _frame_rate(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    video_codec = str(video.get("codec_name") or "")
    pixel_format = str(video.get("pix_fmt") or "")
    sample_rate = _int(audio.get("sample_rate"))
    channels = _int(audio.get("channels"))
    duration = _float((data.get("format") or {}).get("duration"))
    checks = {
        "has_video": bool(video),
        "has_audio": bool(audio),
        "vertical_9_16": width > 0 and height > width and abs((width / height) - (9 / 16)) <= 0.03,
        "fps_30": abs(fps - 30.0) <= 0.01,
        "video_codec_h264": video_codec == "h264",
        "pixel_format_yuv420p": pixel_format == "yuv420p",
        "duration_ok": 1 <= duration <= 60,
        "audio_ready": sample_rate == 48000 and channels >= 2,
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        "video_codec": video_codec,
        "pixel_format": pixel_format,
        "duration_s": duration,
        "audio_codec": str(audio.get("codec_name") or ""),
        "sample_rate": sample_rate,
        "channels": channels,
        "reason": "" if all(checks.values()) else "final MP4 failed upload-readiness probe",
    }


def _render_failure_reason(proc: subprocess.CompletedProcess[str], probe: dict[str, Any]) -> str:
    if proc.returncode != 0:
        return proc.stderr[-1000:] if proc.stderr else f"ffmpeg exit {proc.returncode}"
    return str(probe.get("reason") or "final MP4 failed upload-readiness probe")


def _write_srt(segments: list[dict], path) -> None:
    cursor = 0.0
    blocks: list[str] = []
    for index, segment in enumerate(segments, start=1):
        duration = float(segment.get("duration_target_sec") or 4)
        start = cursor
        end = cursor + duration
        blocks.append(
            f"{index}\n{_srt_ts(start)} --> {_srt_ts(end)}\n{segment.get('text', '')}\n"
        )
        cursor = end
    path.write_text("\n".join(blocks), encoding="utf-8")


def ensure_fonts() -> Path:
    """Ensure a subtitle font with Polish glyphs is available for ASS rendering."""
    FONTS_DIR.mkdir(parents=True, exist_ok=True)
    if os.getenv("PYTEST_CURRENT_TEST"):
        return FONTS_DIR
    for filename, url in REQUIRED_FONTS.items():
        path = FONTS_DIR / filename
        if path.exists() and path.stat().st_size > 1024:
            continue
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                path.write_bytes(response.read())
        except Exception:
            # FFmpeg/libass can still fall back to system fonts; do not block production.
            continue
    return FONTS_DIR


def generate_ass_subtitles(segments: list[dict[str, Any]], output_path: Path) -> Path:
    """Generate Shorts-safe ASS subtitles with large kinetic cue chunks."""
    cues = _chunk_subtitle_segments(segments)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Montserrat ExtraBold,{ASS_SUBTITLE_FONT_SIZE},&H00FFFFFF,&H000000FF,&H00000000,&HA8000000,-1,0,0,0,100,100,0,0,1,6,2,2,64,64,250,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: list[str] = []
    for segment in cues:
        text = _ass_escape(_wrap_ass_text(str(segment.get("text") or ""), max_chars=22))
        events.append(
            f"Dialogue: 0,{_ass_ts(float(segment['start']))},{_ass_ts(float(segment['end']))},Default,,0,0,0,,"
            f"{{\\fad(80,60)\\an2\\pos(540,1510)}}{text}"
        )
    output_path.write_text(header + "\n".join(events), encoding="utf-8")
    return output_path


def _chunk_subtitle_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Split narration-sized subtitle segments into 3-4 word mobile cues."""
    cues: list[dict[str, Any]] = []
    for segment in segments:
        text = " ".join(str(segment.get("text") or "").split())
        words = text.split()
        if not words:
            continue
        start = float(segment.get("start") or 0)
        end = max(start + ASS_SUBTITLE_MIN_DURATION_S, float(segment.get("end") or start + 1.0))
        duration = max(ASS_SUBTITLE_MIN_DURATION_S, end - start)
        word_chunks = _word_chunks(words, max_words=ASS_SUBTITLE_MAX_WORDS)
        min_chunks_for_duration = max(1, int((duration + ASS_SUBTITLE_MAX_DURATION_S - 0.001) // ASS_SUBTITLE_MAX_DURATION_S))
        if min_chunks_for_duration > len(word_chunks) and len(words) > len(word_chunks):
            word_chunks = _word_chunks(words, max_words=max(1, min(ASS_SUBTITLE_MAX_WORDS, len(words) // min_chunks_for_duration or 1)))
        chunk_duration = min(ASS_SUBTITLE_MAX_DURATION_S, duration / max(1, len(word_chunks)))
        chunk_duration = max(ASS_SUBTITLE_MIN_DURATION_S, chunk_duration)
        cursor = start
        for chunk in word_chunks:
            cue_end = min(end, cursor + chunk_duration)
            if cue_end <= cursor:
                cue_end = min(end, cursor + ASS_SUBTITLE_MIN_DURATION_S)
            cues.append({"start": round(cursor, 3), "end": round(cue_end, 3), "text": " ".join(chunk)})
            cursor = cue_end
            if cursor >= end - 0.01:
                break
    return cues


def _word_chunks(words: list[str], *, max_words: int) -> list[list[str]]:
    safe_max = max(1, int(max_words))
    return [words[index : index + safe_max] for index in range(0, len(words), safe_max)]


def _subtitle_segments(script_segments: list[dict], voice_timestamps: Any) -> list[dict[str, Any]]:
    if isinstance(voice_timestamps, list) and voice_timestamps:
        return [
            {
                "start": float(item.get("start_sec") or 0),
                "end": float(item.get("end_sec") or item.get("duration_sec") or 1),
                "text": str(item.get("text") or ""),
            }
            for item in voice_timestamps
        ]
    cursor = 0.0
    output: list[dict[str, Any]] = []
    for segment in script_segments:
        duration = float(segment.get("duration_target_sec") or 4)
        output.append({"start": cursor, "end": cursor + duration, "text": str(segment.get("text") or "")})
        cursor += duration
    return output


def _read_srt_segments(path: Path) -> list[dict[str, Any]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    segments: list[dict[str, Any]] = []
    for block in raw.replace("\r\n", "\n").split("\n\n"):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        start_raw, end_raw = [part.strip() for part in lines[1].split("-->", maxsplit=1)]
        segments.append(
            {
                "start": _parse_srt_ts(start_raw),
                "end": _parse_srt_ts(end_raw),
                "text": " ".join(lines[2:]),
            }
        )
    return segments


def _parse_srt_ts(value: str) -> float:
    try:
        hhmmss, millis = value.split(",", maxsplit=1)
        hours, minutes, seconds = hhmmss.split(":", maxsplit=2)
        return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis[:3]) / 1000
    except (ValueError, TypeError):
        return 0.0


def _wrap_ass_text(text: str, max_chars: int = 22) -> str:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
    if current:
        lines.append(current)
    return "\\N".join(lines)


def _ass_escape(text: str) -> str:
    return text.replace("{", "\\{").replace("}", "\\}")


def _ass_ts(seconds: float) -> str:
    total = max(0.0, seconds)
    h = int(total // 3600)
    m = int((total % 3600) // 60)
    s = total % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _ass_filter(subtitle_path: Path, fonts_dir: Path | None) -> str:
    subtitle = _ffmpeg_filter_path(subtitle_path)
    font_arg = f":fontsdir='{_ffmpeg_filter_path(fonts_dir)}'" if fonts_dir is not None else ""
    return f"ass=filename='{subtitle}'{font_arg}"


def _ffmpeg_filter_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _srt_ts(seconds: float) -> str:
    millis = int(round((seconds - int(seconds)) * 1000))
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d},{millis:03d}"


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _frame_rate(value: Any) -> float:
    text = str(value or "0/1")
    if "/" not in text:
        return _float(text)
    numerator, denominator = text.split("/", 1)
    divisor = _float(denominator)
    return _float(numerator) / divisor if divisor else 0.0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
