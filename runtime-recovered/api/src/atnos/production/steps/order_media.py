from __future__ import annotations

import asyncio
import math
from pathlib import Path
import subprocess

import numpy as np

from atnos.production import editorial_film
from atnos.production.media_tools import resolve_ffmpeg
from atnos.production.models import StepResult
from atnos.production.order_store import file_hash
from atnos.production.qa_gate import probe_media
from atnos.production.reference_profile_builder import _measure_lufs
from atnos.production.steps.common import write_json
from atnos.production.steps.voice_synthesis import _srt_ts


def _result(path, payload, files=()):
    result = write_json(path, payload)
    return StepResult(result.artifact_name, path, payload, result.message, tuple(files))


async def produce_captions(executor):
    context = executor.context
    profile = context.artifacts["project_profile"]["production"]
    voice = context.artifacts["voice"]

    async def generate():
        words = voice["word_timestamps"]
        expected = " ".join(s["text"] for s in context.artifacts["script"]["segments"])
        if " ".join(w["text"] for w in words) != expected:
            raise ValueError("Captions do not match the validated narration")
        cursor = 0
        for word in words:
            if not cursor - 1e-6 <= word["start_sec"] < word["end_sec"] <= voice["duration_seconds"] + .03:
                raise ValueError("Invalid caption word timing")
            cursor = word["end_sec"]
        groups = editorial_film.caption_groups(words, max_words=profile["caption_max_words"], max_characters=profile["caption_max_characters"])
        boxes = [list(editorial_film.caption_layout(group, safe_area=profile["safe_area"])[-1]) for group in groups]
        target = context.artifact_dir / "captions.srt"
        target.write_text("\n\n".join(f"{i+1}\n{_srt_ts(g[0]['start_sec'])} --> {_srt_ts(g[-1]['end_sec'])}\n{' '.join(w['text'] for w in g)}"
                                      for i, g in enumerate(groups)) + "\n", encoding="utf-8")
        payload = {"groups": groups, "output_file": str(target), "qa": {"status": "passed", "safe_area": profile["safe_area"],
                   "boxes": boxes, "timing_source": voice["timing_source"], "aligned_word_count": len(words)}}
        return _result(context.artifact_dir / "captions.json", payload, [target])

    payload = await executor.run("captions", {"voice_sha256": voice["sha256"], "words": voice["word_timestamps"],
                                  "limits": [profile["caption_max_words"], profile["caption_max_characters"], profile["safe_area"]]}, generate)
    context.artifacts["captions"] = payload
    return payload


async def produce_edit(executor):
    from atnos.production.steps.order_assets import fit_scenes_to_narration
    from atnos.production.steps.order_repair import apply_quality_repairs

    context = executor.context
    config = context.artifacts["project_profile"]["production"]
    from atnos.production.channels.wealth_blueprint.remotion_mode import is_remotion_renderer

    # Explicit Remotion test option. Fail-closed: never fall through to editorial.
    if is_remotion_renderer(config):
        from atnos.production.channels.wealth_blueprint.remotion_order_edit import (
            RemotionRequiredError,
            produce_edit_remotion,
        )

        try:
            return await produce_edit_remotion(executor)
        except RemotionRequiredError:
            raise
        except Exception as exc:  # noqa: BLE001
            # Preserve fail-closed semantics for unexpected remotion failures.
            raise RemotionRequiredError(str(exc) or type(exc).__name__) from exc
    await fit_scenes_to_narration(executor)
    await apply_quality_repairs(executor)
    visuals, voice, captions = (context.artifacts[key] for key in ("visuals", "voice", "captions"))
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("FFmpeg unavailable")
    brief = {**visuals, "safe_area": config["safe_area"]}
    ranges = editorial_film.frame_ranges(voice["timestamps"])
    if [s["segment_id"] for s in visuals["scenes"]] != [t["segment_id"] for t in voice["timestamps"]]:
        raise ValueError("Visual plan and recorded narration have different segment order")
    clips = []
    for index, (scene, timing, frame_range) in enumerate(zip(visuals["scenes"], voice["timestamps"], ranges, strict=True)):
        asset = visuals["assets"].get(scene["asset_id"]) if scene["asset_id"] else None
        source = Path(asset["staged_path"]) if asset else None
        if asset and file_hash(source) != asset["sha256"]:
            raise ValueError("Selected footage changed after asset validation")
        if asset and scene["source_start"] + (frame_range[1] - frame_range[0]) / 30 > asset["duration_seconds"] - .04:
            raise ValueError(f"Scene {scene['segment_id']} is longer than its available footage; select a different source")
        groups = [g for g in captions["groups"] if g[0]["segment_id"] == scene["segment_id"]]

        async def render():
            clip = context.artifact_dir / "scene_cache" / f"{scene['segment_id']}.mp4"
            last = await asyncio.to_thread(editorial_film.render_scene_clip, ffmpeg=ffmpeg, brief=brief, scene=scene,
                index=index, timing=timing, frame_range=frame_range, groups=groups, output=clip, source=source, previous=None)
            preview = clip.with_suffix(".png")
            last.save(preview)
            probe = probe_media(clip)
            if not probe.get("video_codec") or abs(probe["duration_s"] - (frame_range[1]-frame_range[0])/30) > .06:
                raise ValueError("Rendered scene failed the duration/format check")
            return _result(clip.with_suffix(".json"), {"path": str(clip), "sha256": file_hash(clip), "preview": str(preview),
                 "frame_range": frame_range, "segment_id": scene["segment_id"], "probe": probe}, [clip, preview])

        clip = await executor.run(f"render/{scene['segment_id']}", {"scene": scene, "frames": frame_range, "groups": groups,
                    "asset": asset, "claims": brief["claims"], "safe_area": config["safe_area"],
                    "renderer_sha256": file_hash(Path(editorial_film.__file__))}, render)
        clips.append(clip)

    def assemble():
        directory = context.artifact_dir / "scene_cache"
        concat = directory / "concat.txt"
        concat.write_text("".join(f"file '{Path(c['path']).name}'\n" for c in clips), encoding="utf-8")
        silent, final = context.artifact_dir / "assets/renderer_silent.mp4", context.artifact_dir / "assets/final_short.mp4"
        silent.parent.mkdir(exist_ok=True)
        subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "1", "-i", str(concat), "-c", "copy", str(silent)], check=True, capture_output=True, timeout=120)
        subprocess.run([ffmpeg, "-y", "-v", "error", "-i", str(silent), "-i", voice["output_file"], "-map", "0:v:0", "-map", "1:a:0",
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(final)],
                       check=True, capture_output=True, timeout=180)
        payload = {"output_file": str(final), "sha256": file_hash(final), "clips": clips,
                   "duration_seconds": voice["duration_seconds"], "music": "none", "qa": "not_started"}
        return _result(context.artifact_dir / "edit.json", payload, [silent, final])

    async def assemble_thread():
        # FFmpeg waits must not block status/API handling in the worker's event loop.
        return await asyncio.to_thread(assemble)

    # Retain the final complete video frame; audio duration need not be an exact multiple of 1/30s.
    payload = await executor.run("editing", {"clips": clips, "voice_sha256": voice["sha256"], "mux_contract": 2}, assemble_thread)
    context.artifacts["edit"] = payload
    return payload


