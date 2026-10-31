from __future__ import annotations

import json
import shutil
import statistics
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from atnos.core.config import Settings
from atnos.production.media_tools import resolve_ffmpeg, resolve_ffprobe


REFERENCE_PROFILE_DIRNAME = "user-reference-shorts"
REFERENCE_PROFILE_FILENAME = "latest_reference_profile.json"


def build_reference_shorts_profile(
    settings: Settings,
    source_paths: Iterable[str | Path],
    *,
    output_path: str | Path | None = None,
    max_video_seconds: float = 60.0,
) -> dict[str, Any]:
    """Zbuduj profil produkcyjny Shorts z lokalnych referencyjnych plikow MP4."""
    sources = [_existing_video_path(path) for path in source_paths]
    sources = [path for path in sources if path is not None]
    if not sources:
        raise ValueError("No existing reference video files were provided.")

    analyses = [_analyze_reference_video(path, max_video_seconds=max_video_seconds) for path in sources]
    successful = [item for item in analyses if item.get("status") == "analyzed"]
    if not successful:
        raise ValueError("Reference analysis failed for every provided video.")

    profile = _build_profile_payload(successful, all_analyses=analyses)
    target = Path(output_path) if output_path is not None else _default_profile_path(settings)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    profile["path"] = str(target)
    target.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return profile


def analyze_video_visual_profile(video_path: str | Path, *, max_video_seconds: float = 20.0) -> dict[str, Any]:
    """Zmierz jasnosc, saturacje i rytm cieć dla pojedynczego wideo."""
    path = _existing_video_path(video_path)
    if path is None:
        return _empty_frame_metrics("missing_video")
    probe = _probe_video(path)
    duration = float(probe.get("duration_sec") or 0.0)
    if duration <= 0:
        return _empty_frame_metrics("duration_unavailable")
    return _analyze_frames(path, duration_s=min(duration, max_video_seconds))


def _default_profile_path(settings: Settings) -> Path:
    return settings.data_dir / "learning" / REFERENCE_PROFILE_DIRNAME / REFERENCE_PROFILE_FILENAME


def _existing_video_path(value: str | Path) -> Path | None:
    path = Path(str(value)).expanduser()
    return path if path.exists() and path.is_file() and path.suffix.lower() in {".mp4", ".mov", ".m4v", ".webm"} else None


def _analyze_reference_video(path: Path, *, max_video_seconds: float) -> dict[str, Any]:
    probe = _probe_video(path)
    duration = float(probe.get("duration_sec") or 0.0)
    if duration <= 0:
        return {"status": "failed", "path": str(path), "reason": "duration_unavailable", "probe": probe}
    frame_metrics = _analyze_frames(path, duration_s=min(duration, max_video_seconds))
    lufs = _measure_lufs(path, duration_s=min(duration, max_video_seconds))
    return {
        "status": "analyzed",
        "path": str(path),
        "file_name": path.name,
        "duration_sec": round(duration, 3),
        "width": probe.get("width"),
        "height": probe.get("height"),
        "frame_rate": probe.get("frame_rate"),
        "lufs": lufs,
        **frame_metrics,
    }


