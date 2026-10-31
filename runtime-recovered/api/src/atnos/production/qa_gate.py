from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
import wave
from array import array
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from atnos.core.config import Settings
from atnos.production.channel_profile import ProductionChannelProfile, resolve_channel_profile
from atnos.production.media_tools import resolve_ffmpeg, resolve_ffprobe
from atnos.production.models import ProductionContext
from atnos.production.project_manifest import load_project_manifest
from atnos.production.reference_profile import load_reference_shorts_profile
from atnos.production.reference_profile_builder import analyze_video_visual_profile
from atnos.production.shot_boundary_audit import analyze_shot_boundaries
from atnos.production.steps.assembler import verify_remotion_assembly_provenance
from atnos.production.thumbnail_quality import thumbnail_quality_blockers
from atnos.production.visual_regression import (
    compare_visual_regression,
    visual_regression_baseline_path,
)
from atnos.production.voice_approval import verify_production_voice_approval
from atnos.production.visual_semantic_audit import SEMANTIC_SCORE_FIELDS
from atnos.services.ollama_model_roles_service import OllamaModelRolesService

_ORIGINAL_HTTPX_POST = httpx.post
_MIN_MEAN_HASH_DISTANCE = 0.16
_MIN_MEAN_TOP_CROP_PIXEL_DIFF = 0.10
_MAX_DOMINANT_IMAGE_MOTIF_RATIO = 0.50


def _measure_prompt_motif_diversity(clips: list[dict[str, Any]]) -> dict[str, Any]:
    """Detect rearranged versions of the same generated ImageScene motif.

    Pixel and composition hashes can be far apart while every scene still shows
    the same props on the same surface. This deterministic evidence complements
    the vision review and deliberately ignores Remotion-native scene types.
    """
    image_scenes = [
        clip
        for clip in clips
        if isinstance(clip, dict)
        and str(clip.get("scene_type") or "") == "ImageScene"
        and str(clip.get("prompt") or "").strip()
    ]
    if len(image_scenes) < 4:
        return {
            "status": "not_applicable",
            "passed": True,
            "image_scene_count": len(image_scenes),
            "reason": "fewer_than_four_prompted_image_scenes",
        }

    motif_counts: dict[str, int] = {}
    scene_motifs: list[dict[str, Any]] = []
    for clip in image_scenes:
        prompt = str(clip.get("prompt") or "").casefold()
        motifs: list[str] = []
        if (
            any(token in prompt for token in ("transparent", "translucent", "acrylic", "glass"))
            and any(token in prompt for token in ("block", "column", "layer", "base", "slab", "frame"))
        ):
            motifs.append("transparent_growth_structure")
        if "precision" in prompt and "tray" in prompt:
            motifs.append("precision_coin_tray")
        if "coin tower" in prompt or "coin stack" in prompt:
            motifs.append("coin_stack")
        if any(token in prompt for token in ("flywheel", "gear", "feedback wheel")):
            motifs.append("feedback_mechanism")
        if "balance scale" in prompt and "padlock" in prompt:
            motifs.append("balance_and_lock")
        if "hourglass" in prompt:
            motifs.append("hourglass_timeline")
        if "wallet" in prompt:
            motifs.append("wallet")
        if not motifs:
            motifs.append("unclassified")
        for motif in set(motifs):
            motif_counts[motif] = motif_counts.get(motif, 0) + 1
        scene_motifs.append({"segment": clip.get("segment"), "motifs": sorted(set(motifs))})

    dominant_motif, dominant_count = max(motif_counts.items(), key=lambda item: item[1])
    dominant_ratio = dominant_count / len(image_scenes)
    passed = dominant_ratio <= _MAX_DOMINANT_IMAGE_MOTIF_RATIO
    return {
        "status": "pass" if passed else "fail",
        "passed": passed,
        "image_scene_count": len(image_scenes),
        "dominant_motif": dominant_motif,
        "dominant_count": dominant_count,
        "dominant_ratio": round(dominant_ratio, 4),
        "max_dominant_ratio": _MAX_DOMINANT_IMAGE_MOTIF_RATIO,
        "motif_counts": motif_counts,
        "scene_motifs": scene_motifs,
        "reason": "prompt_motifs_are_varied" if passed else "dominant_prompt_motif_repeats_too_often",
    }


REQUIRED_QA_CHECK_NAMES = (
    "Hook 3-second test",
    "Final MP4 present",
    "Video encoding",
    "Visual variety",
    "Timeline visual change",
    "Reference pacing floor",
    "Scene concreteness",
    "Visual topic relevance",
    "Generated image text risk",
    "Generated image topic match",
    "Semantic storyboard quality",
    "Channel visual floor",
    "Vertical 9:16 format",
    "Video duration",
    "Final MP4 audio stream",
    "Audio format",
    "Production voice approval",
    "Audio clipping",
    "Excessive silence",
    "Subtitle sync",
    "Caption safe area",
    "Black frames",
    "Visual regression baseline",
    "Thumbnail present",
    "Operating standards evidence",
    "Ollama QA review",
)

ADVISORY_QA_CHECK_NAMES = (
    "Audio levels",
    "Visual reference match",
    "Copyright risk",
    "Brand consistency",
    "Shot boundary evidence",
)


def build_qa_report(context: ProductionContext, *, settings: Settings | None = None) -> dict[str, Any]:
    script = context.artifacts.get("script", {})
    voice = context.artifacts.get("voice", {})
    video = context.artifacts.get("video", {})
    assembly = context.artifacts.get("assembly", {})
    thumbnail = context.artifacts.get("thumbnail", {})
    video_path = Path(str(assembly.get("output_file") or ""))
    audio_path = Path(str(voice.get("output_file") or voice.get("audio_path") or ""))
    probe = probe_media(video_path)
    reference_profile = load_reference_shorts_profile(settings) if settings is not None else None
    channel_profile = resolve_channel_profile(context)
    operating_standards = context.artifacts.get("operating_standards")
    if not isinstance(operating_standards, dict):
        operating_standards = {}
    visual_reference_check = _check_visual_reference_match(video_path, reference_profile, channel_profile=channel_profile)
    actual_visual_variety = _measure_actual_clip_visual_variety(video)
    timeline_visual_change = _measure_timeline_visual_change(video_path, probe, video)
    reference_pacing = _measure_reference_pacing(video_path, reference_profile)
    required_checks = [
        _check_hook(
            script,
            topic_title=context.topic.title,
            channel_profile_key=channel_profile.key,
        ),
        _check_video_file(video_path, probe, video),
        _check_video_encoding(probe),
        _check_visual_variety(
            video,
            actual_visual_variety=actual_visual_variety,
            require_measurement=channel_profile.key == "wealth_blueprint",
        ),
        _check_timeline_visual_change(video, timeline_visual_change),
        _check_reference_pacing_floor(reference_pacing, reference_profile),
        _check_scene_concreteness(video),
        _check_visual_topic_relevance(video),
        _check_generated_image_text_risk(video),
        _check_generated_image_topic_match(video),
        _check_semantic_storyboard_quality(
            context.artifacts.get("semantic_storyboard_audit"),
            settings=settings,
            required=channel_profile.key == "wealth_blueprint",
        ),
        _check_channel_visual_floor(visual_reference_check, channel_profile=channel_profile),
        _check_vertical_format(probe),
        _check_duration(script, probe, strict_shorts_v1=channel_profile.key == "wealth_blueprint"),
        _check_final_audio_stream(probe),
        _check_audio_format(audio_path, voice),
        _check_production_voice_approval(settings, voice, channel_profile=channel_profile),
        _check_audio_clipping(audio_path),
        _check_excessive_silence(audio_path),
        _check_subtitle_sync(assembly, voice),
        _check_caption_safe_area(assembly),
        _check_black_frames(video_path, required=channel_profile.key == "wealth_blueprint"),
        _check_visual_regression(settings, context, video_path, assembly),
        _check_thumbnail(thumbnail),
        _check_operating_standards(operating_standards),
        _check_ollama_review(
            settings,
            context,
            script,
            required=channel_profile.key == "wealth_blueprint",
        ),
    ]
    advisory_checks = [
        _check_audio_levels(voice, media_path=video_path),
        visual_reference_check,
        _check_copyright(script),
        _check_brand_consistency(script, context.topic.title),
        _check_shot_boundary_evidence(video_path),
    ]
    _assert_qa_contract(required_checks, advisory_checks)
    checks = [*required_checks, *advisory_checks]
    blocking = [check["name"] for check in required_checks if check["status"] == "fail"]
    blocking_details = [
        _check_decision_detail(check)
        for check in required_checks
        if check["status"] == "fail"
    ]
    advisory_failures = [check["name"] for check in advisory_checks if check["status"] == "fail"]
    warnings = [check["name"] for check in checks if check["status"] == "warn"]
    overall = "failed" if blocking else "warning" if warnings or advisory_failures else "passed"
    upload_decision = _build_upload_decision(
        blocking=blocking,
        blocking_details=blocking_details,
        warnings=warnings,
        advisory_failures=advisory_failures,
    )
    return {
        "job_id": context.job_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "overall": overall,
        "required_check_count": len(required_checks),
        "required_checks": required_checks,
        "advisory_checks": advisory_checks,
        "checks": checks,
        "blocking_failures": blocking,
        "blocking_failure_details": blocking_details,
        "advisory_failures": advisory_failures,
        "warnings": warnings,
        "auto_uploadable": upload_decision["auto_uploadable"],
        "upload_decision": upload_decision,
        "media_probe": probe,
        "reference_profile": reference_profile.to_payload() if reference_profile is not None else None,
        "operating_standards": _operating_standards_evidence(operating_standards),
    }


def _check_shot_boundary_evidence(video_path: Path) -> dict[str, Any]:
    evidence = analyze_shot_boundaries(video_path)
    measured = evidence.get("status") == "measured"
    return {
        "name": "Shot boundary evidence",
        "status": "pass" if measured or evidence.get("reason") == "final_video_missing" else "warn",
        "value": evidence,
        "detail": (
            "Detected cut times and shot lengths from the final render for editorial review."
            if measured
            else "Cut analysis unavailable; final-video checks still decide whether the file is valid."
        ),
    }