def full_frame_scan(video: Path, *, expected_frames: int) -> dict:
    ffmpeg = resolve_ffmpeg()
    result = subprocess.run([ffmpeg, "-v", "error", "-i", str(video), "-an", "-vf", "scale=64:114", "-pix_fmt", "gray",
                             "-f", "rawvideo", "pipe:1"], capture_output=True, timeout=180)
    size = 64 * 114
    if result.returncode or result.stderr.strip() or not result.stdout or len(result.stdout) % size:
        raise ValueError("Full video decode failed")
    frames = np.frombuffer(result.stdout, dtype=np.uint8).reshape(-1, 114, 64).astype(np.float32) / 255
    luminance = frames.mean(axis=(1, 2))
    black = np.flatnonzero((frames < .035).mean(axis=(1, 2)) > .99).tolist()
    flashes = []
    for i in range(1, len(frames)-1):
        if abs(float(luminance[i-1] - luminance[i+1])) < .06 and abs(float(luminance[i] - (luminance[i-1]+luminance[i+1])/2)) > .2:
            flashes.append(i)
    return {"status": "passed" if not black and not flashes and len(frames) == expected_frames else "failed",
            "frames_decoded": len(frames), "expected_frames": expected_frames, "black_frames": black,
            "isolated_flash_frames": flashes, "sampling": "every decoded frame at 64x114", "decode_errors": False}


def delivery_format_matches(probe: dict) -> bool:
    fps = probe.get("fps")
    # MP4 track timescales can round the final frame duration at stream-copy/mux time.
    return (probe.get("width") == 1080 and probe.get("height") == 1920
            and isinstance(fps, (int, float)) and math.isclose(fps, 30, rel_tol=0, abs_tol=.001)
            and probe.get("video_codec") == "h264" and probe.get("pixel_format") == "yuv420p")


async def produce_media_qa(executor):
    context = executor.context
    edit, voice = context.artifacts["edit"], context.artifacts["voice"]
    renderer = str(
        ((context.artifacts.get("project_profile") or {}).get("production") or {}).get("renderer")
        or edit.get("renderer")
        or "editorial"
    ).casefold()

    async def inspect():
        video = Path(edit["output_file"])
        probe = await asyncio.to_thread(probe_media, video)
        expected_frames = round(voice["duration_seconds"] * 30)
        frames = await asyncio.to_thread(full_frame_scan, video, expected_frames=expected_frames)
        # Remotion mux can land ±2 frames vs narration clock; still reject black/flash.
        if renderer == "remotion" and frames["status"] == "failed":
            decoded = int(frames.get("frames_decoded") or 0)
            if (
                not frames.get("black_frames")
                and not frames.get("isolated_flash_frames")
                and not frames.get("decode_errors")
                and abs(decoded - expected_frames) <= 2
            ):
                frames = {**frames, "status": "passed", "remotion_frame_tolerance": True}
        loudness = await asyncio.to_thread(_measure_lufs, video, duration_s=voice["duration_seconds"])
        checks = {"decoded_frames": frames["status"] == "passed", "format": delivery_format_matches(probe),
                  "audio": bool(probe.get("audio_codec")), "duration": abs(probe.get("duration_s", 0)-voice["duration_seconds"]) <= .1,
                  "loudness": loudness is not None and -18 <= loudness <= -12}
        # Remotion path: technical video_qc is a precheck; media_qa remains the order hard gate.
        if renderer == "remotion" and isinstance(edit.get("video_qc"), dict) and edit["video_qc"].get("ok") is False:
            checks["video_qc_precheck"] = False
        payload = {"status": "passed" if all(checks.values()) else "failed", "checks": checks, "frames": frames, "probe": probe,
                   "integrated_lufs": loudness, "video_sha256": file_hash(video), "renderer": renderer,
                   "semantic_review": "separate_required_gate", "gate_role": "production_qa_final"}
        return _result(context.artifact_dir / "media_qa.json", payload)

    result = await executor.run("media_qa", {"video_sha256": edit["sha256"], "duration": voice["duration_seconds"], "validator": 2}, inspect)
    context.artifacts["media_qa"] = result
    return result
