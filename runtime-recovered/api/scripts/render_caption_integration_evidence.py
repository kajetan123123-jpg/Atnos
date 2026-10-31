"""Render Remotion evidence frames proving on-screen captions ≠ headline.

Evidence lands in a NEW directory under E: (does not overwrite historical E2E).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from PIL import Image

from atnos.production.caption_timing import active_caption_text, caption_cues_from_words
from atnos.production.channels.wealth_blueprint.manifest_adapters import (
    order_artifacts_to_project_manifest,
)
from atnos.production.media_tools import resolve_ffmpeg
from atnos.production.project_manifest import save_project_manifest
from atnos.production.qa_gate import probe_media
from atnos.production.remotion_renderer import render_project_manifest
from atnos.production.steps.assembler import _mux_remotion_video


EVIDENCE_ROOT = Path(
    r"E:\Atnos\App\data\production\wealth_blueprint\caption-integration-20260919"
)


def _tone(path: Path, seconds: float) -> None:
    import math
    import wave
    from array import array

    path.parent.mkdir(parents=True, exist_ok=True)
    rate = 24_000
    n = max(1, int(seconds * rate))
    samples = array("h")
    for i in range(n):
        env = min(1.0, i / 400) * min(1.0, (n - i) / 800)
        samples.append(int(6500 * env * math.sin(2 * math.pi * 170 * (i / rate))))
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(samples.tobytes())


def _words(segment_id: str, text: str, start: float, end: float) -> list[dict]:
    tokens = text.split()
    if not tokens:
        return []
    span = max(0.05, end - start)
    step = span / len(tokens)
    words = []
    for index, token in enumerate(tokens):
        word_start = start + index * step
        word_end = start + (index + 1) * step
        words.append(
            {
                "text": token,
                "segment_id": segment_id,
                "start_sec": round(word_start, 3),
                "end_sec": round(word_end, 3),
            }
        )
    return words


def build_fixture(work: Path) -> dict:
    duration = 6.0
    voice = work / "voice.wav"
    _tone(voice, duration)
    still = work / "still.png"
    Image.new("RGB", (1080, 1920), (34, 66, 59)).save(still)

    s1 = "Your paycheck feels bigger this year."
    s2 = "But wealth is what you keep."
    s3 = "Save first then spend without guilt."
    words = (
        _words("s01", s1, 0.05, 1.95)
        + _words("s02", s2, 2.05, 3.95)
        + _words("s03", s3, 4.05, 5.95)
    )
    return {
        "script": {
            "topic": "Keep more of your raise",
            "segments": [
                {"id": "s01", "text": s1},
                {"id": "s02", "text": s2},
                {"id": "s03", "text": s3},
            ],
        },
        "voice": {
            "output_file": str(voice),
            "sha256": "fixture",
            "duration_seconds": duration,
            "timestamps": [
                {"segment_id": "s01", "text": s1, "start_sec": 0.0, "end_sec": 2.0},
                {"segment_id": "s02", "text": s2, "start_sec": 2.0, "end_sec": 4.0},
                {"segment_id": "s03", "text": s3, "start_sec": 4.0, "end_sec": 6.0},
            ],
            "word_timestamps": words,
        },
        "visuals": {
            "scenes": [
                {"layout_id": "fullscreen_statement", "headline": "RAISE SUMMARY"},
                {"layout_id": "quote_or_fact", "headline": "KEEP SUMMARY"},
                {"layout_id": "fullscreen_statement", "headline": "SAVE SUMMARY"},
            ],
            "assets": {"still": {"staged_path": str(still)}},
        },
        "captions": {"groups": [words]},
        "still": still,
        "voice_path": voice,
    }


def extract_frame(ffmpeg: str, video: Path, time_sec: float, output: Path) -> None:
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-v",
            "error",
            "-ss",
            f"{time_sec:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            str(output),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )


def main() -> dict:
    evidence = EVIDENCE_ROOT
    evidence.mkdir(parents=True, exist_ok=True)
    work = evidence / "work"
    work.mkdir(parents=True, exist_ok=True)
    fixture = build_fixture(work)

    manifest = order_artifacts_to_project_manifest(
        job_id="caption-integration-20260919",
        topic_title="Keep more of your raise",
        script=fixture["script"],
        voice=fixture["voice"],
        visuals=fixture["visuals"],
        selected_image=fixture["still"],
        captions=fixture["captions"],
        require_caption_words=True,
    )
    for scene in manifest.scenes:
        assert scene.caption != scene.headline, "headline must not replace subtitles"

    manifest_path = evidence / "project_manifest.json"
    save_project_manifest(manifest_path, manifest)

    silent = evidence / "renderer_silent.mp4"
    remotion = render_project_manifest(manifest_path, silent)
    (evidence / "remotion_result.json").write_text(
        json.dumps(remotion, indent=2, default=str) + "\n", encoding="utf-8"
    )
    if not remotion.get("ok"):
        raise RuntimeError(f"Remotion render failed: {remotion}")

    final = evidence / "final_short.mp4"
    mux = _mux_remotion_video(
        raw_video_path=silent,
        audio_path=fixture["voice_path"],
        output_path=final,
        target_lufs=-14.0,
    )
    (evidence / "mux.json").write_text(json.dumps(mux, indent=2, default=str) + "\n", encoding="utf-8")
    if not mux.get("ok"):
        raise RuntimeError(f"mux failed: {mux}")

    probe = probe_media(final)
    (evidence / "ffprobe.json").write_text(json.dumps(probe, indent=2) + "\n", encoding="utf-8")

    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("FFmpeg unavailable for frame extraction")

    frames_dir = evidence / "frames"
    frames_dir.mkdir(exist_ok=True)
    sample_times = [0.25, 1.1, 2.4, 3.3, 4.3, 5.4]
    samples = []
    for time_sec in sample_times:
        scene = next(
            s
            for s in manifest.scenes
            if s.timing.start_sec <= time_sec < s.timing.end_sec
            or (time_sec >= s.timing.end_sec and s is manifest.scenes[-1])
        )
        cues = caption_cues_from_words(scene.caption_words)
        expected = active_caption_text(cues, time_sec)
        frame_path = frames_dir / f"t_{time_sec:.2f}.png"
        extract_frame(ffmpeg, final, time_sec, frame_path)
        samples.append(
            {
                "time_sec": time_sec,
                "scene_id": scene.scene_id,
                "headline": scene.headline,
                "expected_subtitle": expected,
                "frame": str(frame_path),
                "headline_must_not_equal_subtitle": scene.headline != expected,
            }
        )

    report = {
        "ok": True,
        "evidence_root": str(evidence),
        "manifest": str(manifest_path),
        "final_mp4": str(final),
        "remotion_ok": bool(remotion.get("ok")),
        "probe": probe,
        "samples": samples,
        "checks": {
            "headline_differs_from_caption": all(
                scene.headline != scene.caption for scene in manifest.scenes
            ),
            "all_scenes_have_caption_words": all(bool(scene.caption_words) for scene in manifest.scenes),
            "frame_files_exist": all(Path(item["frame"]).is_file() for item in samples),
            "expected_subtitles_nonempty": all(bool(item["expected_subtitle"]) for item in samples),
        },
    }
    report["ok"] = all(report["checks"].values())
    (evidence / "caption_frame_evidence.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    payload = main()
    print(json.dumps(payload, indent=2))
    raise SystemExit(0 if payload.get("ok") else 1)