def _probe_video(path: Path) -> dict[str, Any]:
    ffprobe = resolve_ffprobe(resolve_ffmpeg())
    if not ffprobe:
        return {"available": False, "reason": "ffprobe_unavailable"}
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
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": False, "reason": str(exc)}
    if proc.returncode != 0 or not proc.stdout.strip():
        return {"available": False, "reason": proc.stderr[-500:]}
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"available": False, "reason": "invalid_ffprobe_json"}
    streams = payload.get("streams") if isinstance(payload.get("streams"), list) else []
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), {})
    fmt = payload.get("format") if isinstance(payload.get("format"), dict) else {}
    return {
        "available": True,
        "duration_sec": _float(fmt.get("duration") or video_stream.get("duration")),
        "width": int(_float(video_stream.get("width"))),
        "height": int(_float(video_stream.get("height"))),
        "frame_rate": _parse_rate(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")),
    }


def _analyze_frames(path: Path, *, duration_s: float, scratch_dir: Path | None = None) -> dict[str, Any]:
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        return _empty_frame_metrics("ffmpeg_unavailable")
    try:
        from PIL import Image, ImageChops, ImageStat
    except Exception:
        return _empty_frame_metrics("pillow_unavailable")

    if scratch_dir is not None:
        scratch_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="atnos-ref-frames-", dir=scratch_dir) as tmp:
        frame_dir = Path(tmp)
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(path),
            "-t",
            str(max(1.0, min(60.0, duration_s))),
            "-vf",
            "fps=2,scale=180:320:force_original_aspect_ratio=increase,crop=180:320",
            str(frame_dir / "frame_%04d.jpg"),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            return _empty_frame_metrics(f"frame_extract_failed:{exc}")
        if proc.returncode != 0:
            return _empty_frame_metrics(f"frame_extract_failed:{proc.stderr[-300:]}")
        frames = sorted(frame_dir.glob("frame_*.jpg"))
        if not frames:
            return _empty_frame_metrics("no_frames")

        brightness_values: list[float] = []
        saturation_values: list[float] = []
        diffs: list[float] = []
        prev = None
        for frame in frames:
            image = Image.open(frame).convert("RGB")
            gray = image.convert("L")
            brightness_values.append(float(ImageStat.Stat(gray).mean[0]) / 255.0)
            saturation = image.convert("HSV").split()[1]
            saturation_values.append(float(ImageStat.Stat(saturation).mean[0]) / 255.0)
            if prev is not None:
                gray_diff = ImageChops.difference(gray, prev.convert("L"))
                rgb_diff = ImageChops.difference(image, prev)
                gray_score = float(ImageStat.Stat(gray_diff).mean[0]) / 255.0
                rgb_score = sum(ImageStat.Stat(rgb_diff).mean) / (3.0 * 255.0)
                diffs.append(max(gray_score, rgb_score))
            prev = image

        # Dark editorial layouts can change composition without a large luminance
        # jump. RGB distance at 0.11 catches those cuts while ignoring ordinary
        # camera drift, which stays far below the threshold in sampled footage.
        cut_times = [round((index + 1) / 2.0, 2) for index, diff in enumerate(diffs) if diff >= 0.11]
        intervals = [cut_times[0], *[cut_times[i] - cut_times[i - 1] for i in range(1, len(cut_times))]] if cut_times else []
        measured_duration = min(duration_s, len(frames) / 2.0)
        brightness_deltas = [
            abs(brightness_values[index] - brightness_values[index - 1])
            for index in range(1, len(brightness_values))
        ]
        isolated_flashes = sum(
            1
            for index in range(1, len(brightness_values) - 1)
            if abs(brightness_values[index] - brightness_values[index - 1]) >= 0.16
            and abs(brightness_values[index] - brightness_values[index + 1]) >= 0.16
            and abs(brightness_values[index - 1] - brightness_values[index + 1]) <= 0.08
        )
        return {
            "brightness": round(statistics.median(brightness_values), 4),
            "saturation": round(statistics.median(saturation_values), 4),
            "first_cut_sec": cut_times[0] if cut_times else None,
            "avg_cut_interval_sec": round(statistics.median(intervals), 3) if intervals else None,
            "cuts_per_10s": round(len(cut_times) / max(0.1, measured_duration) * 10.0, 3),
            "median_frame_delta": round(statistics.median(diffs), 4) if diffs else None,
            "p95_frame_delta": _round_or_none(_percentile(diffs, 0.95), 4),
            "median_brightness_delta": round(statistics.median(brightness_deltas), 4) if brightness_deltas else None,
            "p95_brightness_delta": _round_or_none(_percentile(brightness_deltas, 0.95), 4),
            "isolated_flash_events": isolated_flashes,
            "flash_events_per_10s": round(isolated_flashes / max(0.1, measured_duration) * 10.0, 3),
            "frame_sample_count": len(frames),
            "frame_analysis_status": "analyzed",
        }