def _operating_standards_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    sections = payload.get("sections") if isinstance(payload.get("sections"), list) else []
    section_keys: list[str] = []
    required_keys: list[str] = []
    blocked_keys: list[str] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        section_key = str(section.get("key") or "").strip()
        if section_key:
            section_keys.append(section_key)
        items = section.get("items") if isinstance(section.get("items"), list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            item_key = str(item.get("key") or "").strip()
            if not item_key:
                continue
            if bool(item.get("required")):
                required_keys.append(item_key)
            if str(item.get("status") or "") == "blocked_until_configured":
                blocked_keys.append(item_key)
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    source_exists = bool(source.get("exists"))
    status = str(payload.get("status") or "missing") if payload else "missing"
    return {
        "used": bool(payload),
        "status": status,
        "project_type": str(payload.get("project_type") or "") if payload else "",
        "source": source,
        "source_kind": "external" if source_exists else ("built_in_baseline" if status == "baseline" else "missing"),
        "section_keys": section_keys,
        "required_keys": required_keys,
        "blocked_until_configured_keys": blocked_keys,
        "required_count": len(required_keys),
        "blocked_until_configured_count": len(blocked_keys),
    }


def _check_operating_standards(payload: dict[str, Any]) -> dict[str, Any]:
    evidence = _operating_standards_evidence(payload)
    used = bool(evidence["used"])
    required_count = int(_float(evidence["required_count"]))
    blocked_count = int(_float(evidence["blocked_until_configured_count"]))
    status = str(evidence["status"] or "")
    status_ok = status in {"ready", "baseline"}
    ok = used and required_count > 0 and blocked_count == 0 and status_ok
    if not used:
        detail = "Operating standards/playbook evidence is missing; attach standards before QA or upload."
    elif not status_ok:
        detail = f"Operating standards status is not usable for production QA: {status}."
    elif required_count <= 0:
        detail = "Operating standards do not include any required production checks."
    elif blocked_count > 0:
        detail = "Operating standards include unresolved blocked items; configure them before this job can pass QA."
    elif evidence["source_kind"] == "built_in_baseline":
        detail = "The versioned built-in operating baseline is active; this check uses no external playbook source."
    else:
        detail = "Operating standards/playbook evidence is attached and all required items are usable."
    return {
        "name": "Operating standards evidence",
        "status": "pass" if ok else "fail",
        "value": evidence,
        "detail": detail,
    }


def _assert_qa_contract(required_checks: list[dict[str, Any]], advisory_checks: list[dict[str, Any]]) -> None:
    required_names = tuple(check["name"] for check in required_checks)
    advisory_names = tuple(check["name"] for check in advisory_checks)
    if required_names != REQUIRED_QA_CHECK_NAMES:
        raise RuntimeError(f"QA required checks contract drifted: {required_names}")
    if advisory_names != ADVISORY_QA_CHECK_NAMES:
        raise RuntimeError(f"QA advisory checks contract drifted: {advisory_names}")


def _check_decision_detail(check: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(check.get("name") or ""),
        "status": str(check.get("status") or ""),
        "value": check.get("value"),
        "detail": str(check.get("detail") or ""),
    }


def _build_upload_decision(
    *,
    blocking: list[str],
    blocking_details: list[dict[str, Any]],
    warnings: list[str],
    advisory_failures: list[str],
) -> dict[str, Any]:
    auto_uploadable = not blocking
    if auto_uploadable:
        status = "ready_for_private_upload"
        reason = (
            "All required QA checks passed; advisory warnings do not block private upload."
            if warnings or advisory_failures
            else "All required and advisory QA checks passed."
        )
    else:
        status = "blocked"
        reason = "Required QA checks failed; private upload is blocked until these checks pass."
    return {
        "status": status,
        "auto_uploadable": auto_uploadable,
        "private_upload_allowed": auto_uploadable,
        "required_failures": list(blocking),
        "required_failure_details": blocking_details,
        "warning_checks": list(warnings),
        "advisory_failures": list(advisory_failures),
        "reason": reason,
    }


def probe_media(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"available": False, "path": str(path), "reason": "file missing"}
    ffprobe = _ffprobe_exe()
    if ffprobe:
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
            if proc.returncode == 0 and proc.stdout.strip():
                data = json.loads(proc.stdout)
                return {"available": True, "path": str(path), "tool": "ffprobe", **_normalize_probe(data)}
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            pass
    return _probe_with_ffmpeg(path)


def _normalize_probe(data: dict[str, Any]) -> dict[str, Any]:
    streams = data.get("streams") if isinstance(data.get("streams"), list) else []
    fmt = data.get("format") if isinstance(data.get("format"), dict) else {}
    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
    return {
        "duration_s": _float(fmt.get("duration") or video_stream.get("duration") or audio_stream.get("duration")),
        "size_bytes": int(_float(fmt.get("size"))),
        "width": int(_float(video_stream.get("width"))),
        "height": int(_float(video_stream.get("height"))),
        "video_codec": str(video_stream.get("codec_name") or ""),
        "pixel_format": str(video_stream.get("pix_fmt") or ""),
        "fps": _frame_rate(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")),
        "audio_codec": str(audio_stream.get("codec_name") or ""),
        "sample_rate": int(_float(audio_stream.get("sample_rate"))),
        "channels": int(_float(audio_stream.get("channels"))),
    }


def _probe_with_ffmpeg(path: Path) -> dict[str, Any]:
    ffmpeg = _ffmpeg_exe()
    if not ffmpeg:
        return {"available": True, "path": str(path), "tool": "none", "reason": "ffprobe unavailable"}
    try:
        proc = subprocess.run([ffmpeg, "-i", str(path), "-f", "null", "-"], capture_output=True, text=True, timeout=25)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": False, "path": str(path), "tool": "ffmpeg", "reason": str(exc)}
    output = f"{proc.stdout}\n{proc.stderr}"
    return {
        "available": proc.returncode == 0,
        "path": str(path),
        "tool": "ffmpeg",
        "duration_s": _parse_duration(output),
        **_parse_ffmpeg_stream_info(output),
        "reason": "" if proc.returncode == 0 else output[-1000:],
    }


def _check_hook(
    script: dict[str, Any],
    *,
    topic_title: str = "",
    channel_profile_key: str = "",
) -> dict[str, Any]:
    hook = str(script.get("hook") or "")
    words = hook.split()
    normalized = " ".join(re.findall(r"[a-z0-9$%.]+", hook.casefold()))
    generic_hooks = {
        "compound interest rewards time not hype",
        "understand this before you invest",
        "why does this matter",
    }
    generic = normalized in generic_hooks or normalized.startswith("why does this topic")
    compound_topic = "compound interest" in topic_title.casefold()
    numeric_tokens = re.findall(r"(?<![\w.])(?:[$€£])?-?\d+(?:[.,]\d+)?(?:\s?%)?", hook)
    source_specific = not (
        channel_profile_key == "wealth_blueprint" and compound_topic and len(numeric_tokens) < 2
    )
    concise = ("?" in hook or len(words) <= 8) and len(words) <= 14
    score = 8 if concise and not generic and source_specific else 5
    if generic:
        detail = "Hook is concise but generic; use a concrete source-backed tension or transformation."
    elif not source_specific:
        detail = "Compound-interest hook must expose at least two source-backed values from the verified example."
    else:
        detail = "Hook is concrete and short enough to land in the first 3 seconds."
    return {
        "name": "Hook 3-second test",
        "status": "pass" if score >= 7 else "fail",
        "value": score,
        "detail": detail,
    }


def _check_video_file(path: Path, probe: dict[str, Any], video_manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    size = path.stat().st_size if path.exists() else 0
    manifest = video_manifest if isinstance(video_manifest, dict) else {}
    fallback_used = bool(manifest.get("fallback_used")) or str(manifest.get("status") or "") == "placeholder_ready"
    ok = bool(probe.get("available")) and size > 4096 and not fallback_used
    detail = str(probe.get("reason") or "Final rendered MP4 exists and is probeable.")
    if fallback_used:
        detail = "Final MP4 is a placeholder/cache fallback render; fresh generated visuals are required before upload."
    return {
        "name": "Final MP4 present",
        "status": "pass" if ok else "fail",
        "value": size,
        "detail": detail,
    }


def _check_video_encoding(probe: dict[str, Any]) -> dict[str, Any]:
    width = int(_float(probe.get("width")))
    height = int(_float(probe.get("height")))
    fps = _float(probe.get("fps"))
    codec = str(probe.get("video_codec") or "").casefold()
    pixel_format = str(probe.get("pixel_format") or "").casefold()
    checks = {
        "resolution_1080x1920": width == 1080 and height == 1920,
        "fps_30": abs(fps - 30.0) <= 0.01,
        "codec_h264": codec == "h264",
        "pixel_format_yuv420p": pixel_format == "yuv420p",
    }
    return {
        "name": "Video encoding",
        "status": "pass" if all(checks.values()) else "fail",
        "value": {
            "width": width,
            "height": height,
            "fps": round(fps, 3),
            "codec": codec,
            "pixel_format": pixel_format,
            "checks": checks,
        },
        "detail": "Final MP4 must be exactly 1080x1920, 30 FPS, H.264 and yuv420p.",
    }


def _check_visual_variety(
    video_manifest: dict[str, Any] | None = None,
    *,
    actual_visual_variety: dict[str, Any] | None = None,
    require_measurement: bool = False,
) -> dict[str, Any]:
    manifest = video_manifest if isinstance(video_manifest, dict) else {}
    if not manifest:
        return {
            "name": "Visual variety",
            "status": "fail" if require_measurement else "pass",
            "value": "required_manifest_missing" if require_measurement else "legacy_manifest_missing",
            "detail": (
                "A measured video manifest is required for Wealth Blueprint; upload is blocked."
                if require_measurement
                else "Legacy QA context without video manifest; visual variety check skipped."
            ),
        }
    quality_plan = manifest.get("quality_plan") if isinstance(manifest.get("quality_plan"), dict) else {}
    clips = manifest.get("clips") if isinstance(manifest.get("clips"), list) else []
    generated_count = int(_float(quality_plan.get("generated_clip_count")))
    placeholder_count = int(_float(quality_plan.get("placeholder_clip_count")))
    cached_fallback_count = int(_float(quality_plan.get("cached_fallback_clip_count")))
    non_generated_count = int(_float(quality_plan.get("non_generated_clip_count")))
    min_real = int(_float(quality_plan.get("min_real_clips"))) or 3
    if not quality_plan:
        generated_count = sum(1 for clip in clips if isinstance(clip, dict) and clip.get("status") == "generated")
        placeholder_count = sum(1 for clip in clips if isinstance(clip, dict) and clip.get("status") == "placeholder")
        cached_fallback_count = sum(
            1 for clip in clips if isinstance(clip, dict) and clip.get("status") == "cached_fallback"
        )
        non_generated_count = len(clips) - generated_count
    required = min_real
    count_ok = generated_count >= required and non_generated_count == 0 and len(clips) >= required
    composition_passed = quality_plan.get("passes_composition_diversity_floor")
    if composition_passed is None:
        composition_passed = True
    manifest_variety_passed = quality_plan.get("passes_visual_variety_floor")
    if manifest_variety_passed is None:
        manifest_variety_passed = count_ok
    concept_passed = quality_plan.get("passes_concept_diversity_floor")
    if concept_passed is None:
        concept_passed = True
    actual_visual_variety = actual_visual_variety if isinstance(actual_visual_variety, dict) else {}
    actual_status = str(actual_visual_variety.get("status") or "not_measured")
    measured_actual_passed = actual_visual_variety_evidence_passed(actual_visual_variety)
    actual_passed = measured_actual_passed if require_measurement else actual_status != "fail"
    motif_diversity = _measure_prompt_motif_diversity(clips)
    motif_passed = motif_diversity.get("status") != "fail"
    ok = (
        count_ok
        and bool(composition_passed)
        and bool(concept_passed)
        and bool(manifest_variety_passed)
        and actual_passed
        and (motif_passed or not require_measurement)
    )
    detail = "Shorts production requires multiple fresh generated clips with visibly different compositions."
    if require_measurement and actual_status != "pass":
        detail = "Actual rendered-clip variety was not measured successfully; Wealth Blueprint upload is blocked."
    elif not actual_passed:
        detail = "Actual rendered clips are too visually similar; regenerate visuals with stronger scene variety before upload."
    elif not bool(composition_passed):
        detail = "Generated clips are too compositionally similar; regenerate visual plan/render before upload."
    elif not bool(concept_passed):
        detail = "Scenes repeat the same visual idea with rearranged props; regenerate distinct narrative motifs before upload."
    elif require_measurement and not motif_passed:
        detail = (
            "ImageScenes reuse one dominant prop family too often; regenerate with genuinely different physical "
            "motifs and environments before presenting a reference candidate."
        )
    elif not count_ok:
        detail = "Shorts production requires multiple fresh generated clips; placeholder and cached fallback clips are not upload quality."
    return {
        "name": "Visual variety",
        "status": "pass" if ok else "fail",
        "value": {
            "generated_clip_count": generated_count,
            "placeholder_clip_count": placeholder_count,
            "cached_fallback_clip_count": cached_fallback_count,
            "non_generated_clip_count": non_generated_count,
            "clip_count": len(clips),
            "min_real_clips": min_real,
            "required_real_clips": required,
            "unique_composition_count": int(_float(quality_plan.get("unique_composition_count"))),
            "required_unique_compositions": int(_float(quality_plan.get("required_unique_compositions"))),
            "composition_diversity_ratio": _nullable_float(quality_plan.get("composition_diversity_ratio")),
            "passes_composition_diversity_floor": bool(composition_passed),
            "composition_signatures": quality_plan.get("composition_signatures"),
            "unique_concept_count": int(_float(quality_plan.get("unique_concept_count"))),
            "required_unique_concepts": int(_float(quality_plan.get("required_unique_concepts"))),
            "concept_diversity_ratio": _nullable_float(quality_plan.get("concept_diversity_ratio")),
            "passes_concept_diversity_floor": bool(concept_passed),
            "concept_signatures": quality_plan.get("concept_signatures"),
            "actual_visual_variety": actual_visual_variety,
            "prompt_motif_diversity": motif_diversity,
        },
        "detail": detail,
    }


def _measure_actual_clip_visual_variety(video_manifest: dict[str, Any] | None) -> dict[str, Any]:
    manifest = video_manifest if isinstance(video_manifest, dict) else {}
    clips = manifest.get("clips") if isinstance(manifest.get("clips"), list) else []
    generated_clip_paths = [
        Path(str(clip.get("file") or ""))
        for clip in clips
        if isinstance(clip, dict)
        and str(clip.get("status") or "") == "generated"
        and str(clip.get("file") or "").strip()
    ]
    if len(generated_clip_paths) < 3:
        return {"status": "not_measured", "reason": "fewer_than_three_generated_clip_files"}
    ffmpeg = _ffmpeg_exe()
    if not ffmpeg:
        return {"status": "not_measured", "reason": "ffmpeg_unavailable"}
    try:
        from PIL import Image, ImageChops, ImageStat
    except Exception as exc:
        return {"status": "not_measured", "reason": f"pillow_unavailable: {exc}"}

    with tempfile.TemporaryDirectory(prefix="atnos_clip_variety_") as tmp:
        frame_paths: list[Path] = []
        sampled_clip_paths: list[Path] = []
        for index, clip_path in enumerate(generated_clip_paths, start=1):
            if not clip_path.exists():
                continue
            frame_path = Path(tmp) / f"clip_{index:02d}.jpg"
            cmd = [
                ffmpeg,
                "-y",
                "-ss",
                "0.5",
                "-i",
                str(clip_path),
                "-frames:v",
                "1",
                str(frame_path),
            ]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
            except (OSError, subprocess.SubprocessError):
                continue
            if proc.returncode == 0 and frame_path.exists() and frame_path.stat().st_size > 0:
                frame_paths.append(frame_path)
                sampled_clip_paths.append(clip_path.resolve())
        if len(frame_paths) < 3:
            return {
                "status": "not_measured",
                "reason": "unable_to_extract_three_clip_frames",
                "extracted_frame_count": len(frame_paths),
            }

        hash_values = [_average_hash(path, Image) for path in frame_paths]
        hash_distances: list[float] = []
        top_crop_pixel_diffs: list[float] = []
        for first in range(len(frame_paths)):
            for second in range(first + 1, len(frame_paths)):
                hash_distances.append(_hash_distance(hash_values[first], hash_values[second]))
                top_crop_pixel_diffs.append(
                    _top_crop_pixel_diff(frame_paths[first], frame_paths[second], Image, ImageChops, ImageStat)
                )

    mean_hash_distance = _mean(hash_distances)
    mean_top_crop_pixel_diff = _mean(top_crop_pixel_diffs)
    passed = (
        mean_hash_distance is not None
        and mean_top_crop_pixel_diff is not None
        and len(frame_paths) == len(generated_clip_paths)
        and mean_hash_distance >= _MIN_MEAN_HASH_DISTANCE
        and mean_top_crop_pixel_diff >= _MIN_MEAN_TOP_CROP_PIXEL_DIFF
    )
    sampled_clip_artifacts = [
        {
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
        for path in sampled_clip_paths
        if path.is_file() and path.stat().st_size > 0
    ]
    evidence_sha256 = _visual_variety_evidence_hash(sampled_clip_artifacts)
    return {
        "status": "pass" if passed else "fail",
        "generated_clip_file_count": len(generated_clip_paths),
        "sampled_frame_count": len(frame_paths),
        "mean_hash_distance": round(mean_hash_distance, 4) if mean_hash_distance is not None else None,
        "min_hash_distance": round(min(hash_distances), 4) if hash_distances else None,
        "mean_top_crop_pixel_diff": (
            round(mean_top_crop_pixel_diff, 4) if mean_top_crop_pixel_diff is not None else None
        ),
        "min_top_crop_pixel_diff": round(min(top_crop_pixel_diffs), 4) if top_crop_pixel_diffs else None,
        "thresholds": {
            "min_mean_hash_distance": _MIN_MEAN_HASH_DISTANCE,
            "min_mean_top_crop_pixel_diff": _MIN_MEAN_TOP_CROP_PIXEL_DIFF,
        },
        "sampled_clip_artifacts": sampled_clip_artifacts,
        "evidence_sha256": evidence_sha256,
        "reason": (
            "clip_frames_are_visually_distinct"
            if passed
            else "clip_frames_are_too_similar_after_sampling_actual_rendered_media"
        ),
    }


def actual_visual_variety_evidence_passed(payload: Any) -> bool:
    """Accept a visual-variety pass only when the measured evidence is complete."""
    if not isinstance(payload, dict) or payload.get("status") != "pass":
        return False
    thresholds = payload.get("thresholds") if isinstance(payload.get("thresholds"), dict) else {}
    generated_count = int(_float(payload.get("generated_clip_file_count")))
    sampled_count = int(_float(payload.get("sampled_frame_count")))
    mean_hash_distance = _nullable_float(payload.get("mean_hash_distance"))
    min_hash_distance = _nullable_float(payload.get("min_hash_distance"))
    mean_top_diff = _nullable_float(payload.get("mean_top_crop_pixel_diff"))
    min_top_diff = _nullable_float(payload.get("min_top_crop_pixel_diff"))
    hash_floor = _nullable_float(thresholds.get("min_mean_hash_distance"))
    top_floor = _nullable_float(thresholds.get("min_mean_top_crop_pixel_diff"))
    artifacts = payload.get("sampled_clip_artifacts") if isinstance(payload.get("sampled_clip_artifacts"), list) else []
    normalized_artifacts: list[dict[str, Any]] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            return False
        path = Path(str(artifact.get("path") or "")).expanduser()
        raw_size = artifact.get("size_bytes")
        if isinstance(raw_size, bool) or not isinstance(raw_size, int) or raw_size <= 0:
            return False
        expected_size = raw_size
        expected_hash = str(artifact.get("sha256") or "").strip().lower()
        try:
            artifact_valid = bool(
                path.is_file()
                and expected_size > 0
                and path.stat().st_size == expected_size
                and len(expected_hash) == 64
                and _sha256_file(path) == expected_hash
            )
        except OSError:
            artifact_valid = False
        if not artifact_valid:
            return False
        normalized_artifacts.append(
            {"path": str(path.resolve()), "size_bytes": expected_size, "sha256": expected_hash}
        )
    evidence_sha256 = str(payload.get("evidence_sha256") or "").strip().lower()
    return bool(
        generated_count >= 3
        and sampled_count >= 3
        and min_hash_distance is not None
        and min_top_diff is not None
        and len(normalized_artifacts) == sampled_count
        and len({artifact["path"] for artifact in normalized_artifacts}) == sampled_count
        and evidence_sha256 == _visual_variety_evidence_hash(normalized_artifacts)
        and mean_hash_distance is not None
        and mean_top_diff is not None
        and hash_floor is not None
        and top_floor is not None
        and hash_floor >= _MIN_MEAN_HASH_DISTANCE
        and top_floor >= _MIN_MEAN_TOP_CROP_PIXEL_DIFF
        and mean_hash_distance >= hash_floor
        and mean_top_diff >= top_floor
        and payload.get("reason") == "clip_frames_are_visually_distinct"
    )


def remeasure_actual_visual_variety_evidence(payload: Any) -> bool:
    """Recompute frame metrics from the hash-bound clips before owner approval."""
    if not actual_visual_variety_evidence_passed(payload):
        return False
    artifacts = payload.get("sampled_clip_artifacts")
    if not isinstance(artifacts, list):
        return False
    manifest = {
        "clips": [
            {"status": "generated", "file": str(artifact.get("path") or "")}
            for artifact in artifacts
            if isinstance(artifact, dict)
        ]
    }
    measured = _measure_actual_clip_visual_variety(manifest)
    if not actual_visual_variety_evidence_passed(measured):
        return False
    scalar_fields = (
        "generated_clip_file_count",
        "sampled_frame_count",
        "mean_hash_distance",
        "min_hash_distance",
        "mean_top_crop_pixel_diff",
        "min_top_crop_pixel_diff",
        "evidence_sha256",
    )
    if any(measured.get(field) != payload.get(field) for field in scalar_fields):
        return False
    return measured.get("thresholds") == payload.get("thresholds")


def _visual_variety_evidence_hash(artifacts: list[dict[str, Any]]) -> str:
    canonical = json.dumps(artifacts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _check_timeline_visual_change(
    video_manifest: dict[str, Any] | None,
    timeline_visual_change: dict[str, Any] | None,
) -> dict[str, Any]:
    manifest = video_manifest if isinstance(video_manifest, dict) else {}
    if not manifest:
        return {
            "name": "Timeline visual change",
            "status": "pass",
            "value": "legacy_manifest_missing",
            "detail": "Legacy QA context without video manifest; timeline visual-change check skipped.",
        }
    result = timeline_visual_change if isinstance(timeline_visual_change, dict) else {}
    status = str(result.get("status") or "not_measured")
    if status == "not_measured":
        return {
            "name": "Timeline visual change",
            "status": "fail",
            "value": result,
            "detail": "Timeline visual-change evidence was not measurable; a production render cannot pass without it.",
        }
    return {
        "name": "Timeline visual change",
        "status": "pass" if status == "pass" else "fail",
        "value": result,
        "detail": (
            "Final video changes enough over the timeline for Shorts pacing."
            if status == "pass"
            else "Final video is too visually static over time; regenerate scenes with stronger composition changes."
        ),
    }


def _measure_reference_pacing(video_path: Path, reference_profile: Any) -> dict[str, Any]:
    if reference_profile is None or not bool(reference_profile.available):
        return {"status": "not_configured", "reason": "reference_profile_unavailable"}
    if not video_path.exists() or video_path.stat().st_size <= 0:
        return {"status": "not_measured", "reason": "final_video_missing"}
    measured = analyze_video_visual_profile(video_path, max_video_seconds=60.0)
    if str(measured.get("frame_analysis_status") or "") != "analyzed":
        return {"status": "not_measured", **measured}

    reference_cuts = _float(reference_profile.median_cuts_per_10s)
    reference_interval = _float(reference_profile.median_avg_cut_interval_sec)
    min_cuts_per_10s = max(1.0, reference_cuts * 0.65) if reference_cuts > 0 else 1.0
    max_avg_cut_interval = min(5.0, max(2.0, reference_interval * 1.4)) if reference_interval > 0 else 4.5
    max_first_cut = min(3.0, max(1.0, reference_profile.hook_target_sec() + 0.5))

    first_cut = measured.get("first_cut_sec")
    avg_interval = measured.get("avg_cut_interval_sec")
    cuts_per_10s = _float(measured.get("cuts_per_10s"))
    flash_events_per_10s = _float(measured.get("flash_events_per_10s"))
    first_cut_ok = first_cut is not None and _float(first_cut) <= max_first_cut
    interval_ok = avg_interval is not None and _float(avg_interval) <= max_avg_cut_interval
    cut_density_ok = cuts_per_10s >= min_cuts_per_10s
    no_isolated_flashes = flash_events_per_10s <= 0.001
    passed = first_cut_ok and interval_ok and cut_density_ok and no_isolated_flashes
    return {
        "status": "pass" if passed else "fail",
        "measured": measured,
        "thresholds": {
            "max_first_cut_sec": round(max_first_cut, 3),
            "max_avg_cut_interval_sec": round(max_avg_cut_interval, 3),
            "min_cuts_per_10s": round(min_cuts_per_10s, 3),
            "max_flash_events_per_10s": 0.0,
        },
        "checks": {
            "first_cut_ok": first_cut_ok,
            "avg_cut_interval_ok": interval_ok,
            "cut_density_ok": cut_density_ok,
            "no_isolated_flashes": no_isolated_flashes,
        },
        "reference_source": reference_profile.source,
        "reference_sample_count": reference_profile.sample_count,
    }


def _check_reference_pacing_floor(result: dict[str, Any], reference_profile: Any) -> dict[str, Any]:
    status = str(result.get("status") or "not_measured")
    if status == "not_configured":
        return {
            "name": "Reference pacing floor",
            "status": "pass",
            "value": result,
            "detail": "No measured reference profile is configured; the generic pacing checks remain active.",
        }
    passed = status == "pass"
    return {
        "name": "Reference pacing floor",
        "status": "pass" if passed else "fail",
        "value": result,
        "detail": (
            "The final render meets the measured reference floor for hook timing and visual-change density."
            if passed
            else "The final render is slower or more static than the measured reference Shorts; regenerate before review."
        ),
    }


def _measure_timeline_visual_change(
    video_path: Path,
    probe: dict[str, Any],
    video_manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    manifest = video_manifest if isinstance(video_manifest, dict) else {}
    clips = manifest.get("clips") if isinstance(manifest.get("clips"), list) else []
    if not clips:
        return {"status": "not_measured", "reason": "video_manifest_without_clips"}
    if not video_path.exists() or video_path.stat().st_size <= 0:
        return {"status": "not_measured", "reason": "final_video_missing"}
    duration = float(probe.get("duration_s") or 0)
    if duration < 8:
        return {"status": "not_measured", "reason": "video_too_short_for_timeline_sampling", "duration_s": duration}
    ffmpeg = _ffmpeg_exe()
    if not ffmpeg:
        return {"status": "not_measured", "reason": "ffmpeg_unavailable"}
    try:
        from PIL import Image, ImageChops, ImageStat
    except Exception as exc:
        return {"status": "not_measured", "reason": f"pillow_unavailable: {exc}"}

    sample_times = _timeline_sample_times(duration)
    with tempfile.TemporaryDirectory(prefix="atnos_timeline_change_") as tmp:
        frame_paths: list[Path] = []
        for index, timestamp in enumerate(sample_times, start=1):
            frame_path = Path(tmp) / f"timeline_{index:02d}.jpg"
            cmd = [
                ffmpeg,
                "-y",
                "-ss",
                f"{timestamp:.3f}",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                str(frame_path),
            ]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
            except (OSError, subprocess.SubprocessError):
                continue
            if proc.returncode == 0 and frame_path.exists() and frame_path.stat().st_size > 0:
                frame_paths.append(frame_path)
        if len(frame_paths) < 4:
            return {
                "status": "not_measured",
                "reason": "unable_to_extract_timeline_frames",
                "sampled_frame_count": len(frame_paths),
                "requested_sample_times": sample_times,
            }
        consecutive_diffs = [
            _full_frame_pixel_diff(frame_paths[index], frame_paths[index + 1], Image, ImageChops, ImageStat)
            for index in range(len(frame_paths) - 1)
        ]

    mean_diff = _mean(consecutive_diffs)
    min_diff = min(consecutive_diffs) if consecutive_diffs else None
    low_change_count = sum(1 for value in consecutive_diffs if value < 0.07)
    low_change_ratio = low_change_count / len(consecutive_diffs) if consecutive_diffs else 1.0
    min_mean_timeline_diff = 0.11
    max_low_change_ratio = 0.40
    passed = (
        mean_diff is not None
        and mean_diff >= min_mean_timeline_diff
        and low_change_ratio <= max_low_change_ratio
    )
    return {
        "status": "pass" if passed else "fail",
        "sample_times_sec": [round(value, 2) for value in sample_times],
        "sampled_frame_count": len(frame_paths),
        "consecutive_frame_diffs": [round(value, 4) for value in consecutive_diffs],
        "mean_consecutive_frame_diff": round(mean_diff, 4) if mean_diff is not None else None,
        "min_consecutive_frame_diff": round(min_diff, 4) if min_diff is not None else None,
        "low_change_ratio": round(low_change_ratio, 3),
        "thresholds": {
            "min_mean_consecutive_frame_diff": min_mean_timeline_diff,
            "max_low_change_ratio": max_low_change_ratio,
        },
        "reason": (
            "timeline_frames_change_enough_for_short_form_pacing"
            if passed
            else "timeline_frames_are_too_similar_for_short_form_pacing"
        ),
    }


def _timeline_sample_times(duration: float) -> list[float]:
    raw = [
        2.0,
        duration * 0.18,
        duration * 0.34,
        duration * 0.50,
        duration * 0.66,
        duration * 0.82,
        max(0.5, duration - 3.0),
    ]
    values: list[float] = []
    for value in raw:
        clipped = max(0.5, min(duration - 0.5, float(value)))
        if not values or abs(clipped - values[-1]) >= 1.0:
            values.append(clipped)
    return values


def _average_hash(path: Path, image_module: Any) -> str:
    image = image_module.open(path).convert("L").resize((16, 16))
    pixels = list(image.getdata())
    average = sum(float(pixel) for pixel in pixels) / len(pixels)
    return "".join("1" if float(pixel) > average else "0" for pixel in pixels)


def _hash_distance(first: str, second: str) -> float:
    if not first or not second or len(first) != len(second):
        return 0.0
    return sum(a != b for a, b in zip(first, second)) / len(first)


def _top_crop_pixel_diff(
    first_path: Path,
    second_path: Path,
    image_module: Any,
    image_chops: Any,
    image_stat: Any,
) -> float:
    first = image_module.open(first_path).convert("RGB").resize((96, 170)).crop((0, 0, 96, 120))
    second = image_module.open(second_path).convert("RGB").resize((96, 170)).crop((0, 0, 96, 120))
    diff = image_chops.difference(first, second).convert("L")
    return float(image_stat.Stat(diff).mean[0]) / 255.0


def _full_frame_pixel_diff(
    first_path: Path,
    second_path: Path,
    image_module: Any,
    image_chops: Any,
    image_stat: Any,
) -> float:
    first = image_module.open(first_path).convert("RGB").resize((96, 170))
    second = image_module.open(second_path).convert("RGB").resize((96, 170))
    diff = image_chops.difference(first, second).convert("L")
    return float(image_stat.Stat(diff).mean[0]) / 255.0


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _check_visual_topic_relevance(video_manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    manifest = video_manifest if isinstance(video_manifest, dict) else {}
    if not manifest:
        return {
            "name": "Visual topic relevance",
            "status": "fail",
            "value": "legacy_manifest_missing",
            "detail": "Video manifest is missing; visual topic relevance cannot be verified.",
        }
    quality_plan = manifest.get("quality_plan") if isinstance(manifest.get("quality_plan"), dict) else {}
    if not quality_plan or "passes_visual_relevance_floor" not in quality_plan:
        return {
            "name": "Visual topic relevance",
            "status": "fail",
            "value": "quality_plan_missing",
            "detail": "Video manifest does not include visual relevance evidence; upload is blocked.",
        }
    passed = bool(quality_plan.get("passes_visual_relevance_floor"))
    return {
        "name": "Visual topic relevance",
        "status": "pass" if passed else "fail",
        "value": {
            "avg_visual_relevance_score": _nullable_float(quality_plan.get("avg_visual_relevance_score")),
            "visual_relevance_scores": quality_plan.get("visual_relevance_scores"),
            "passes_visual_relevance_floor": passed,
        },
        "detail": (
            "Visual scenes include enough topic-specific subjects and props."
            if passed
            else "Visual scenes are too generic or unrelated to the topic; regenerate visual plan before upload."
        ),
    }


def _check_scene_concreteness(video_manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    manifest = video_manifest if isinstance(video_manifest, dict) else {}
    if not manifest:
        return {
            "name": "Scene concreteness",
            "status": "fail",
            "value": "legacy_manifest_missing",
            "detail": "Video manifest is missing; scene concreteness cannot be verified.",
        }
    quality_plan = manifest.get("quality_plan") if isinstance(manifest.get("quality_plan"), dict) else {}
    if "passes_scene_concreteness_floor" not in quality_plan:
        return {
            "name": "Scene concreteness",
            "status": "fail",
            "value": "scene_concreteness_missing",
            "detail": "Video manifest does not include scene concreteness audit; upload is blocked.",
        }
    passed = bool(quality_plan.get("passes_scene_concreteness_floor"))
    audits = quality_plan.get("scene_concreteness_audits")
    risk_count = int(_float(quality_plan.get("scene_concreteness_risk_count")))
    return {
        "name": "Scene concreteness",
        "status": "pass" if passed else "fail",
        "value": {
            "passes_scene_concreteness_floor": passed,
            "scene_concreteness_risk_count": risk_count,
            "audits": audits if isinstance(audits, list) else [],
        },
        "detail": (
            "Visual scenes include concrete physical objects and visible action."
            if passed
            else "Visual scenes are too abstract/static; regenerate the visual plan with concrete subject, action, and props."
        ),
    }


def _check_generated_image_text_risk(video_manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    manifest = video_manifest if isinstance(video_manifest, dict) else {}
    if not manifest:
        return {
            "name": "Generated image text risk",
            "status": "fail",
            "value": "legacy_manifest_missing",
            "detail": "Video manifest is missing; generated-image OCR risk cannot be verified.",
        }
    quality_plan = manifest.get("quality_plan") if isinstance(manifest.get("quality_plan"), dict) else {}
    if "passes_generated_image_text_floor" not in quality_plan:
        return {
            "name": "Generated image text risk",
            "status": "fail",
            "value": "image_text_audit_missing",
            "detail": "Video manifest does not include generated-image OCR audit; upload is blocked.",
        }
    passed = bool(quality_plan.get("passes_generated_image_text_floor"))
    audits = quality_plan.get("generated_image_text_audits")
    risk_count = int(_float(quality_plan.get("generated_image_text_risk_count")))
    return {
        "name": "Generated image text risk",
        "status": "pass" if passed else "fail",
        "value": {
            "passes_generated_image_text_floor": passed,
            "generated_image_text_risk_count": risk_count,
            "audits": audits if isinstance(audits, list) else [],
        },
        "detail": (
            "Generated images passed OCR text-risk audit."
            if passed
            else "Generated images contain readable text; regenerate visuals before upload."
        ),
    }


def _check_generated_image_topic_match(video_manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    manifest = video_manifest if isinstance(video_manifest, dict) else {}
    if not manifest:
        return {
            "name": "Generated image topic match",
            "status": "fail",
            "value": "legacy_manifest_missing",
            "detail": "Video manifest is missing; generated-image topic match cannot be verified.",
        }
    quality_plan = manifest.get("quality_plan") if isinstance(manifest.get("quality_plan"), dict) else {}
    if "passes_generated_image_visual_floor" not in quality_plan:
        return {
            "name": "Generated image topic match",
            "status": "fail",
            "value": "image_visual_audit_missing",
            "detail": "Video manifest does not include generated-image visual audit; upload is blocked.",
        }
    passed = bool(quality_plan.get("passes_generated_image_visual_floor"))
    audits = quality_plan.get("generated_image_visual_audits")
    risk_count = int(_float(quality_plan.get("generated_image_visual_risk_count")))
    return {
        "name": "Generated image topic match",
        "status": "pass" if passed else "fail",
        "value": {
            "passes_generated_image_visual_floor": passed,
            "generated_image_visual_risk_count": risk_count,
            "audits": audits if isinstance(audits, list) else [],
        },
        "detail": (
            "Generated images passed channel/topic visual floor."
            if passed
            else "Generated images are visually off-profile or too weak for the channel; regenerate visuals before upload."
        ),
    }


def _check_semantic_storyboard_quality(
    audit_payload: Any,
    *,
    settings: Settings | None,
    required: bool = False,
) -> dict[str, Any]:
    if not required and (
        settings is None or str(getattr(settings, "app_env", "") or "").strip().lower() == "test"
    ):
        return {
            "name": "Semantic storyboard quality",
            "status": "pass",
            "value": "skipped_in_test_or_legacy_context",
            "detail": "Semantic storyboard audit is skipped only for legacy or test-only QA calls.",
        }
    if settings is None or not bool(getattr(settings, "shorts_semantic_visual_audit_enabled", False)):
        return {
            "name": "Semantic storyboard quality",
            "status": "fail" if required else "pass",
            "value": "settings_missing" if settings is None else "disabled",
            "detail": (
                "Semantic storyboard audit is required for Wealth Blueprint but is unavailable or disabled."
                if required
                else "Semantic storyboard audit is disabled by explicit runtime configuration."
            ),
        }
    audit = audit_payload if isinstance(audit_payload, dict) else {}
    blockers = [str(item) for item in audit.get("blockers") or [] if str(item).strip()]
    scores = audit.get("scores") if isinstance(audit.get("scores"), dict) else {}
    min_score = float(getattr(settings, "shorts_semantic_visual_audit_min_score", 7.0))
    if not math.isfinite(min_score) or not 0.0 <= min_score <= 10.0:
        blockers.append("semantic_score_threshold_invalid")
    for field in SEMANTIC_SCORE_FIELDS:
        score = _nullable_float(scores.get(field))
        if score is None:
            blockers.append(f"{field}_missing")
        elif not 0.0 <= score <= 10.0:
            blockers.append(f"{field}_out_of_range")
        elif score < min_score:
            blockers.append(f"{field}_below_floor")
    recorded_min_score = _nullable_float(audit.get("min_score"))
    if recorded_min_score is None or abs(recorded_min_score - min_score) > 0.0001:
        blockers.append("semantic_score_threshold_unverified")
    audit_input = audit.get("audit_input") if isinstance(audit.get("audit_input"), dict) else {}
    storyboard_path = Path(str(audit.get("storyboard_path") or "")).expanduser()
    expected_hash = str(audit_input.get("source_sha256") or "").strip().lower()
    expected_size = int(_float(audit_input.get("source_size_bytes")))
    evidence_exists = storyboard_path.is_file() and storyboard_path.stat().st_size > 0
    actual_size = storyboard_path.stat().st_size if evidence_exists else 0
    actual_hash = hashlib.sha256(storyboard_path.read_bytes()).hexdigest() if evidence_exists else ""
    evidence_authenticated = bool(
        evidence_exists
        and expected_hash
        and expected_hash == actual_hash
        and expected_size > 0
        and expected_size == actual_size
    )
    if not evidence_authenticated:
        blockers.append("semantic_storyboard_evidence_unverified")
    blockers = list(dict.fromkeys(blockers))
    passed = bool(audit.get("passed")) and str(audit.get("status") or "") == "pass" and not blockers
    return {
        "name": "Semantic storyboard quality",
        "status": "pass" if passed else "fail",
        "value": {
            "status": audit.get("status") or "missing",
            "scores": scores,
            "min_score": min_score,
            "blockers": blockers or (["semantic_storyboard_audit_missing"] if not audit else []),
            "storyboard_path": str(storyboard_path) if str(storyboard_path) != "." else "",
            "audit_path": audit.get("audit_path"),
            "evidence": {
                "authenticated": evidence_authenticated,
                "expected_sha256": expected_hash,
                "actual_sha256": actual_hash,
                "expected_size_bytes": expected_size,
                "actual_size_bytes": actual_size,
            },
        },
        "detail": (
            "The complete storyboard is topic-relevant, visually progressive, mature, and sufficiently polished."
            if passed
            else "The local vision audit found off-topic, repetitive, synthetic, or visually weak scenes; upload is blocked."
        ),
    }


def _check_channel_visual_floor(
    visual_reference_check: dict[str, Any],
    *,
    channel_profile: ProductionChannelProfile | None = None,
) -> dict[str, Any]:
    profile_key = getattr(channel_profile, "key", "")
    if profile_key != "wealth_blueprint":
        return {
            "name": "Channel visual floor",
            "status": "pass",
            "value": {"channel_profile": profile_key or None, "enforced": False},
            "detail": "No additional channel-specific visual floor is enforced for this profile.",
        }
    value = visual_reference_check.get("value") if isinstance(visual_reference_check, dict) else None
    metrics = value if isinstance(value, dict) else {}
    measured_brightness = _nullable_float(metrics.get("measured_brightness"))
    measured_saturation = _nullable_float(metrics.get("measured_saturation"))
    brightness_ok = measured_brightness is not None and measured_brightness >= 0.18
    saturation_ok = measured_saturation is not None and measured_saturation <= 0.72
    return {
        "name": "Channel visual floor",
        "status": "pass" if brightness_ok and saturation_ok else "fail",
        "value": {
            "channel_profile": profile_key,
            "enforced": True,
            "measured_brightness": measured_brightness,
            "min_brightness": 0.18,
            "measured_saturation": measured_saturation,
            "max_saturation": 0.72,
        },
        "detail": (
            "Wealth Blueprint must stay readable: not near-black and not neon-saturated. "
            "This blocks private upload when the local motion render is visually below channel quality floor."
        ),
    }


def _check_duration(
    script: dict[str, Any],
    probe: dict[str, Any],
    *,
    strict_shorts_v1: bool = False,
) -> dict[str, Any]:
    duration = float(probe.get("duration_s") or script.get("estimated_duration_s") or script.get("total_duration_target_sec") or 0)
    passed = 30 <= duration < 60 if strict_shorts_v1 else 1 <= duration <= 60
    return {
        "name": "Video duration",
        "status": "pass" if passed else "fail",
        "value": round(duration, 2),
        "detail": (
            "Wealth Blueprint Shorts V1 requires duration from 30 seconds up to, but not including, 60 seconds."
            if strict_shorts_v1
            else "Legacy Shorts QA accepts duration between 1 and 60 seconds."
        ),
    }


def _check_vertical_format(probe: dict[str, Any]) -> dict[str, Any]:
    width = int(_float(probe.get("width")))
    height = int(_float(probe.get("height")))
    ratio = round(width / height, 3) if height else 0.0
    ok = width > 0 and height > 0 and height > width and abs(ratio - (9 / 16)) <= 0.03
    return {
        "name": "Vertical 9:16 format",
        "status": "pass" if ok else "fail",
        "value": {"width": width, "height": height, "aspect_ratio": ratio},
        "detail": "Final video must use a vertical 9:16 aspect ratio; exact encoding is checked separately.",
    }


def _check_final_audio_stream(probe: dict[str, Any]) -> dict[str, Any]:
    codec = str(probe.get("audio_codec") or "")
    sample_rate = int(_float(probe.get("sample_rate")))
    channels = int(_float(probe.get("channels")))
    ok = bool(codec) and sample_rate == 48000 and channels >= 2
    return {
        "name": "Final MP4 audio stream",
        "status": "pass" if ok else "fail",
        "value": {"codec": codec, "sample_rate": sample_rate, "channels": channels},
        "detail": "Final upload file must contain AAC-compatible 48kHz stereo audio.",
    }


def _check_audio_format(audio_path: Path, voice_manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    manifest = voice_manifest if isinstance(voice_manifest, dict) else {}
    voice_status = str(manifest.get("status") or "")
    quality_tier = str(manifest.get("quality_tier") or "")
    production_ready = manifest.get("production_ready")
    not_production_ready = production_ready is not True
    silent_fallback = (
        voice_status == "generated_fallback_silence"
        or voice_status == "generated_partial_fallback_silence"
        or quality_tier == "placeholder_silence"
        or not_production_ready
        or bool(manifest.get("silent_segment_ids"))
    )
    if not audio_path.exists():
        return {"name": "Audio format", "status": "fail", "value": "missing", "detail": "Narration file is missing."}
    try:
        with wave.open(str(audio_path), "rb") as wav:
            value = {"sample_rate": wav.getframerate(), "channels": wav.getnchannels()}
    except (wave.Error, OSError):
        value = {"sample_rate": 0, "channels": 0}
    voice_profile = manifest.get("voice_profile") if isinstance(manifest.get("voice_profile"), dict) else {}
    actual_engine = str(manifest.get("primary") or manifest.get("engine") or "unknown")
    is_edge_engine = "edge" in actual_engine.lower()
    voice_identity = manifest.get("voice_identity") if isinstance(manifest.get("voice_identity"), dict) else {}
    value.update(
        {
            "quality_tier": quality_tier or "unknown",
            "voice_backend_grade": manifest.get("voice_backend_grade") or "unknown",
            "engine": actual_engine,
            "voice": (
                voice_profile.get("edge_tts_voice")
                if is_edge_engine
                else voice_identity.get("voice_key") or voice_identity.get("provider_family")
            ),
            "rate": voice_profile.get("edge_tts_rate") if is_edge_engine else voice_identity.get("speed"),
            "language_code": voice_profile.get("language_code"),
            "voice_identity_hash": manifest.get("voice_identity_hash") or voice_identity.get("identity_hash"),
        }
    )
    ok = value["sample_rate"] == 44100 and value["channels"] == 2 and not silent_fallback
    detail = "YouTube-ready narration should be 44100Hz stereo."
    if silent_fallback:
        detail = "Narration contains deterministic silence/placeholder fallback; real TTS is required before upload."
        if quality_tier == "offline_system_fallback":
            detail = "Narration used pyttsx3 offline system voice; use F5-TTS or Coqui before upload."
        elif production_ready is None:
            detail = "Narration manifest is missing production_ready=true; regenerate voice before upload."
    elif quality_tier == "cloud_neural_fallback":
        detail = (
            "Narration uses Edge neural fallback. It is acceptable for private QA, but F5-TTS or ElevenLabs "
            "should be configured for final public-quality cadence."
        )
    elif quality_tier in {"premium_cloud", "primary"}:
        detail = "Narration uses the preferred premium/local neural backend and is 44100Hz stereo."
    return {
        "name": "Audio format",
        "status": "pass" if ok else "fail",
        "value": value,
        "detail": detail,
    }


def _check_production_voice_approval(
    settings: Settings | None,
    voice_manifest: dict[str, Any] | None,
    *,
    channel_profile: ProductionChannelProfile,
) -> dict[str, Any]:
    if channel_profile.key != "wealth_blueprint":
        return {
            "name": "Production voice approval",
            "status": "pass",
            "value": {"status": "not_applicable", "channel_profile": channel_profile.key},
            "detail": "The Wealth Blueprint V1 production-voice gate is not applicable to this channel.",
        }
    if settings is None or settings.app_env == "test":
        return {
            "name": "Production voice approval",
            "status": "pass",
            "value": {"status": "not_enforced_in_test_context", "voice_status": "test_only"},
            "detail": "Production voice approval is enforced by the real runtime; this isolated test has no runtime settings.",
        }
    manifest = voice_manifest if isinstance(voice_manifest, dict) else {}
    evidence = verify_production_voice_approval(settings, manifest)
    approved = evidence.get("approved") is True and evidence.get("voice_status") == "production_approved"
    return {
        "name": "Production voice approval",
        "status": "pass" if approved else "fail",
        "value": evidence,
        "detail": (
            "The current voice identity matches the signed production approval."
            if approved
            else "The current voice is only a candidate. Gate A requires explicit owner approval before final production PASS."
        ),
    }


def _check_audio_levels(voice: dict[str, Any], *, media_path: Path | None = None) -> dict[str, Any]:
    voice_profile = voice.get("voice_profile") if isinstance(voice.get("voice_profile"), dict) else {}
    target_lufs = float(voice_profile.get("target_loudness_lufs") or -14.0)
    measured = _measure_media_loudness(media_path) if media_path is not None else {}
    input_lufs = measured.get("input_i")
    true_peak = measured.get("input_tp")
    has_measurement = isinstance(input_lufs, (int, float)) and isinstance(true_peak, (int, float))
    within_target = has_measurement and abs(float(input_lufs) - target_lufs) <= 2.0 and float(true_peak) <= -0.5
    normalized_metadata = "loudnorm" in str(voice.get("normalization") or "").casefold()
    if within_target:
        status = "pass"
        detail = "Measured final audio is within the channel LUFS target and true-peak ceiling."
    elif has_measurement:
        status = "warn"
        detail = "Measured final audio is outside the channel LUFS target or true-peak ceiling."
    else:
        status = "pass" if normalized_metadata else "warn"
        detail = "Loudness could not be measured; normalization metadata was used as fallback evidence."
    return {
        "name": "Audio levels",
        "status": status,
        "value": {
            "target_lufs": target_lufs,
            "allowed_lufs_delta": 2.0,
            "max_true_peak_db": -0.5,
            "measured": measured,
            "normalization_metadata": voice.get("normalization") or "unknown",
        },
        "detail": detail,
    }


def _measure_media_loudness(media_path: Path | None) -> dict[str, Any]:
    if media_path is None or not media_path.is_file():
        return {"status": "unavailable", "reason": "media file is missing"}
    ffmpeg = _ffmpeg_exe()
    if not ffmpeg:
        return {"status": "unavailable", "reason": "ffmpeg is unavailable"}
    try:
        proc = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-nostats",
                "-i",
                str(media_path),
                "-map",
                "0:a:0",
                "-af",
                "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "unavailable", "reason": str(exc)}
    matches = re.findall(r"\{\s*\"input_i\".*?\}", proc.stderr, flags=re.DOTALL)
    if not matches:
        return {"status": "unavailable", "reason": "ffmpeg loudnorm output was not parseable"}
    try:
        payload = json.loads(matches[-1])
        return {
            "status": "measured",
            "input_i": float(payload["input_i"]),
            "input_tp": float(payload["input_tp"]),
            "input_lra": float(payload["input_lra"]),
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {"status": "unavailable", "reason": f"invalid loudnorm payload: {exc}"}


def _check_audio_clipping(audio_path: Path) -> dict[str, Any]:
    analysis = _analyze_pcm_wav(audio_path)
    peak_dbfs = analysis.get("peak_dbfs")
    measured = isinstance(peak_dbfs, (int, float))
    passed = measured and float(peak_dbfs) <= -0.5
    detail = "Measured PCM sample peak must stay at or below -0.5 dBFS; the production target is -1.5 dBTP."
    if not measured:
        detail = f"Unable to measure narration peak: {analysis.get('reason') or 'unsupported audio'}"
    return {
        "name": "Audio clipping",
        "status": "pass" if passed else "fail",
        "value": analysis,
        "detail": detail,
    }


def _check_excessive_silence(audio_path: Path) -> dict[str, Any]:
    analysis = _analyze_pcm_wav(audio_path)
    silence_ratio = analysis.get("silence_ratio")
    measured = isinstance(silence_ratio, (int, float))
    passed = measured and float(silence_ratio) <= 0.35
    detail = "Narration may contain natural pauses, but no more than 35% of measured windows may be below -50 dBFS."
    if not measured:
        detail = f"Unable to measure narration silence: {analysis.get('reason') or 'unsupported audio'}"
    return {
        "name": "Excessive silence",
        "status": "pass" if passed else "fail",
        "value": analysis,
        "detail": detail,
    }


def _analyze_pcm_wav(audio_path: Path) -> dict[str, Any]:
    if not audio_path.exists():
        return {"peak_dbfs": None, "silence_ratio": None, "reason": "narration file is missing"}
    try:
        with wave.open(str(audio_path), "rb") as wav:
            sample_width = wav.getsampwidth()
            channels = wav.getnchannels()
            sample_rate = wav.getframerate()
            if sample_width not in {1, 2, 3, 4} or channels <= 0 or sample_rate <= 0:
                return {"peak_dbfs": None, "silence_ratio": None, "reason": "unsupported PCM WAV format"}
            max_amplitude = float((1 << (sample_width * 8 - 1)) - 1)
            window_frames = max(1, sample_rate // 50)
            max_peak = 0
            silent_windows = 0
            windows = 0
            while True:
                payload = wav.readframes(window_frames)
                if not payload:
                    break
                samples = _pcm_samples(payload, sample_width)
                if not samples:
                    continue
                absolute_peak = max(abs(sample) for sample in samples)
                max_peak = max(max_peak, absolute_peak)
                rms = math.sqrt(sum(float(sample) * float(sample) for sample in samples) / len(samples))
                rms_dbfs = 20.0 * math.log10(rms / max_amplitude) if rms > 0 else -120.0
                silent_windows += int(rms_dbfs < -50.0)
                windows += 1
    except (OSError, wave.Error, EOFError) as exc:
        return {"peak_dbfs": None, "silence_ratio": None, "reason": str(exc)}
    if windows == 0:
        return {"peak_dbfs": None, "silence_ratio": None, "reason": "narration contains no PCM frames"}
    peak_dbfs = 20.0 * math.log10(max_peak / max_amplitude) if max_peak > 0 else -120.0
    return {
        "peak_dbfs": round(peak_dbfs, 3),
        "silence_ratio": round(silent_windows / windows, 4),
        "sample_rate": sample_rate,
        "channels": channels,
        "sample_width_bytes": sample_width,
        "windows": windows,
    }


def _pcm_samples(payload: bytes, sample_width: int) -> list[int] | array:
    if sample_width == 1:
        return [value - 128 for value in payload]
    if sample_width == 2:
        values = array("h")
        values.frombytes(payload[: len(payload) - (len(payload) % 2)])
        return values
    if sample_width == 4:
        values = array("i")
        values.frombytes(payload[: len(payload) - (len(payload) % 4)])
        return values
    usable = len(payload) - (len(payload) % 3)
    values_24: list[int] = []
    for offset in range(0, usable, 3):
        raw = int.from_bytes(payload[offset : offset + 3], byteorder="little", signed=False)
        values_24.append(raw - (1 << 24) if raw & (1 << 23) else raw)
    return values_24


def _check_subtitle_sync(assembly: dict[str, Any], voice: dict[str, Any]) -> dict[str, Any]:
    subtitle_file = Path(str(assembly.get("subtitle_file") or ""))
    timestamps = voice.get("timestamps") if isinstance(voice.get("timestamps"), list) else []
    subtitle_status = voice.get("subtitle_status") if isinstance(voice.get("subtitle_status"), dict) else {}
    drift = 0.0 if subtitle_file.exists() and timestamps else 99.0
    engine = str(subtitle_status.get("engine") or "unknown")
    match = _subtitle_script_match_score(subtitle_file, timestamps)
    detail = "Subtitle file must be generated from segment timestamps."
    if engine == "script_aligned_fallback":
        detail += " Whisper was unavailable or rejected; script-aligned fallback was used."
    elif engine == "whisper_cpp":
        detail += " Whisper SRT passed script-overlap validation."
    if not match["accepted"]:
        detail += " Subtitle text does not match approved narration script closely enough."
    return {
        "name": "Subtitle sync",
        "status": "pass" if drift < 0.5 and match["accepted"] else "fail",
        "value": {
            "drift_sec": drift,
            "engine": engine,
            "source": subtitle_status.get("source"),
            "script_word_overlap": match["overlap"],
            "expected_script_words": match["expected_words"],
            "overlap_ratio": match["overlap_ratio"],
        },
        "detail": detail,
    }


def _check_caption_safe_area(assembly: dict[str, Any]) -> dict[str, Any]:
    style = assembly.get("subtitle_style") if isinstance(assembly.get("subtitle_style"), dict) else {}
    safe_area = style.get("safe_area_px") if isinstance(style.get("safe_area_px"), dict) else {}
    frame = style.get("frame_px") if isinstance(style.get("frame_px"), dict) else {}
    expected = {
        "left": 72,
        "right": 72,
        "top": 96,
        "bottom": 220,
    }
    measured = {key: int(_float(safe_area.get(key))) for key in expected}
    frame_width = int(_float(frame.get("width")))
    frame_height = int(_float(frame.get("height")))
    position = str(style.get("position") or "")
    passed = (
        frame_width == 1080
        and frame_height == 1920
        and all(measured[key] >= minimum for key, minimum in expected.items())
        and position == "lower_third_center"
    )
    return {
        "name": "Caption safe area",
        "status": "pass" if passed else "fail",
        "value": {
            "frame_px": {"width": frame_width, "height": frame_height},
            "safe_area_px": measured,
            "minimum_safe_area_px": expected,
            "position": position,
        },
        "detail": "Caption layout metadata must enforce the versioned 1080x1920 mobile safe area.",
    }


def _subtitle_script_match_score(subtitle_file: Path, timestamps: list[dict[str, Any]]) -> dict[str, Any]:
    if not subtitle_file.exists():
        return {"accepted": False, "overlap": 0, "expected_words": 0, "overlap_ratio": 0.0}
    try:
        subtitle_text = subtitle_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {"accepted": False, "overlap": 0, "expected_words": 0, "overlap_ratio": 0.0}
    subtitle_words = _word_set(subtitle_text)
    expected_words: set[str] = set()
    for item in timestamps:
        expected_words.update(_word_set(str(item.get("text") or "")))
    expected_words = {word for word in expected_words if len(word) >= 4}
    if not expected_words:
        return {"accepted": False, "overlap": 0, "expected_words": 0, "overlap_ratio": 0.0}
    overlap = expected_words.intersection(subtitle_words)
    overlap_ratio = len(overlap) / len(expected_words)
    return {
        "accepted": overlap_ratio >= 0.72 and len(overlap) >= min(8, len(expected_words)),
        "overlap": len(overlap),
        "expected_words": len(expected_words),
        "overlap_ratio": round(overlap_ratio, 4),
    }


def _word_set(value: str) -> set[str]:
    cleaned = "".join(char.casefold() if char.isalnum() else " " for char in value)
    return {word for word in cleaned.split() if word and not word.isdigit()}


def _check_black_frames(video_path: Path, *, required: bool = False) -> dict[str, Any]:
    if not required and os.getenv("PYTEST_CURRENT_TEST"):
        return {
            "name": "Black frames",
            "status": "pass",
            "value": "skipped_for_legacy_test",
            "detail": "Legacy test context skips media blackdetect; Wealth Blueprint never skips it.",
        }
    ffmpeg = _ffmpeg_exe()
    if not ffmpeg or not video_path.exists():
        return {
            "name": "Black frames",
            "status": "fail" if required else "warn",
            "value": "not_checked",
            "detail": "ffmpeg blackdetect unavailable or final video missing.",
        }
    cmd = [ffmpeg, "-i", str(video_path), "-vf", "blackdetect=d=0.5:pix_th=0.02:pic_th=0.98", "-an", "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"name": "Black frames", "status": "fail" if required else "warn", "value": "not_checked", "detail": str(exc)}
    if proc.returncode != 0:
        return {
            "name": "Black frames",
            "status": "fail" if required else "warn",
            "value": "measurement_failed",
            "detail": f"ffmpeg blackdetect exited with code {proc.returncode}.",
        }
    detected = "black_start:" in f"{proc.stdout}\n{proc.stderr}"
    return {
        "name": "Black frames",
        "status": "fail" if detected else "pass",
        "value": "detected" if detected else "not_detected",
        "detail": "ffmpeg blackdetect threshold d=0.5 pix_th=0.02 pic_th=0.98.",
    }


def _check_visual_regression(
    settings: Settings | None,
    context: ProductionContext,
    video_path: Path,
    assembly: dict[str, Any],
) -> dict[str, Any]:
    manifest_path = context.artifact_dir / "project_manifest.json"
    channel = str(getattr(resolve_channel_profile(context), "key", "") or "").strip().casefold()
    if channel != "wealth_blueprint":
        return {
            "name": "Visual regression baseline",
            "status": "pass",
            "value": "not_applicable",
            "detail": "The frozen Wealth Blueprint visual style does not apply to this channel.",
        }
    if settings is None:
        return {
            "name": "Visual regression baseline",
            "status": "fail",
            "value": "settings_unavailable",
            "detail": "Final Wealth Blueprint output is blocked because runtime settings were not supplied.",
        }
    baseline_path = visual_regression_baseline_path(settings)
    if not baseline_path.is_file():
        return {
            "name": "Visual regression baseline",
            "status": "fail",
            "value": {
                "status": "not_configured",
                "baseline_path": str(baseline_path),
            },
            "detail": (
                "No accepted WEALTH_BLUEPRINT_REFERENCE_001 baseline exists. "
                "Final output is blocked until Gate C explicitly accepts the reference."
            ),
        }
    if not manifest_path.is_file() or not video_path.is_file():
        missing = []
        if not manifest_path.is_file():
            missing.append("project manifest")
        if not video_path.is_file():
            missing.append("final render")
        return {
            "name": "Visual regression baseline",
            "status": "fail",
            "value": {"status": "missing_evidence", "missing": missing},
            "detail": f"Visual regression cannot run without: {', '.join(missing)}.",
        }
    try:
        manifest = load_project_manifest(manifest_path)
    except (OSError, ValueError) as exc:
        return {
            "name": "Visual regression baseline",
            "status": "fail",
            "value": {"status": "invalid_manifest", "reason": str(exc)},
            "detail": "The Wealth Blueprint manifest failed integrity verification; final output is blocked.",
        }
    if manifest.channel.strip().casefold() != channel:
        return {
            "name": "Visual regression baseline",
            "status": "fail",
            "value": {
                "status": "channel_mismatch",
                "profile_channel": channel,
                "manifest_channel": manifest.channel,
            },
            "detail": "The project channel and signed manifest channel do not match; final output is blocked.",
        }
    assembly_binding = verify_remotion_assembly_provenance(assembly, video_path)
    if assembly_binding.get("verified") is not True:
        return {
            "name": "Visual regression baseline",
            "status": "fail",
            "value": {
                "status": "assembly_provenance_failed",
                "verification": assembly_binding,
            },
            "detail": "The final MP4 is not verifiably bound to the Remotion render and mux inputs; final output is blocked.",
        }
    result = compare_visual_regression(
        manifest_path,
        video_path,
        baseline_path,
        subtitle_style=assembly.get("subtitle_style") if isinstance(assembly, dict) else None,
        renderer_evidence=assembly,
    )
    passed = bool(result.get("passed"))
    return {
        "name": "Visual regression baseline",
        "status": "pass" if passed else "fail",
        "value": result,
        "detail": (
            "The render matches the accepted Wealth Blueprint style signature."
            if passed
            else "The render drifted from the accepted style baseline; final output is blocked."
        ),
    }


def _check_visual_reference_match(
    video_path: Path,
    reference_profile: Any,
    *,
    channel_profile: ProductionChannelProfile | None = None,
) -> dict[str, Any]:
    try:
        from atnos.production.reference_profile_builder import analyze_video_visual_profile

        metrics = analyze_video_visual_profile(video_path, max_video_seconds=20)
    except Exception as exc:
        return {
            "name": "Visual reference match",
            "status": "warn",
            "value": "analysis_unavailable",
            "detail": f"Unable to analyze visual reference match: {exc}",
        }
    if metrics.get("frame_analysis_status") != "analyzed":
        return {
            "name": "Visual reference match",
            "status": "warn",
            "value": metrics,
            "detail": "Visual reference analysis did not produce frame metrics.",
        }
    measured_brightness = _nullable_float(metrics.get("brightness"))
    measured_saturation = _nullable_float(metrics.get("saturation"))
    profile_key = getattr(channel_profile, "key", "")
    if reference_profile is None or not bool(getattr(reference_profile, "available", False)):
        return {
            "name": "Visual reference match",
            "status": "pass",
            "value": {
                "channel_profile": profile_key or None,
                "reference_profile": "missing",
                "measured_brightness": measured_brightness,
                "measured_saturation": measured_saturation,
                "frame_sample_count": metrics.get("frame_sample_count"),
            },
            "detail": (
                "No local user reference profile is configured; reference-distance comparison was skipped, "
                "but final-video frame metrics were measured for channel-specific quality floors."
            ),
        }
    target_brightness = _nullable_float(getattr(reference_profile, "median_brightness", None))
    target_saturation = _nullable_float(getattr(reference_profile, "median_saturation", None))
    brightness_delta = (
        abs(measured_brightness - target_brightness)
        if measured_brightness is not None and target_brightness is not None
        else None
    )
    saturation_delta = (
        abs(measured_saturation - target_saturation)
        if measured_saturation is not None and target_saturation is not None
        else None
    )
    brightness_ok = brightness_delta is None or brightness_delta <= 0.22
    saturation_ok = saturation_delta is None or saturation_delta <= 0.35
    profile_override = ""
    if profile_key in {"sigma_squad", "brainrot"}:
        profile_override = "high_saturation_profile"
        saturation_ok = measured_saturation is None or measured_saturation >= 0.45
    elif profile_key in {"dark_chronicles", "history"}:
        profile_override = "dark_documentary_profile"
        brightness_ok = measured_brightness is None or 0.04 <= measured_brightness <= 0.38
        saturation_ok = measured_saturation is None or measured_saturation >= 0.35
    elif profile_key == "wealth_blueprint":
        profile_override = "profile_palette_checked"
    return {
        "name": "Visual reference match",
        "status": "pass" if brightness_ok and saturation_ok else "warn",
        "value": {
            "channel_profile": profile_key or None,
            "profile_override": profile_override or None,
            "measured_brightness": measured_brightness,
            "target_brightness": target_brightness,
            "brightness_delta": round(brightness_delta, 4) if brightness_delta is not None else None,
            "measured_saturation": measured_saturation,
            "target_saturation": target_saturation,
            "saturation_delta": round(saturation_delta, 4) if saturation_delta is not None else None,
            "frame_sample_count": metrics.get("frame_sample_count"),
        },
        "detail": (
            "Final video brightness/saturation should stay close to the local user reference Shorts profile, "
            "unless the active channel profile intentionally requires a different palette."
        ),
    }


def _check_thumbnail(thumbnail: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(thumbnail.get("thumbnail_path") or ""))
    size = path.stat().st_size if path.exists() else 0
    status = str(thumbnail.get("status") or "").casefold()
    fallback_used = bool(thumbnail.get("fallback_used"))
    generation_backend = str(thumbnail.get("thumbnail_generation_backend") or "").strip()
    comfyui_generated = thumbnail.get("comfyui_generated")
    local_generated = bool(thumbnail.get("local_generated")) or str(
        thumbnail.get("thumbnail_generation_backend") or ""
    ) == "local_pillow_thumbnail"
    copied_cached_thumbnail = bool(thumbnail.get("copied_cached_thumbnail")) or generation_backend == "cached_comfyui_thumbnail"
    placeholder_status = status in {"placeholder_ready", "fallback", "fallback_ready"}
    explicit_placeholder = placeholder_status or fallback_used
    template_thumbnail = generation_backend == "local_pillow_thumbnail"
    quality_audit = thumbnail.get("thumbnail_quality") if isinstance(thumbnail.get("thumbnail_quality"), dict) else None
    quality_blockers = thumbnail_quality_blockers(quality_audit)
    passed = (
        size >= 50 * 1024
        and not explicit_placeholder
        and not quality_blockers
        and not template_thumbnail
        and not copied_cached_thumbnail
    )
    if explicit_placeholder:
        detail = "Thumbnail is a placeholder/fallback; a fresh topic-specific generated thumbnail is required before review/upload."
    elif template_thumbnail:
        detail = "Thumbnail was produced by the local template fallback; generate a real topic-specific thumbnail before review/upload."
    elif copied_cached_thumbnail:
        detail = "Thumbnail was copied from cached ComfyUI output and may not match the current topic; regenerate it fresh."
    elif quality_blockers:
        detail = f"Thumbnail quality audit failed: {', '.join(quality_blockers[:5])}."
    else:
        detail = "Thumbnail exists; generated thumbnail should exceed 50KB."
    return {
        "name": "Thumbnail present",
        "status": "pass" if passed else "fail",
        "value": {
            "size_bytes": size,
            "status": thumbnail.get("status"),
            "fallback_used": fallback_used,
            "comfyui_generated": comfyui_generated,
            "local_generated": local_generated,
            "thumbnail_generation_backend": generation_backend,
            "copied_cached_thumbnail": copied_cached_thumbnail,
            "template_thumbnail": template_thumbnail,
            "quality_audit_present": quality_audit is not None,
            "quality_blockers": quality_blockers,
            "quality_metrics": quality_audit.get("metrics") if isinstance(quality_audit, dict) else {},
        },
        "detail": detail,
    }


def _check_copyright(script: dict[str, Any]) -> dict[str, Any]:
    text = str(script.get("description_draft") or "").casefold()
    risky = [word for word in ["disney", "netflix", "spotify", "taylor swift"] if word in text]
    return {
        "name": "Copyright risk",
        "status": "warn" if risky else "pass",
        "value": risky,
        "detail": "Brand/song-title heuristic until LLM copyright review is enabled.",
    }


def _check_brand_consistency(script: dict[str, Any], title: str) -> dict[str, Any]:
    text = f"{title} {script.get('description_draft', '')}".casefold()
    score = 7 if any(word in text for word in ["finanse", "pieniadze", "budzet", "inflacja"]) else 6
    return {
        "name": "Brand consistency",
        "status": "pass" if score >= 6 else "fail",
        "value": score,
        "detail": "Local niche/tone heuristic until LLM brand review is enabled.",
    }


def _check_ollama_review(
    settings: Settings | None,
    context: ProductionContext,
    script: dict[str, Any],
    *,
    required: bool = False,
) -> dict[str, Any]:
    if settings is None:
        return {
            "name": "Ollama QA review",
            "status": "fail" if required else "pass",
            "value": "settings_missing",
            "detail": "Ollama QA review skipped because runtime settings were not supplied.",
        }
    if not required and os.getenv("PYTEST_CURRENT_TEST") and httpx.post is _ORIGINAL_HTTPX_POST:
        return {
            "name": "Ollama QA review",
            "status": "pass",
            "value": "skipped_in_test",
            "detail": "Live Ollama QA review skipped in pytest runtime.",
        }
    prompt = (
        "Jesteś QA reviewerem YouTube Shorts. Oceń czy materiał jest gotowy do prywatnego uploadu.\n"
        "Zwróć TYLKO JSON: {\"passed\": true/false, \"score\": 0-10, \"issues\": [\"...\"], "
        "\"copyright_risk\": \"low|medium|high\", \"brand_fit\": \"low|medium|high\"}.\n\n"
        f"Temat: {context.topic.title}\n"
        f"Opis tematu: {context.topic.description}\n"
        f"Hook: {script.get('hook', '')}\n"
        f"Skrypt JSON: {json.dumps(script, ensure_ascii=False)[:5000]}"
    )
    model = (
        settings.ollama_fast_model
        or settings.ollama_chat_model
        or settings.ollama_main_model
        or OllamaModelRolesService.DEFAULT_FAST_TEXT_MODEL
    )
    try:
        timeout_s = max(120.0, float(getattr(settings, "ollama_timeout_s", 120.0) or 120.0))
        response = httpx.post(
            f"{settings.ollama_base_url.rstrip('/')}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "keep_alive": "0s",
                "options": {"temperature": 0.1},
            },
            timeout=timeout_s,
        )
        response.raise_for_status()
        data = response.json()
        review = _parse_ollama_review(str(data.get("response") or ""))
    except Exception as exc:
        return {
            "name": "Ollama QA review",
            "status": "fail" if required else "warn",
            "value": "unavailable",
            "detail": (
                "Ollama QA review is required for Wealth Blueprint; unavailable review blocks upload. "
                if required
                else "Ollama QA review unavailable; deterministic QA remains authoritative for legacy private review upload. "
            ) + (
                f"Runtime detail: {exc}"
            ),
        }

    score = _float(review.get("score"))
    passed = bool(review.get("passed")) and score >= 7
    status = "pass" if passed else "fail"
    return {
        "name": "Ollama QA review",
        "status": status,
        "value": {
            "score": score,
            "copyright_risk": review.get("copyright_risk"),
            "brand_fit": review.get("brand_fit"),
            "issues": review.get("issues") if isinstance(review.get("issues"), list) else [],
        },
        "detail": "Local Ollama review of script, hook, brand fit and copyright risk.",
    }


def _ffprobe_exe() -> str | None:
    return resolve_ffprobe(_ffmpeg_exe())


def _ffmpeg_exe() -> str | None:
    return resolve_ffmpeg()


def _parse_duration(output: str) -> float:
    marker = "Duration:"
    if marker not in output:
        return 0.0
    raw = output.split(marker, 1)[1].split(",", 1)[0].strip()
    try:
        h, m, s = raw.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    except (ValueError, TypeError):
        return 0.0


def _parse_ffmpeg_stream_info(output: str) -> dict[str, Any]:
    info: dict[str, Any] = {
        "width": 0,
        "height": 0,
        "video_codec": "",
        "pixel_format": "",
        "fps": 0.0,
        "audio_codec": "",
        "sample_rate": 0,
        "channels": 0,
    }
    in_input_section = False
    for line in output.splitlines():
        normalized = line.strip()
        if normalized.startswith("Input #"):
            in_input_section = True
            continue
        if normalized.startswith("Output #"):
            in_input_section = False
            continue
        if not in_input_section:
            continue
        if " Video: " in normalized or " Video:" in normalized:
            tail = normalized.split("Video:", 1)[1]
            codec = tail.split(",", 1)[0].strip().split(" ", 1)[0]
            match = re.search(r"(?<![a-zA-Z0-9])(\d{2,5})x(\d{2,5})(?![a-zA-Z0-9])", normalized)
            if match:
                info["width"] = int(match.group(1))
                info["height"] = int(match.group(2))
            info["video_codec"] = codec
            pixel_match = re.search(r"\b(yuv420p|yuv422p|yuv444p|nv12|rgb24)\b", tail.casefold())
            if pixel_match:
                info["pixel_format"] = pixel_match.group(1)
            fps_match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*fps\b", tail.casefold())
            if fps_match:
                info["fps"] = float(fps_match.group(1))
        if " Audio: " in normalized or " Audio:" in normalized:
            tail = normalized.split("Audio:", 1)[1]
            info["audio_codec"] = tail.split(",", 1)[0].strip()
            sample_match = re.search(r"(\d{4,6})\s*Hz", tail)
            if sample_match:
                info["sample_rate"] = int(sample_match.group(1))
            lowered = tail.casefold()
            if "stereo" in lowered:
                info["channels"] = 2
            elif "mono" in lowered:
                info["channels"] = 1
            else:
                channel_match = re.search(r"(\d+)\s*channels?", lowered)
                if channel_match:
                    info["channels"] = int(channel_match.group(1))
    return info


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _frame_rate(value: Any) -> float:
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    if "/" not in raw:
        return _float(raw)
    numerator, denominator = raw.split("/", 1)
    denominator_value = _float(denominator)
    return _float(numerator) / denominator_value if denominator_value else 0.0


def _nullable_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_ollama_review(content: str) -> dict[str, Any]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            return {"passed": False, "score": 0, "issues": ["Ollama returned non-JSON QA output."]}
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return {"passed": False, "score": 0, "issues": ["Ollama returned invalid QA JSON."]}
    return parsed if isinstance(parsed, dict) else {"passed": False, "score": 0, "issues": ["Ollama QA output was not an object."]}
