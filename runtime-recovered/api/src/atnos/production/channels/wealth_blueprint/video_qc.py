"""Wealth Blueprint pipeline QC — production checks + Quality Council video probe patterns."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from atnos.production.media_tools import resolve_ffmpeg, resolve_ffprobe
from atnos.production.qa_gate import _check_black_frames, probe_media


def _safe_area_contract() -> dict[str, int]:
    """Prefer channel production_safe_area from style pack; fall back to Remotion SAFE_*."""
    try:
        from atnos.production.channels.wealth_blueprint.design_system import load_wealth_blueprint_style_v1

        pack = load_wealth_blueprint_style_v1()
        box = pack.safe_zones.get("production_safe_area")
        if isinstance(box, list) and len(box) == 4:
            x0, y0, x1, y1 = (int(box[0]), int(box[1]), int(box[2]), int(box[3]))
            return {
                "left": x0,
                "top": y0,
                "right": max(0, 1080 - x1),
                "bottom": max(0, 1920 - y1),
                "box": [x0, y0, x1, y1],
            }
        legacy = pack.safe_zones.get("remotion_legacy") or {}
        return {
            "left": int(legacy.get("SAFE_X") or 72),
            "right": int(legacy.get("SAFE_X") or 72),
            "top": int(legacy.get("SAFE_TOP") or 96),
            "bottom": int(legacy.get("SAFE_BOTTOM") or 220),
        }
    except Exception:
        return {"left": 72, "right": 72, "top": 96, "bottom": 220}


SAFE_AREA = _safe_area_contract()


def run_wealth_blueprint_qc(
    *,
    artifact_dir: Path,
    video_path: Path,
    audio_path: Path,
    manifest_path: Path | None,
    scene_plan_path: Path | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    # Assets exist
    asset_issues: list[str] = []
    if manifest_path is not None and manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for scene in manifest.get("scenes") or []:
            for asset in scene.get("assets") or []:
                path = Path(str(asset.get("path") or ""))
                if asset.get("role") == "selected_image" and (not path.is_file() or not asset.get("exists", True)):
                    asset_issues.append(str(path))
    elif video_path is None or not Path(video_path).is_file():
        asset_issues.append(str(video_path))
    checks.append(
        {
            "name": "assets_exist",
            "status": "fail" if asset_issues else "pass",
            "detail": "missing: " + ", ".join(asset_issues) if asset_issues else "all referenced assets present",
        }
    )

    # Geometric placeholders (orange circle / stick Alex) must never pass film QC.
    geometric_hits: list[str] = []
    if manifest_path is not None and manifest_path.is_file():
        try:
            from atnos.production.channels.wealth_blueprint.geometric_assets import (
                is_geometric_placeholder_asset,
                looks_like_geometric_raster,
            )

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for scene in manifest.get("scenes") or []:
                sid = str(scene.get("scene_id") or "")
                for asset in scene.get("assets") or []:
                    if asset.get("role") not in {"selected_image", "image_candidate"}:
                        continue
                    asset_id = str(asset.get("asset_id") or "")
                    path = Path(str(asset.get("path") or ""))
                    provenance = asset.get("provenance") if isinstance(asset.get("provenance"), dict) else {}
                    params = provenance.get("generation_parameters") if isinstance(provenance, dict) else {}
                    if not isinstance(params, dict):
                        params = {}
                    # Prefer provenance asset id when path is a staged copy.
                    check_id = str(params.get("source_asset_id") or asset_id)
                    if is_geometric_placeholder_asset(
                        {"id": check_id, "file_path": str(path)},
                        resolve_path=path if path.is_file() else None,
                    ) or (path.is_file() and looks_like_geometric_raster(path)):
                        geometric_hits.append(f"{sid}:{check_id or path.name}")
        except Exception as exc:  # noqa: BLE001
            geometric_hits.append(f"audit_error:{exc}")
    checks.append(
        {
            "name": "no_geometric_placeholders",
            "status": "fail" if geometric_hits else "pass",
            "detail": (
                "blocked: " + ", ".join(geometric_hits)
                if geometric_hits
                else "no geometric library placeholders in selected_image assets"
            ),
        }
    )

    # Audio exists + duration
    audio_ok = audio_path.is_file() and audio_path.stat().st_size > 44
    audio_probe = probe_media(audio_path) if audio_ok else {}
    audio_dur = float(audio_probe.get("duration_s") or 0.0)
    checks.append(
        {
            "name": "audio_exists",
            "status": "pass" if audio_ok else "fail",
            "detail": f"duration_s={audio_dur:.3f}" if audio_ok else "narration.wav missing",
        }
    )

    # Video probe (needed early for scaled voice floor)
    video_ok = video_path.is_file() and video_path.stat().st_size > 4096
    video_probe = probe_media(video_path) if video_ok else {}
    video_dur = float(video_probe.get("duration_s") or 0.0)
    # Production ~30s shorts expect >=8s VO; shorter ladder/test renders scale the floor.
    voice_floor = 8.0 if video_dur >= 20.0 or video_dur <= 0 else max(2.0, min(8.0, video_dur * 0.9))
    checks.append(
        {
            "name": "voice_not_truncated",
            "status": "pass" if audio_dur >= voice_floor else "fail",
            "detail": f"audio duration {audio_dur:.2f}s (expect >= {voice_floor:.1f}s; video={video_dur:.2f}s)",
        }
    )

    checks.append(
        {
            "name": "final_mp4_valid",
            "status": "pass"
            if video_ok and video_probe.get("available") and video_probe.get("video_codec")
            else "fail",
            "detail": json.dumps(
                {
                    "bytes": video_path.stat().st_size if video_path.is_file() else 0,
                    "width": video_probe.get("width"),
                    "height": video_probe.get("height"),
                    "fps": video_probe.get("fps"),
                    "duration_s": video_dur,
                    "video_codec": video_probe.get("video_codec"),
                    "audio_codec": video_probe.get("audio_codec"),
                },
                ensure_ascii=False,
            ),
        }
    )

    # Duration match audio
    delta = abs(video_dur - audio_dur) if video_ok and audio_ok else 999.0
    checks.append(
        {
            "name": "video_matches_audio_duration",
            "status": "pass" if delta <= 0.85 else "fail",
            "detail": f"video={video_dur:.3f}s audio={audio_dur:.3f}s delta={delta:.3f}s",
        }
    )

    # Black frames (qa_gate)
    if video_ok:
        black = _check_black_frames(video_path, required=True)
        checks.append(
            {
                "name": "no_blank_or_black_frames",
                "status": black.get("status", "fail"),
                "detail": black.get("detail") or black.get("value"),
            }
        )
    else:
        checks.append({"name": "no_blank_or_black_frames", "status": "fail", "detail": "video missing"})

    # Luma sample — not entirely black mid-frame
    checks.append(_midframe_luma_check(video_path) if video_ok else {"name": "not_entirely_black", "status": "fail", "detail": "no video"})

    # Scene timing from plan
    timing_ok = True
    timing_detail = "no scene plan"
    if scene_plan_path and scene_plan_path.is_file():
        plan = json.loads(scene_plan_path.read_text(encoding="utf-8"))
        scenes = plan.get("scenes") or []
        timing_ok = bool(scenes)
        prev_end = 0.0
        for scene in scenes:
            start = float(scene.get("start_sec") or 0)
            end = float(scene.get("end_sec") or 0)
            if end <= start or start + 1e-6 < prev_end:
                timing_ok = False
                break
            prev_end = end
        timing_detail = f"scenes={len(scenes)} span={prev_end:.2f}s"
    checks.append({"name": "scene_timing_valid", "status": "pass" if timing_ok else "fail", "detail": timing_detail})

    # Safe-zone / text fit — contract from Remotion SAFE_* + plan on_screen length
    text_fit_ok = True
    text_detail = "ok"
    if scene_plan_path and scene_plan_path.is_file():
        plan = json.loads(scene_plan_path.read_text(encoding="utf-8"))
        long_lines = [
            scene.get("scene_id")
            for scene in plan.get("scenes") or []
            if len(str(scene.get("on_screen_text") or "").split()) > 8
        ]
        text_fit_ok = not long_lines
        text_detail = "oversized: " + ",".join(long_lines) if long_lines else f"safe_area={SAFE_AREA}"
    checks.append({"name": "text_fits_safe_zone", "status": "pass" if text_fit_ok else "fail", "detail": text_detail})
    # Frame render errors marker (job stages path OR order/ladder remotion_result.json)
    render_candidates = [
        artifact_dir / "stages" / "render_preview" / "remotion_result.json",
        artifact_dir / "remotion_result.json",
        artifact_dir / "remotion.json",
    ]
    render_log = next((path for path in render_candidates if path.is_file()), render_candidates[0])
    render_ok = True
    render_detail = "no remotion result"
    if render_log.is_file():
        try:
            payload = json.loads(render_log.read_text(encoding="utf-8"))
            render_ok = bool(payload.get("ok"))
            render_detail = str(payload.get("reason") or payload.get("status") or "ok")[:300]
        except (OSError, json.JSONDecodeError) as exc:
            render_ok = False
            render_detail = f"invalid remotion_result.json: {exc}"
    else:
        render_ok = False
        render_detail = f"missing remotion result under {artifact_dir}"
    checks.append({"name": "frame_render_errors", "status": "pass" if render_ok else "fail", "detail": render_detail})

    # Safe-zone is layout-contract only — not a pixel measurement (report as warn, not fake pass)
    checks.append(
        {
            "name": "elements_within_safe_zone",
            "status": "warn",
            "detail": f"layout-contract only; SAFE margins {SAFE_AREA} — pixel OCR not implemented",
        }
    )

    # Quality Council-style ffprobe decode (same spirit as council video_quality)
    council = _quality_council_style_probe(video_path) if video_ok else {"status": "fail", "detail": "missing"}
    checks.append({"name": "quality_council_video_probe", **council})

    failed = [c for c in checks if c.get("status") == "fail"]
    return {
        "ok": not failed,
        "checks": checks,
        "failed_count": len(failed),
        "safe_area": SAFE_AREA,
        "source": "wealth_blueprint_qc+qa_gate.blackdetect+quality_council_probe_pattern",
    }


def _midframe_luma_check(video_path: Path) -> dict[str, Any]:
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        return {"name": "not_entirely_black", "status": "fail", "detail": "ffmpeg missing"}
    frame = video_path.with_suffix(".qc_mid.png")
    try:
        subprocess.run(
            [ffmpeg, "-y", "-ss", "1.0", "-i", str(video_path), "-frames:v", "1", str(frame)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if not frame.is_file():
            return {"name": "not_entirely_black", "status": "fail", "detail": "could not extract mid frame"}
        from PIL import Image

        image = Image.open(frame).convert("L")
        hist = image.histogram()
        mean = sum(i * hist[i] for i in range(256)) / max(1, sum(hist))
        status = "pass" if mean > 8 else "fail"
        return {"name": "not_entirely_black", "status": status, "detail": f"mean_luma={mean:.2f}"}
    except Exception as exc:  # noqa: BLE001
        return {"name": "not_entirely_black", "status": "fail", "detail": str(exc)}
    finally:
        frame.unlink(missing_ok=True)


def _quality_council_style_probe(video_path: Path) -> dict[str, Any]:
    """Mirror Quality Council video_quality decode/ffprobe checks for a single artifact."""
    ffprobe = resolve_ffprobe()
    if not ffprobe:
        return {"status": "fail", "detail": "ffprobe unavailable"}
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,codec_name,width,height",
                "-of",
                "json",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if proc.returncode != 0:
            return {"status": "fail", "detail": proc.stderr.strip()[:300] or "ffprobe failed"}
        payload = json.loads(proc.stdout or "{}")
        streams = payload.get("streams") or []
        has_video = any(s.get("codec_type") == "video" for s in streams)
        has_audio = any(s.get("codec_type") == "audio" for s in streams)
        ok = has_video and has_audio
        return {
            "status": "pass" if ok else "fail",
            "detail": f"streams={len(streams)} video={has_video} audio={has_audio}",
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "fail", "detail": str(exc)}