def _empty_frame_metrics(reason: str) -> dict[str, Any]:
    return {
        "brightness": None,
        "saturation": None,
        "first_cut_sec": None,
        "avg_cut_interval_sec": None,
        "cuts_per_10s": None,
        "median_frame_delta": None,
        "p95_frame_delta": None,
        "median_brightness_delta": None,
        "p95_brightness_delta": None,
        "isolated_flash_events": None,
        "flash_events_per_10s": None,
        "frame_sample_count": 0,
        "frame_analysis_status": reason,
    }


def _measure_lufs(path: Path, *, duration_s: float) -> float | None:
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        return None
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-nostats",
        "-i",
        str(path),
        "-t",
        str(max(1.0, min(60.0, duration_s))),
        "-af",
        "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json",
        "-f",
        "null",
        "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    output = f"{proc.stdout}\n{proc.stderr}"
    start = output.rfind("{")
    end = output.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(output[start : end + 1])
    except json.JSONDecodeError:
        return None
    return _optional_float(payload.get("input_i"))


def _build_profile_payload(analyses: list[dict[str, Any]], *, all_analyses: list[dict[str, Any]]) -> dict[str, Any]:
    duration = _median_number(item.get("duration_sec") for item in analyses)
    lufs = _median_number(item.get("lufs") for item in analyses)
    brightness = _median_number(item.get("brightness") for item in analyses)
    saturation = _median_number(item.get("saturation") for item in analyses)
    first_cut = _median_number(item.get("first_cut_sec") for item in analyses)
    avg_cut = _median_number(item.get("avg_cut_interval_sec") for item in analyses)
    cuts_per_10s = _median_number(item.get("cuts_per_10s") for item in analyses)
    frame_delta = _median_number(item.get("median_frame_delta") for item in analyses)
    p95_frame_delta = _median_number(item.get("p95_frame_delta") for item in analyses)
    brightness_delta = _median_number(item.get("median_brightness_delta") for item in analyses)
    p95_brightness_delta = _median_number(item.get("p95_brightness_delta") for item in analyses)
    flash_events = _median_number(item.get("flash_events_per_10s") for item in analyses)
    targets = _atnos_targets(
        duration=duration,
        lufs=lufs,
        brightness=brightness,
        saturation=saturation,
        first_cut=first_cut,
        avg_cut=avg_cut,
    )
    return {
        "source": "user_supplied_reference_shorts",
        "generated_at": datetime.now(UTC).isoformat(),
        "sample_count": len(analyses),
        "failed_sample_count": len(all_analyses) - len(analyses),
        "median_duration_sec": _round_or_none(duration, 3),
        "median_lufs": _round_or_none(lufs, 2),
        "median_first_cut_sec": _round_or_none(first_cut, 3),
        "median_avg_cut_interval_sec": _round_or_none(avg_cut, 3),
        "median_cuts_per_10s": _round_or_none(cuts_per_10s, 3),
        "median_frame_delta": _round_or_none(frame_delta, 4),
        "median_p95_frame_delta": _round_or_none(p95_frame_delta, 4),
        "median_brightness_delta": _round_or_none(brightness_delta, 4),
        "median_p95_brightness_delta": _round_or_none(p95_brightness_delta, 4),
        "median_flash_events_per_10s": _round_or_none(flash_events, 3),
        "median_brightness": _round_or_none(brightness, 4),
        "median_saturation": _round_or_none(saturation, 4),
        "atnos_targets": targets,
        "production_rules_for_atnos": _production_rules(
            duration=duration,
            lufs=lufs,
            brightness=brightness,
            saturation=saturation,
            first_cut=first_cut,
            avg_cut=avg_cut,
            flash_events=flash_events,
            targets=targets,
        ),
        "samples": analyses,
        "all_samples": all_analyses,
    }


def _atnos_targets(
    *,
    duration: float | None,
    lufs: float | None,
    brightness: float | None,
    saturation: float | None,
    first_cut: float | None,
    avg_cut: float | None,
) -> dict[str, Any]:
    return {
        "target_duration_sec": _round_or_none(_clamp(duration, 20.0, 59.0), 2),
        "target_lufs": _round_or_none(_safe_target_lufs(lufs), 2),
        "hook_target_sec": _round_or_none(_clamp(first_cut, 0.5, 3.0), 2),
        "avg_cut_interval_sec": _round_or_none(_clamp(avg_cut, 1.2, 4.5), 2),
        "brightness": _round_or_none(brightness, 4),
        "saturation": _round_or_none(saturation, 4),
    }


def _safe_target_lufs(value: float | None) -> float:
    if value is not None and -18.0 <= value <= -12.0:
        return float(value)
    return -14.0


def _production_rules(
    *,
    duration: float | None,
    lufs: float | None,
    brightness: float | None,
    saturation: float | None,
    first_cut: float | None,
    avg_cut: float | None,
    flash_events: float | None,
    targets: dict[str, Any],
) -> list[str]:
    rules = [
        "Use the measured user reference profile as a target, not as copied content.",
        "Keep every output vertical 9:16 and under 60 seconds.",
        "Do not publish public videos until the private review flow approves the exact render.",
    ]
    if lufs is not None:
        target_lufs = targets.get("target_lufs")
        if target_lufs != round(lufs, 2):
            rules.append(
                f"References measured {round(lufs, 1)} LUFS, but keep Atnos target at {target_lufs} LUFS for YouTube-safe loudness."
            )
        else:
            rules.append(f"Target narration/music mix near {round(lufs, 1)} LUFS within YouTube-safe bounds.")
    if first_cut is not None:
        rules.append(f"Establish the hook visually before {round(max(0.5, min(3.0, first_cut)), 1)} seconds.")
    if avg_cut is not None:
        rules.append(f"Keep visual rhythm around one meaningful change every {round(max(1.2, min(4.5, avg_cut)), 1)} seconds.")
    rules.extend(
        [
            "Use one semantic motion per scene: movement must explain the spoken idea, not decorate it.",
            "Use continuous interpolation inside a shot; never reset scale, position, or exposure mid-scene.",
            "Prefer a clean hard cut on a new idea. Do not use crossfades, white flashes, or transition-only filler frames.",
            "Reject isolated luminance spikes and repeated near-identical frames that create visible stutter.",
        ]
    )
    if flash_events is not None and flash_events > 0:
        rules.append(
            "Reference footage contains isolated brightness spikes; treat them as defects and keep Atnos target at zero flashes."
        )
    if brightness is not None:
        mood = "dark/high-contrast" if brightness < 0.38 else "medium-bright"
        rules.append(f"Match reference brightness: {mood}, median brightness {round(brightness, 2)}.")
    if saturation is not None:
        rules.append(f"Match reference color intensity: median saturation {round(saturation, 2)}.")
    if duration is not None:
        rules.append(f"Aim final Shorts duration near {round(min(59.0, max(20.0, duration)), 1)} seconds when script allows.")
    return rules


def _percentile(values: list[float], ratio: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    position = max(0.0, min(1.0, ratio)) * (len(ordered) - 1)
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def copy_reference_videos_into_learning_dir(settings: Settings, source_paths: Iterable[str | Path]) -> list[Path]:
    """Skopiuj lokalne referencje do katalogu learning, bez modyfikowania zrodel."""
    target_dir = settings.data_dir / "learning" / REFERENCE_PROFILE_DIRNAME / "sources"
    target_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for source in source_paths:
        path = _existing_video_path(source)
        if path is None:
            continue
        target = target_dir / path.name
        if path.resolve() != target.resolve():
            shutil.copyfile(path, target)
        copied.append(target)
    return copied


def _median_number(values: Iterable[Any]) -> float | None:
    numbers = [_float(value) for value in values if _optional_float(value) is not None]
    if not numbers:
        return None
    return float(statistics.median(numbers))


def _round_or_none(value: float | None, ndigits: int) -> float | None:
    return round(float(value), ndigits) if value is not None else None


def _clamp(value: float | None, low: float, high: float) -> float | None:
    if value is None:
        return None
    return max(low, min(high, float(value)))


def _parse_rate(value: Any) -> float:
    raw = str(value or "").strip()
    if "/" in raw:
        left, right = raw.split("/", 1)
        denominator = _float(right)
        return round(_float(left) / denominator, 3) if denominator else 0.0
    return round(_float(raw), 3)


def _optional_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0
