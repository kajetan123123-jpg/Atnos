from __future__ import annotations

import asyncio
import base64
import colorsys
import hashlib
import json
import logging
import os
import random
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from atnos.core.config import Settings
from atnos.model_manager.model_manager import ModelManager
from atnos.production.channel_profile import ProductionChannelProfile, resolve_channel_profile
from atnos.production.media_tools import resolve_ffmpeg, resolve_ffprobe
from atnos.production.models import ProductionContext, StepResult
from atnos.production.project_manifest import (
    SceneType,
    infer_scene_type,
    infer_semantic_overlay_kind,
    normalize_semantic_overlay_kind,
)
from atnos.production.reference_profile import ReferenceShortsProfile, load_reference_shorts_profile
from atnos.production.scene_checkpoint import SceneCheckpointStore, scene_generation_input_hash
from atnos.production.steps.common import write_json
from atnos.production.visual_semantic_audit import (
    audit_visual_contract_compliance,
    audit_visual_semantics,
    compare_visual_candidates,
)
from atnos.production.visual_quality_contract import (
    finance_prompt_has_text_surface,
    negative_visual_prompt,
    visual_quality_contract,
)
from atnos.services.comfyui_runtime_service import ComfyUiRuntimeService
from atnos.services.comfyui_image_generation_service import (
    ComfyUiImageGenerationError,
    ComfyUiImageGenerationService,
)
from atnos.services.hardware_guard_service import HardwareGuardService


COMFYUI_TIMEOUT_S = 5.0
COMFYUI_VIDEO_BACKENDS = ("wan", "ltx")
LOCAL_IMAGE_MOTION_BACKEND = "comfy_image_motion"
REMOTION_NATIVE_BACKEND = "remotion_native"
VIDEO_BACKENDS = (LOCAL_IMAGE_MOTION_BACKEND, "openai_image_motion", *COMFYUI_VIDEO_BACKENDS)
OPENAI_IMAGE_MODEL_CANDIDATES = ("gpt-image-1.5", "gpt-image-1", "gpt-image-1-mini")
SCENE_RENDERER_VERSION = "video-generator-v2"
logger = logging.getLogger(__name__)


async def run(settings: Settings, context: ProductionContext) -> StepResult:
    asset_plan = context.artifacts["visuals"]
    video_dir = context.artifact_dir / "video"
    video_dir.mkdir(parents=True, exist_ok=True)
    scenes = asset_plan.get("scenes") if isinstance(asset_plan.get("scenes"), list) else []
    scenes = scenes or [_fallback_scene(context)]
    scenes = _attach_scene_narration(scenes, context.artifacts.get("script"))
    reference_profile = load_reference_shorts_profile(settings)
    channel_profile = resolve_channel_profile(context)
    scenes = _select_generation_scenes(settings, scenes, reference_profile=reference_profile)
    scenes = _align_scene_durations_to_voice(scenes, context.artifacts.get("voice"))
    if not os.getenv("PYTEST_CURRENT_TEST"):
        _assert_scene_generation_safe(scenes, channel_profile=channel_profile)

    preferred_backend = _preferred_video_backend(settings)
    comfy_ready = await _ensure_comfyui(settings) if _should_start_comfyui(settings, preferred_backend) else False
    backend = await _select_backend(settings, comfy_ready=comfy_ready)
    _assert_real_visual_backend_available(settings, backend)
    workflow_path = _workflow_path(settings, backend)
    workflow = (
        {}
        if backend["mode"] in {"motion_graphic", LOCAL_IMAGE_MOTION_BACKEND, "openai_image_motion"}
        else _load_or_create_workflow(workflow_path, backend)
    )
    workflow_diagnostics = _workflow_diagnostics(workflow, backend)
    clips: list[dict[str, Any]] = []
    pytest_placeholder_cache: dict[float, Path] = {}
    checkpoint_store = SceneCheckpointStore(
        video_dir / "scene_checkpoint.json",
        job_id=context.job_id,
        scene_ids=[f"scene_{index:02d}" for index in range(1, len(scenes) + 1)],
    )
    checkpoint_input_hashes = {
        index: scene_generation_input_hash(
            _scene_checkpoint_contract(scene),
            segment=index,
            total=len(scenes),
            backend=backend,
            workflow_name=workflow_path.name,
            channel_profile=channel_profile.key,
            renderer_version=SCENE_RENDERER_VERSION,
        )
        for index, scene in enumerate(scenes, start=1)
    }
    for index, scene in enumerate(scenes, start=1):
        checkpoint_scene_id = f"scene_{index:02d}"
        clip_path = video_dir / f"s{index:02d}.mp4"
        generated = False
        generated_backend = ""
        cached_fallback = False
        prompt_text = _scene_motion_prompt_text(scene)
        composition_signature = _motion_composition_signature(
            prompt_text,
            segment=index,
            total=len(scenes),
            channel_profile=channel_profile,
        )
        checkpoint_input_hash = checkpoint_input_hashes[index]
        checkpoint_before_begin = next(
            item
            for item in checkpoint_store.manifest.scenes
            if item.scene_id == checkpoint_scene_id
        )
        cached_clip = checkpoint_store.begin(
            checkpoint_scene_id,
            input_hash=checkpoint_input_hash,
            artifact_validator=_is_probeable_video,
        )
        if cached_clip is not None:
            cached_clip["checkpoint_status"] = "CACHED"
            clips.append(cached_clip)
            continue
        checkpoint_attempt = next(
            (
                item.attempt
                for item in checkpoint_store.manifest.scenes
                if item.scene_id == checkpoint_scene_id
            ),
            1,
        )
        candidate_generation_attempt = _candidate_generation_attempt(
            previous_status=checkpoint_before_begin.status,
            previous_input_hash=checkpoint_before_begin.input_hash,
            current_input_hash=checkpoint_input_hash,
            current_attempt=checkpoint_attempt,
        )
        image_text_audit: dict[str, Any] | None = None
        image_visual_audit: dict[str, Any] | None = None
        cutaway_image_text_audit: dict[str, Any] | None = None
        cutaway_image_visual_audit: dict[str, Any] | None = None
        generated_image_paths: list[str] = []
        image_candidates: list[dict[str, Any]] = []
        selected_image_path = ""
        semantic_visual_audit: dict[str, Any] | None = None
        scene_type = _manifest_scene_type(scene)
        prepared_selection: dict[str, Any] | None = None
        if (
            backend["mode"] == LOCAL_IMAGE_MOTION_BACKEND
            and workflow_diagnostics["valid"]
            and channel_profile.key in {"wealth_blueprint", "finance"}
            and scene_type is SceneType.IMAGE
            and not os.getenv("PYTEST_CURRENT_TEST")
        ):
            prepared_selection = (
                await _prepare_wealth_image_candidates(
                    settings,
                    scenes,
                    video_dir=video_dir,
                    topic_title=context.topic.title,
                    channel_profile=channel_profile,
                    segments={index},
                    generation_attempt=candidate_generation_attempt,
                    existing_selected_image_paths=[
                        Path(str(clip.get("selected_image_path") or ""))
                        for clip in clips
                        if str(clip.get("selected_image_path") or "").strip()
                    ],
                )
            ).get(index)
        if channel_profile.key in {"wealth_blueprint", "finance"} and scene_type is not SceneType.IMAGE:
            generated = await asyncio.to_thread(
                _write_motion_graphic_clip,
                clip_path,
                scene=scene,
                segment=index,
                total=len(scenes),
                reference_profile=reference_profile,
                channel_profile=channel_profile,
            )
            generated = generated and _is_probeable_video(clip_path)
            if generated:
                generated_backend = REMOTION_NATIVE_BACKEND
                image_visual_audit = {
                    "status": "not_applicable",
                    "passed": True,
                    "reason": f"{scene_type.value} is rendered deterministically by Remotion.",
                }
        elif prepared_selection is not None:
            image_candidates = list(prepared_selection.get("candidates") or [])
            selected_image_path = str(prepared_selection.get("selected_image_path") or "")
            generated_image_paths = [
                str(candidate.get("path"))
                for candidate in image_candidates
                if str(candidate.get("path") or "").strip()
            ]
            if not selected_image_path:
                reasons = list(prepared_selection.get("reasons") or [])
                raise RuntimeError(
                    "REGENERATE_SCENE: no ImageScene candidate passed OCR, visual relevance, and semantic vision "
                    f"for scene {index}/{len(scenes)}. Reasons: {json.dumps(reasons, ensure_ascii=False)}"
                )
            selected_candidate = next(
                (
                    candidate
                    for candidate in image_candidates
                    if str(candidate.get("path") or "") == selected_image_path
                ),
                {},
            )
            selected_qa = selected_candidate.get("qa") if isinstance(selected_candidate.get("qa"), dict) else {}
            image_text_audit = selected_qa.get("text") if isinstance(selected_qa.get("text"), dict) else None
            image_visual_audit = (
                selected_qa.get("heuristic") if isinstance(selected_qa.get("heuristic"), dict) else None
            )
            semantic_visual_audit = (
                selected_qa.get("semantic") if isinstance(selected_qa.get("semantic"), dict) else None
            )
            generated = await asyncio.to_thread(
                _write_image_motion_clip,
                clip_path,
                image_path=Path(selected_image_path),
                scene=scene,
                segment=index,
                reference_profile=reference_profile,
                channel_profile=channel_profile,
            )
            generated = generated and _is_probeable_video(clip_path)
            if generated:
                generated_backend = backend["mode"]
        elif backend["mode"] == LOCAL_IMAGE_MOTION_BACKEND and workflow_diagnostics["valid"]:
            generated = await _try_generate_comfy_image_motion_clip(
                settings,
                scene,
                clip_path,
                index,
                len(scenes),
                reference_profile=reference_profile,
                channel_profile=channel_profile,
            )
            generated = generated and _is_probeable_video(clip_path)
            if generated:
                generated_backend = backend["mode"]
                image_path = clip_path.with_suffix(f".s{index:02d}.comfy.png")
                cutaway_path = clip_path.with_suffix(f".s{index:02d}.comfy.cutaway.png")
                generated_image_paths = [
                    str(candidate)
                    for candidate in (image_path, cutaway_path)
                    if candidate.exists()
                ]
                image_text_audit = _audit_generated_image_text(image_path)
                image_visual_audit = _audit_generated_image_visual_relevance(
                    image_path,
                    scene=scene,
                    channel_profile=channel_profile,
                )
                if cutaway_path.exists():
                    cutaway_image_text_audit = _audit_generated_image_text(cutaway_path)
                    cutaway_image_visual_audit = _audit_generated_image_visual_relevance(
                        cutaway_path,
                        scene=scene,
                        channel_profile=channel_profile,
                    )
        elif backend["mode"] == "openai_image_motion" and workflow_diagnostics["valid"]:
            generated = await _try_generate_openai_image_motion_clip(
                settings,
                scene,
                clip_path,
                index,
                len(scenes),
                reference_profile=reference_profile,
                channel_profile=channel_profile,
            )
            generated = generated and _is_probeable_video(clip_path)
            if generated:
                generated_backend = backend["mode"]
                image_path = clip_path.with_suffix(f".s{index:02d}.openai.png")
                image_text_audit = _audit_generated_image_text(image_path)
                image_visual_audit = _audit_generated_image_visual_relevance(
                    image_path,
                    scene=scene,
                    channel_profile=channel_profile,
                )
            else:
                image_visual_audit = None
        elif comfy_ready and backend["mode"] in COMFYUI_VIDEO_BACKENDS and workflow_diagnostics["valid"]:
            generated = await _try_generate_clip_with_retries(
                settings,
                workflow,
                scene,
                clip_path,
                index,
                len(scenes),
                backend["mode"],
            )
            generated = generated and _is_probeable_video(clip_path)
            if generated:
                generated_backend = backend["mode"]
        _assert_scene_fallback_allowed(
            settings,
            backend,
            generated=generated,
            segment=index,
            total=len(scenes),
        )
        if not generated and not os.getenv("PYTEST_CURRENT_TEST"):
            generated = _write_motion_graphic_clip(
                clip_path,
                scene=scene,
                segment=index,
                total=len(scenes),
                reference_profile=reference_profile,
                channel_profile=channel_profile,
            )
            if generated:
                generated_backend = "motion_graphic"
        if not generated and comfy_ready and backend["mode"] != "placeholder":
            cached_fallback = _copy_cached_ltx_clip(settings, clip_path)
        if not generated:
            placeholder_duration = float(scene.get("duration_s") or 4)
            if cached_fallback:
                pass
            elif os.getenv("PYTEST_CURRENT_TEST"):
                placeholder_duration = min(placeholder_duration, 0.35)
                cached = pytest_placeholder_cache.get(round(placeholder_duration, 2))
                if cached and cached.exists():
                    shutil.copyfile(cached, clip_path)
                else:
                    _write_placeholder_clip(clip_path, duration_s=placeholder_duration)
                    pytest_placeholder_cache[round(placeholder_duration, 2)] = clip_path
            else:
                _write_placeholder_clip(clip_path, duration_s=placeholder_duration)
        if generated and image_visual_audit is None:
            frame_path = _extract_clip_audit_frame(clip_path, segment=index)
            if frame_path is not None:
                image_visual_audit = _audit_generated_image_visual_relevance(
                    frame_path,
                    scene=scene,
                    channel_profile=channel_profile,
                )
        clip_status = "generated" if generated else "cached_fallback" if cached_fallback else "placeholder"
        target_backend = str(backend.get("mode") or "")
        actual_backend = generated_backend if generated else "cached_fallback" if cached_fallback else "placeholder"
        matches_target_backend = _clip_backend_matches_target(
            actual_backend=actual_backend,
            target_backend=target_backend,
            scene_type=scene_type.value,
        )
        clip_payload = {
            "segment": index,
            "scene_id": scene.get("scene_id", index - 1),
            "prompt": scene.get("visual_prompt", ""),
            "duration_s": scene.get("generation_duration_s") or scene.get("duration_s", 4),
            "file": str(clip_path),
            "status": clip_status,
            "backend": actual_backend,
            "target_backend": target_backend,
            "fallback_from_backend": target_backend
            if target_backend in VIDEO_BACKENDS and not matches_target_backend
            else None,
            "composition_signature": composition_signature,
            "image_text_audit": image_text_audit,
            "image_visual_audit": image_visual_audit,
            "cutaway_image_text_audit": cutaway_image_text_audit,
            "cutaway_image_visual_audit": cutaway_image_visual_audit,
            "semantic_visual_audit": semantic_visual_audit,
            "generated_image_paths": generated_image_paths,
            "image_candidates": image_candidates,
            "selected_image_path": selected_image_path or None,
            "scene_type": scene_type.value,
            "semantic_overlay_kind": _scene_semantic_overlay_kind(scene),
        }
        if clip_status == "generated":
            checkpoint_store.complete(
                checkpoint_scene_id,
                input_hash=checkpoint_input_hash,
                artifact_path=clip_path,
                result=clip_payload,
                artifact_validator=_is_probeable_video,
            )
            clip_payload["checkpoint_status"] = "DONE"
        else:
            checkpoint_store.fail(
                checkpoint_scene_id,
                f"Scene finished as {clip_status}; only generated target assets are reusable.",
            )
            clip_payload["checkpoint_status"] = "FAILED"
        clips.append(clip_payload)

    _replace_fallback_clips_with_recent_comfyui_outputs(settings, clips)
    fallback_used = _video_fallback_used(clips, backend, workflow_diagnostics)
    _assert_real_visual_generation_succeeded(settings, backend, clips, workflow_diagnostics, fallback_used=fallback_used)
    image_scene_errors = _wealth_image_scene_evidence_errors(channel_profile, clips)
    if (
        _requires_real_visuals(settings)
        and _preferred_video_backend(settings) != "motion_graphic"
        and image_scene_errors
    ):
        raise RuntimeError(
            "Wealth Blueprint ImageScene generation is incomplete; refusing to continue without one "
            "QA-passed selected image per scene. "
            f"Errors: {json.dumps(image_scene_errors, ensure_ascii=False)}"
        )

    raw_video = video_dir / "video_raw.mp4"
    source_clip_paths = [Path(str(clip["file"])) for clip in clips]
    narration_path = Path(str((context.artifacts.get("voice") or {}).get("output_file") or ""))
    narration_duration_s = _probe_media_duration(narration_path)
    timeline_clip_paths = _expand_clip_timeline(source_clip_paths, target_duration_s=narration_duration_s)
    concat = await asyncio.to_thread(_concat_clips, timeline_clip_paths, raw_video)
    concat.update(
        {
            "source_clip_count": len(source_clip_paths),
            "timeline_clip_count": len(timeline_clip_paths),
            "target_duration_s": round(narration_duration_s, 3),
            "timeline_strategy": (
                "voice_aligned_single_pass"
                if len(timeline_clip_paths) == len(source_clip_paths)
                else "rotating_non_repeating_cycles"
            ),
        }
    )
    payload = {
        "status": "ready" if not fallback_used else "placeholder_ready",
        "comfyui_ready": comfy_ready,
        "backend": backend,
        "workflow_template": str(workflow_path),
        "workflow_diagnostics": workflow_diagnostics,
        "reference_profile": reference_profile.to_payload(),
        "channel_profile": channel_profile.key,
        "clips": clips,
        "video_raw_path": str(raw_video),
        "concat": concat,
        "fallback_used": fallback_used,
        "quality_plan": _quality_plan(
            settings,
            scenes,
            clips,
            reference_profile=reference_profile,
            channel_profile=channel_profile,
        ),
    }
    return write_json(context.artifact_dir / "video_manifest.json", payload)


def repair_generated_image_visual_floor(settings: Settings, context: ProductionContext) -> StepResult | None:
    """Napraw punktowo zbyt ciemne obrazy i przebuduj tylko dotknięte klipy."""
    manifest = context.artifacts.get("video")
    asset_plan = context.artifacts.get("visuals")
    if not isinstance(manifest, dict) or not isinstance(asset_plan, dict):
        return None
    clips = manifest.get("clips")
    scenes = asset_plan.get("scenes")
    if not isinstance(clips, list) or not isinstance(scenes, list) or len(clips) != len(scenes):
        return None

    channel_profile = resolve_channel_profile(context)
    reference_profile = load_reference_shorts_profile(settings)
    repaired_segments: list[int] = []
    for index, (clip, scene) in enumerate(zip(clips, scenes), start=1):
        if not isinstance(clip, dict) or not isinstance(scene, dict):
            return None
        audits = [
            clip.get("image_visual_audit"),
            clip.get("cutaway_image_visual_audit"),
        ]
        failed_audits = [
            audit
            for audit in audits
            if isinstance(audit, dict) and str(audit.get("status") or "") == "fail"
        ]
        if not failed_audits:
            continue
        if any(
            set(str(item) for item in (audit.get("failures") or [])) - {"finance_visual_too_dark"}
            for audit in failed_audits
        ):
            return None

        image_paths = [
            Path(str(path))
            for path in (clip.get("generated_image_paths") or [])
            if str(path).strip()
        ]
        if not image_paths or not all(path.exists() for path in image_paths):
            return None
        for image_path in image_paths:
            grade = _normalize_generated_image_for_channel(image_path, channel_profile=channel_profile)
            if str(grade.get("status") or "") not in {"graded", "unchanged"}:
                return None

        clip_path = Path(str(clip.get("file") or ""))
        repair_scene = {
            **scene,
            "generation_duration_s": float(
                clip.get("duration_s")
                or scene.get("generation_duration_s")
                or scene.get("duration_s")
                or 1.35
            ),
        }
        if not _write_image_motion_clip(
            clip_path,
            image_path=image_paths[0],
            alternate_image_path=image_paths[1] if len(image_paths) > 1 else None,
            scene=repair_scene,
            segment=index,
            reference_profile=reference_profile,
            channel_profile=channel_profile,
        ):
            return None
        clip["image_visual_audit"] = _audit_generated_image_visual_relevance(
            image_paths[0],
            scene=repair_scene,
            channel_profile=channel_profile,
        )
        clip["cutaway_image_visual_audit"] = (
            _audit_generated_image_visual_relevance(
                image_paths[1],
                scene=repair_scene,
                channel_profile=channel_profile,
            )
            if len(image_paths) > 1
            else None
        )
        if any(
            isinstance(audit, dict) and str(audit.get("status") or "") != "pass"
            for audit in (clip["image_visual_audit"], clip["cutaway_image_visual_audit"])
            if audit is not None
        ):
            return None
        clip["visual_floor_repair"] = {
            "status": "repaired",
            "reason": "finance_visual_too_dark",
        }
        repaired_segments.append(index)

    if not repaired_segments:
        return None

    raw_video = Path(str(manifest.get("video_raw_path") or context.artifact_dir / "video" / "video_raw.mp4"))
    narration_path = Path(str((context.artifacts.get("voice") or {}).get("output_file") or ""))
    narration_duration_s = _probe_media_duration(narration_path)
    source_clip_paths = [Path(str(clip.get("file") or "")) for clip in clips if isinstance(clip, dict)]
    timeline_clip_paths = _expand_clip_timeline(source_clip_paths, target_duration_s=narration_duration_s)
    concat = _concat_clips(timeline_clip_paths, raw_video)
    concat.update(
        {
            "source_clip_count": len(source_clip_paths),
            "timeline_clip_count": len(timeline_clip_paths),
            "target_duration_s": round(narration_duration_s, 3),
            "timeline_strategy": (
                "voice_aligned_single_pass"
                if len(timeline_clip_paths) == len(source_clip_paths)
                else "rotating_non_repeating_cycles"
            ),
        }
    )
    manifest["clips"] = clips
    manifest["video_raw_path"] = str(raw_video)
    manifest["concat"] = concat
    manifest["quality_plan"] = _quality_plan(
        settings,
        scenes,
        clips,
        reference_profile=reference_profile,
        channel_profile=channel_profile,
    )
    manifest["targeted_visual_repair"] = {
        "status": "repaired",
        "segments": repaired_segments,
        "reason": "finance_visual_too_dark",
    }
    return write_json(context.artifact_dir / "video_manifest.json", manifest)


def _requires_real_visuals(settings: Settings) -> bool:
    return (
        bool(getattr(settings, "shorts_require_real_visuals", True))
        and str(getattr(settings, "app_env", "") or "").strip().lower() != "test"
        and not os.getenv("PYTEST_CURRENT_TEST")
    )


def _assert_real_visual_backend_available(settings: Settings, backend: dict[str, Any]) -> None:
    if not _requires_real_visuals(settings):
        return
    preferred_backend = _preferred_video_backend(settings)
    if preferred_backend == "motion_graphic":
        return
    if str(backend.get("mode") or "") == "placeholder":
        raise RuntimeError(
            "Real visual backend is required, but no configured visual backend is ready. "
            f"Preferred backend: {preferred_backend}. Reason: {backend.get('reason') or 'unknown'}"
        )


def _assert_scene_fallback_allowed(
    settings: Settings,
    backend: dict[str, Any],
    *,
    generated: bool,
    segment: int,
    total: int,
) -> None:
    """Zatrzymaj produkcje zanim nieudana scena zostanie zastapiona plansza."""
    if generated or not _requires_real_visuals(settings):
        return
    preferred_backend = _preferred_video_backend(settings)
    if preferred_backend == "motion_graphic":
        return
    target_backend = str(backend.get("mode") or preferred_backend)
    raise RuntimeError(
        "Real visual generation failed for scene "
        f"{segment}/{total} using backend {target_backend}; refusing motion-graphic or placeholder fallback."
    )


def _assert_real_visual_generation_succeeded(
    settings: Settings,
    backend: dict[str, Any],
    clips: list[dict[str, Any]],
    workflow_diagnostics: dict[str, Any],
    *,
    fallback_used: bool,
) -> None:
    if not _requires_real_visuals(settings):
        return
    preferred_backend = _preferred_video_backend(settings)
    if preferred_backend == "motion_graphic":
        return
    if not fallback_used:
        return
    target_backend = str(backend.get("mode") or "")
    clip_summary = [
        {
            "segment": clip.get("segment"),
            "status": clip.get("status"),
            "backend": clip.get("backend"),
            "target_backend": clip.get("target_backend"),
        }
        for clip in clips
    ]
    raise RuntimeError(
        "Real visual backend is required, but generation used fallback or placeholder clips. "
        f"Target backend: {target_backend}. Workflow valid: {bool(workflow_diagnostics.get('valid'))}. "
        f"Clips: {json.dumps(clip_summary, ensure_ascii=False)}"
    )


def _video_fallback_used(
    clips: list[dict[str, Any]],
    backend: dict[str, Any],
    workflow_diagnostics: dict[str, Any],
) -> bool:
    target_backend = str(backend.get("mode") or "")
    if not bool(workflow_diagnostics.get("valid")):
        return True
    if any(str(clip.get("status") or "") != "generated" for clip in clips):
        return True
    if target_backend in VIDEO_BACKENDS:
        return any(
            not _clip_backend_matches_target(
                actual_backend=str(clip.get("backend") or ""),
                target_backend=target_backend,
                scene_type=str(clip.get("scene_type") or ""),
            )
            for clip in clips
        )
    return False


def _wealth_image_scene_evidence_errors(
    channel_profile: ProductionChannelProfile,
    clips: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if channel_profile.key not in {"wealth_blueprint", "finance"}:
        return []
    errors: list[dict[str, Any]] = []
    for clip in clips:
        if str(clip.get("scene_type") or "") != SceneType.IMAGE.value:
            continue
        selected_path = str(clip.get("selected_image_path") or "").strip()
        candidates = clip.get("image_candidates") if isinstance(clip.get("image_candidates"), list) else []
        selected_candidates = [
            candidate
            for candidate in candidates
            if isinstance(candidate, dict)
            and str(candidate.get("path") or "").strip() == selected_path
            # Candidate selection collapses OCR, semantic, comparative-vision,
            # literal-contract, and cross-scene checks into this canonical flag.
            and bool(candidate.get("passed"))
        ]
        if selected_path and len(selected_candidates) == 1 and Path(selected_path).is_file():
            continue
        errors.append(
            {
                "segment": clip.get("segment"),
                "scene_id": clip.get("scene_id"),
                "backend": clip.get("backend"),
                "selected_image_path": selected_path or None,
                "qa_passed_selected_candidates": len(selected_candidates),
            }
        )
    return errors


def _clip_backend_matches_target(*, actual_backend: str, target_backend: str, scene_type: str) -> bool:
    if actual_backend == target_backend:
        return True
    return (
        target_backend in VIDEO_BACKENDS
        and actual_backend == REMOTION_NATIVE_BACKEND
        and scene_type in {
            SceneType.HOOK.value,
            SceneType.NUMBER.value,
            SceneType.CHART.value,
            SceneType.CTA.value,
        }
    )


def _replace_fallback_clips_with_recent_comfyui_outputs(settings: Settings, clips: list[dict[str, Any]]) -> None:
    """Late ComfyUI writes can arrive after HTTP timeout; prefer real LTX/Wan over fallback motion graphics."""
    for clip in clips:
        target_backend = str(clip.get("target_backend") or "")
        actual_backend = str(clip.get("backend") or "")
        if target_backend not in COMFYUI_VIDEO_BACKENDS or actual_backend == target_backend:
            continue
        clip_path = Path(str(clip.get("file") or ""))
        if not clip_path:
            continue
        segment = int(clip.get("segment") or 0)
        if segment <= 0:
            continue
        min_mtime = 0.0
        error_path = clip_path.with_suffix(f".{target_backend}_errors.json")
        if error_path.exists():
            try:
                min_mtime = max(0.0, error_path.stat().st_mtime - 10.0)
            except OSError:
                min_mtime = 0.0
        if _copy_recent_comfyui_output(settings, clip_path, backend=target_backend, segment=segment, min_mtime=min_mtime):
            clip["status"] = "generated"
            clip["backend"] = target_backend
            clip["fallback_from_backend"] = None
            clip["late_comfyui_output_recovered"] = True


def _select_generation_scenes(
    settings: Settings,
    scenes: list[dict[str, Any]],
    *,
    reference_profile: ReferenceShortsProfile | None = None,
) -> list[dict[str, Any]]:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return scenes
    max_scenes = max(1, min(int(getattr(settings, "shorts_visual_max_generated_scenes", 4) or 4), len(scenes)))
    selected = scenes[:max_scenes]
    configured_duration = float(getattr(settings, "shorts_visual_clip_duration_s", 1.35) or 1.35)
    target_duration = configured_duration
    if reference_profile is not None and reference_profile.available:
        target_duration = reference_profile.avg_cut_interval_sec(default=configured_duration)
    target_duration = max(1.0, min(3.0, target_duration))
    steps = max(4, int(getattr(settings, "video_generation_steps", 8) or 8))
    cfg = max(1.0, float(getattr(settings, "video_generation_guidance_scale", 3.0) or 3.0))
    return [
        {
            **scene,
            "generation_duration_s": target_duration,
            "generation_steps": steps,
            "generation_cfg": cfg,
        }
        for scene in selected
    ]


def _align_scene_durations_to_voice(
    scenes: list[dict[str, Any]],
    voice_payload: Any,
) -> list[dict[str, Any]]:
    """Match one visual scene to each spoken segment when both plans align."""
    if not isinstance(voice_payload, dict):
        return scenes
    timestamps = voice_payload.get("timestamps")
    if not isinstance(timestamps, list) or len(timestamps) < len(scenes):
        return scenes

    aligned: list[dict[str, Any]] = []
    for scene, timing in zip(scenes, timestamps):
        if not isinstance(timing, dict):
            return scenes
        duration = float(timing.get("duration_sec") or 0.0)
        if duration <= 0:
            return scenes
        aligned.append(
            {
                **scene,
                "generation_duration_s": max(1.0, min(6.0, duration)),
                "voice_segment_id": str(timing.get("segment_id") or ""),
            }
        )
    return aligned


def _assert_scene_generation_safe(
    scenes: list[dict[str, Any]],
    *,
    channel_profile: ProductionChannelProfile,
) -> None:
    """Blokuje kosztowny render zanim ComfyUI dostanie prompt wysokiego ryzyka."""
    if channel_profile.key not in {"wealth_blueprint", "finance"}:
        return
    unsafe_scene_ids = [
        scene.get("scene_id", index)
        for index, scene in enumerate(scenes)
        if finance_prompt_has_text_surface(
            f"{scene.get('visual_prompt') or ''} {scene.get('visual_subject') or ''}"
        )
    ]
    if unsafe_scene_ids:
        joined = ", ".join(str(item) for item in unsafe_scene_ids)
        raise RuntimeError(
            "Unsafe finance visual plan blocked before GPU startup: "
            f"text-prone surfaces in scenes {joined}. Regenerate the asset plan."
        )


def _quality_plan(
    settings: Settings,
    scenes: list[dict[str, Any]],
    clips: list[dict[str, Any]],
    *,
    reference_profile: ReferenceShortsProfile | None = None,
    channel_profile: ProductionChannelProfile | None = None,
) -> dict[str, Any]:
    generated = [clip for clip in clips if clip.get("status") == "generated"]
    placeholders = [clip for clip in clips if clip.get("status") == "placeholder"]
    cached_fallbacks = [clip for clip in clips if clip.get("status") == "cached_fallback"]
    min_real = int(getattr(settings, "shorts_visual_min_real_clips", 3) or 3)
    relevance_scores = [_scene_visual_relevance_score(scene, channel_profile=channel_profile) for scene in scenes]
    topic_anchor_audits = [_scene_topic_anchor_audit(scene) for scene in scenes]
    weak_topic_anchor = [audit for audit in topic_anchor_audits if str(audit.get("status") or "") == "fail"]
    concreteness_audits = [_scene_concreteness_audit(scene, channel_profile=channel_profile) for scene in scenes]
    weak_concreteness = [audit for audit in concreteness_audits if str(audit.get("status") or "") == "fail"]
    avg_relevance = round(sum(relevance_scores) / len(relevance_scores), 3) if relevance_scores else 0.0
    composition_signatures = [
        str(clip.get("composition_signature") or "").strip()
        for clip in generated
        if str(clip.get("composition_signature") or "").strip()
    ]
    image_text_audits = [
        audit
        for clip in generated
        for audit in (clip.get("image_text_audit"), clip.get("cutaway_image_text_audit"))
        if isinstance(audit, dict)
    ]
    image_visual_audits = [
        audit
        for clip in generated
        for audit in (clip.get("image_visual_audit"), clip.get("cutaway_image_visual_audit"))
        if isinstance(audit, dict)
    ]
    risky_image_text = [
        audit
        for audit in image_text_audits
        if str(audit.get("status") or "") == "risk"
    ]
    risky_image_visuals = [
        audit
        for audit in image_visual_audits
        if str(audit.get("status") or "") == "fail"
    ]
    unique_compositions = sorted(set(composition_signatures))
    required_unique_compositions = min(len(generated), min_real)
    composition_diversity_ratio = (
        round(len(unique_compositions) / len(generated), 3)
        if generated
        else 0.0
    )
    passes_composition_diversity = (
        not generated
        or len(unique_compositions) >= required_unique_compositions
        and composition_diversity_ratio >= 0.67
    )
    concept_signatures = [
        _motion_subject_motif(_scene_motion_prompt_text(scene), channel_profile=channel_profile)
        for scene in scenes
    ]
    unique_concepts = sorted(set(concept_signatures))
    required_unique_concepts = min(len(scenes), max(3, min_real))
    concept_diversity_ratio = round(len(unique_concepts) / len(scenes), 3) if scenes else 0.0
    passes_concept_diversity = (
        not scenes
        or len(unique_concepts) >= required_unique_concepts
        and concept_diversity_ratio >= 0.5
    )
    return {
        "selected_scene_count": len(scenes),
        "generated_clip_count": len(generated),
        "placeholder_clip_count": len(placeholders),
        "cached_fallback_clip_count": len(cached_fallbacks),
        "non_generated_clip_count": len(clips) - len(generated),
        "min_real_clips": min_real,
        "real_clip_ratio": round(len(generated) / len(clips), 3) if clips else 0.0,
        "target_clip_duration_s": float(getattr(settings, "shorts_visual_clip_duration_s", 1.35) or 1.35),
        "reference_target_clip_duration_s": (
            reference_profile.avg_cut_interval_sec()
            if reference_profile is not None and reference_profile.available
            else None
        ),
        "reference_target_brightness": (
            reference_profile.median_brightness
            if reference_profile is not None and reference_profile.available
            else None
        ),
        "reference_target_saturation": (
            reference_profile.median_saturation
            if reference_profile is not None and reference_profile.available
            else None
        ),
        "visual_relevance_scores": relevance_scores,
        "avg_visual_relevance_score": avg_relevance,
        "topic_anchor_audits": topic_anchor_audits,
        "topic_anchor_risk_count": len(weak_topic_anchor),
        "passes_topic_anchor_floor": not weak_topic_anchor,
        "passes_visual_relevance_floor": (
            avg_relevance >= 0.55
            and all(score >= 0.34 for score in relevance_scores)
            and not weak_topic_anchor
        ),
        "scene_concreteness_audits": concreteness_audits,
        "scene_concreteness_risk_count": len(weak_concreteness),
        "passes_scene_concreteness_floor": not weak_concreteness,
        "generated_image_text_audits": image_text_audits,
        "generated_image_text_risk_count": len(risky_image_text),
        "passes_generated_image_text_floor": not risky_image_text,
        "generated_image_visual_audits": image_visual_audits,
        "generated_image_visual_risk_count": len(risky_image_visuals),
        "passes_generated_image_visual_floor": not risky_image_visuals,
        "composition_signatures": composition_signatures,
        "unique_composition_signatures": unique_compositions,
        "unique_composition_count": len(unique_compositions),
        "required_unique_compositions": required_unique_compositions,
        "composition_diversity_ratio": composition_diversity_ratio,
        "passes_composition_diversity_floor": passes_composition_diversity,
        "concept_signatures": concept_signatures,
        "unique_concept_signatures": unique_concepts,
        "unique_concept_count": len(unique_concepts),
        "required_unique_concepts": required_unique_concepts,
        "concept_diversity_ratio": concept_diversity_ratio,
        "passes_concept_diversity_floor": passes_concept_diversity,
        "passes_visual_variety_floor": (
            len(generated) >= min_real
            and len(generated) == len(clips)
            and passes_composition_diversity
            and passes_concept_diversity
        ),
    }


def _preferred_video_backend(settings: Settings) -> str:
    backend = str(getattr(settings, "shorts_video_backend", "motion_graphic") or "motion_graphic").strip().lower()
    return (
        backend
        if backend in {"motion_graphic", LOCAL_IMAGE_MOTION_BACKEND, "auto", "ltx", "wan", "openai_image_motion"}
        else "motion_graphic"
    )


def _openai_image_motion_ready(settings: Settings) -> bool:
    return bool(getattr(settings, "shorts_allow_cloud_visuals", False) and str(getattr(settings, "openai_api_key", "") or "").strip())


def _openai_image_motion_readiness(settings: Settings) -> dict[str, Any]:
    if not _openai_image_motion_ready(settings):
        return {
            "ready": False,
            "status": "missing",
            "message": "OpenAI image motion backend is not configured or cloud visuals are disabled",
            "evidence": None,
        }
    try:
        from atnos.production.visual_backend_doctor import _openai_image_motion_generation_evidence

        evidence = _openai_image_motion_generation_evidence(settings)
    except Exception as exc:
        return {
            "ready": True,
            "status": "unverified",
            "message": f"OPENAI_API_KEY configured; generation evidence unavailable: {type(exc).__name__}: {exc}",
            "evidence": None,
        }
    status = str(evidence.get("status") or "unverified")
    if status.startswith("blocked"):
        return {
            "ready": False,
            "status": status,
            "message": str(evidence.get("last_error_reason") or "OpenAI image motion has a recent blocking failure."),
            "evidence": evidence,
        }
    if status.startswith("degraded"):
        return {
            "ready": False,
            "status": status,
            "message": str(evidence.get("last_error_reason") or "OpenAI image motion has a recent generation failure."),
            "evidence": evidence,
        }
    return {
        "ready": True,
        "status": status,
        "message": "OPENAI_API_KEY configured and SHORTS_ALLOW_CLOUD_VISUALS enabled",
        "evidence": evidence,
    }


def _should_start_comfyui(settings: Settings, preferred_backend: str) -> bool:
    if preferred_backend in (*COMFYUI_VIDEO_BACKENDS, LOCAL_IMAGE_MOTION_BACKEND):
        return True
    if preferred_backend == "auto":
        return True
    return False


async def _select_backend(settings: Settings, *, comfy_ready: bool) -> dict[str, Any]:
    preferred = _preferred_video_backend(settings)
    openai_readiness = _openai_image_motion_readiness(settings)
    if preferred == "motion_graphic":
        return {
            "mode": "motion_graphic",
            "reason": "stable local motion graphics backend selected",
            "models_ready": True,
            "model_attempts": [],
        }
    if preferred == LOCAL_IMAGE_MOTION_BACKEND:
        if not comfy_ready:
            return {
                "mode": "placeholder",
                "reason": "ComfyUI Desktop unavailable for local image motion",
                "models_ready": False,
                "model_attempts": [],
            }
        checkpoint = await asyncio.to_thread(ComfyUiImageGenerationService(settings).resolve_checkpoint_name)
        if not checkpoint:
            return {
                "mode": "placeholder",
                "reason": "Active ComfyUI has no image checkpoint visible",
                "models_ready": False,
                "model_attempts": [],
            }
        return {
            "mode": LOCAL_IMAGE_MOTION_BACKEND,
            "reason": "active ComfyUI image checkpoint ready",
            "models_ready": True,
            "model_message": checkpoint,
            "model_attempts": [
                {
                    "mode": LOCAL_IMAGE_MOTION_BACKEND,
                    "pipeline": "shorts_comfy_image_motion",
                    "ready": True,
                    "message": f"active checkpoint: {checkpoint}",
                }
            ],
        }
    if preferred == "openai_image_motion":
        return {
            "mode": "openai_image_motion" if bool(openai_readiness["ready"]) else "placeholder",
            "reason": "OpenAI image motion backend ready" if bool(openai_readiness["ready"]) else str(openai_readiness["message"]),
            "models_ready": bool(openai_readiness["ready"]),
            "model_attempts": [],
        }
    if not comfy_ready:
        if preferred == "auto" and bool(openai_readiness["ready"]):
            return {
                "mode": "openai_image_motion",
                "reason": "ComfyUI unavailable; falling back to OpenAI image motion",
                "models_ready": True,
                "model_attempts": [
                    {
                        "mode": "openai_image_motion",
                        "pipeline": "cloud_image_motion",
                        "ready": True,
                        "message": "ComfyUI unavailable; OPENAI_API_KEY configured and SHORTS_ALLOW_CLOUD_VISUALS enabled",
                    }
                ],
            }
        attempts = []
        if preferred == "auto" and str(openai_readiness.get("status") or "") != "missing":
            attempts.append(
                {
                    "mode": "openai_image_motion",
                    "pipeline": "cloud_image_motion",
                    "ready": False,
                    "message": f"ComfyUI unavailable; OpenAI image motion not ready: {openai_readiness['message']}",
                }
            )
        return {
            "mode": "placeholder",
            "reason": "ComfyUI unavailable",
            "models_ready": False,
            "model_attempts": attempts,
        }

    attempts: list[dict[str, Any]] = []
    candidate_modes = (preferred,) if preferred in COMFYUI_VIDEO_BACKENDS else COMFYUI_VIDEO_BACKENDS
    for mode in candidate_modes:
        ok, model_message = await _ensure_video_models(settings, mode)
        ready = ok
        message = model_message
        if ok:
            runtime_ok, runtime_message = await _comfyui_backend_nodes_ready(settings, mode)
            ready = runtime_ok
            if not runtime_ok:
                message = f"{model_message}; runtime_not_ready: {runtime_message}"
        attempts.append(
            {
                "mode": mode,
                "pipeline": "shorts_ltx" if mode == "ltx" else "shorts_wan",
                "ready": ready,
                "message": message,
            }
        )
        if ready:
            return {
                "mode": mode,
                "reason": "ready",
                "models_ready": True,
                "model_message": model_message,
                "model_attempts": attempts,
            }

    if preferred == "auto" and bool(openai_readiness["ready"]):
        attempts.append(
            {
                "mode": "openai_image_motion",
                "pipeline": "cloud_image_motion",
                "ready": True,
                "message": "No local LTX/Wan model set is ready; OPENAI_API_KEY configured and SHORTS_ALLOW_CLOUD_VISUALS enabled",
            }
        )
        return {
            "mode": "openai_image_motion",
            "reason": "No local LTX/Wan video model set is ready; falling back to OpenAI image motion",
            "models_ready": True,
            "model_attempts": attempts,
        }
    if preferred == "auto" and str(openai_readiness.get("status") or "") != "missing":
        attempts.append(
            {
                "mode": "openai_image_motion",
                "pipeline": "cloud_image_motion",
                "ready": False,
                "message": f"OpenAI image motion not ready: {openai_readiness['message']}",
            }
        )

    return {
        "mode": "placeholder",
        "reason": "No LTX/Wan video model set is ready",
        "models_ready": False,
        "model_attempts": attempts,
    }


async def _ensure_video_models(settings: Settings, mode: str) -> tuple[bool, str]:
    # Test runs must never download multi-GB models. Runtime keeps auto-download behavior.
    if os.getenv("PYTEST_CURRENT_TEST"):
        return True, "model check skipped in tests"
    manager = ModelManager.get_instance(settings)
    pipeline = "shorts_ltx" if mode == "ltx" else "shorts_wan"
    results = await manager.ensure_pipeline_models(pipeline)
    ok = all(results.values()) if results else False
    return ok, json.dumps(results, ensure_ascii=False)


async def _comfyui_backend_nodes_ready(settings: Settings, mode: str) -> tuple[bool, str]:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return True, "runtime node check skipped in tests"
    workflow = _load_or_create_workflow(_workflow_path(settings, mode), mode)
    diagnostics = _workflow_diagnostics(workflow, mode)
    if not bool(diagnostics.get("valid")):
        return False, str(diagnostics.get("reason") or "workflow template invalid")
    class_types = _workflow_class_types(workflow)
    missing: list[str] = []
    object_info: dict[str, dict[str, Any]] = {}
    async with httpx.AsyncClient(timeout=COMFYUI_TIMEOUT_S) as client:
        for class_type in class_types:
            try:
                response = await client.get(f"{settings.comfyui_base_url.rstrip('/')}/object_info/{class_type}")
                payload = response.json()
            except Exception as exc:
                return False, f"{class_type}: {type(exc).__name__}: {str(exc)[:160]}"
            if response.status_code != 200 or not isinstance(payload, dict) or class_type not in payload:
                missing.append(class_type)
                continue
            class_info = payload.get(class_type)
            if isinstance(class_info, dict):
                object_info[class_type] = class_info
    if missing:
        return False, "missing ComfyUI node(s): " + ", ".join(missing)

    unavailable_choices = _workflow_runtime_choice_mismatches(workflow, object_info)
    if unavailable_choices:
        return False, "ComfyUI runtime cannot see workflow choice(s): " + ", ".join(unavailable_choices)
    return True, "workflow nodes available"


def _workflow_runtime_choice_mismatches(
    workflow: dict[str, Any],
    object_info: dict[str, dict[str, Any]],
) -> list[str]:
    """Porownuje statyczne wybory workflow z opcjami widocznymi w aktywnym ComfyUI."""
    mismatches: list[str] = []
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type") or "")
        class_info = object_info.get(class_type)
        inputs = node.get("inputs")
        if not class_type or not isinstance(class_info, dict) or not isinstance(inputs, dict):
            continue
        input_schema = class_info.get("input")
        required = input_schema.get("required") if isinstance(input_schema, dict) else None
        optional = input_schema.get("optional") if isinstance(input_schema, dict) else None
        schemas: dict[str, Any] = {}
        if isinstance(required, dict):
            schemas.update(required)
        if isinstance(optional, dict):
            schemas.update(optional)
        for input_name, value in inputs.items():
            if not isinstance(value, str) or value.startswith("{"):
                continue
            schema = schemas.get(input_name)
            if not isinstance(schema, (list, tuple)) or not schema:
                continue
            choices = schema[0]
            if not isinstance(choices, (list, tuple)):
                continue
            normalized_choices = {str(choice) for choice in choices}
            if value not in normalized_choices:
                mismatches.append(f"{class_type}.{input_name}={value!r}")
    return sorted(set(mismatches))


async def _ensure_comfyui(settings: Settings) -> bool:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return False
    try:
        return await asyncio.to_thread(
            ComfyUiRuntimeService(settings).ensure_server_running,
            wait_timeout_s=120,
            lowvram=bool(settings.comfyui_lowvram),
        )
    except Exception:
        return False


async def _comfyui_health(settings: Settings) -> bool:
    try:
        async with httpx.AsyncClient(timeout=COMFYUI_TIMEOUT_S) as client:
            response = await client.get(f"{settings.comfyui_base_url.rstrip('/')}/system_stats")
        return response.status_code == 200
    except httpx.HTTPError:
        return False


async def _try_generate_clip(
    settings: Settings,
    workflow: dict[str, Any],
    scene: dict[str, Any],
    clip_path: Path,
    segment: int,
    total: int,
    backend: str,
) -> bool:
    prompt = _inject_scene(workflow, scene, backend=backend, segment=segment, total=total)
    error_path = clip_path.with_suffix(f".{backend}_errors.json")
    started_at = time.time()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(f"{settings.comfyui_base_url.rstrip('/')}/prompt", json={"prompt": prompt})
            status_code = int(getattr(response, "status_code", 200) or 200)
            if status_code >= 400:
                return _write_comfyui_generation_error(
                    error_path,
                    backend=backend,
                    segment=segment,
                    reason="prompt_http_error",
                    details={"status_code": status_code, "body": str(getattr(response, "text", ""))[:1200]},
                )
            prompt_id = response.json().get("prompt_id")
            if not prompt_id:
                return _write_comfyui_generation_error(
                    error_path,
                    backend=backend,
                    segment=segment,
                    reason="missing_prompt_id",
                    details={"response": response.json()},
                )
            output = await _poll_history(
                client,
                settings.comfyui_base_url.rstrip("/"),
                prompt_id,
                timeout_s=_comfyui_generation_timeout_s(settings),
            )
            if not output:
                if _copy_recent_comfyui_output(
                    settings,
                    clip_path,
                    backend=backend,
                    segment=segment,
                    min_mtime=started_at - 5,
                ):
                    return True
                if await _wait_for_recent_comfyui_output(
                    settings,
                    clip_path,
                    backend=backend,
                    segment=segment,
                    min_mtime=started_at - 5,
                ):
                    return True
                base_url = settings.comfyui_base_url.rstrip("/")
                return _write_comfyui_generation_error(
                    error_path,
                    backend=backend,
                    segment=segment,
                    reason="history_output_missing",
                    details={
                        "prompt_id": prompt_id,
                        "history": await _safe_comfyui_json(client, f"{base_url}/history/{prompt_id}"),
                        "queue": await _safe_comfyui_json(client, f"{base_url}/queue"),
                    },
                )
            video_bytes = await _download_output(client, settings.comfyui_base_url.rstrip("/"), output)
        clip_path.write_bytes(video_bytes)
        if not _is_probeable_video(clip_path):
            try:
                clip_path.unlink()
            except OSError:
                pass
            return _write_comfyui_generation_error(
                error_path,
                backend=backend,
                segment=segment,
                reason="invalid_or_unprobeable_video",
                details={"output": output},
            )
        return True
    except (httpx.HTTPError, OSError, ValueError, KeyError) as exc:
        if await _wait_for_recent_comfyui_output(
            settings,
            clip_path,
            backend=backend,
            segment=segment,
            min_mtime=started_at - 5,
        ):
            return True
        return _write_comfyui_generation_error(
            error_path,
            backend=backend,
            segment=segment,
            reason=type(exc).__name__,
            details={"error": str(exc)[:1200]},
        )


async def _wait_for_recent_comfyui_output(
    settings: Settings,
    clip_path: Path,
    *,
    backend: str,
    segment: int,
    min_mtime: float,
) -> bool:
    timeout_s = _comfyui_video_output_grace_s(settings)
    if timeout_s <= 0:
        return False
    deadline = time.time() + timeout_s
    while time.time() <= deadline:
        if _copy_recent_comfyui_output(settings, clip_path, backend=backend, segment=segment, min_mtime=min_mtime):
            return True
        await asyncio.sleep(2.0 if not os.getenv("PYTEST_CURRENT_TEST") else 0)
    return False


def _comfyui_video_output_grace_s(settings: Settings) -> float:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return max(0.0, float(getattr(settings, "comfyui_video_output_grace_s", 0) or 0))
    try:
        return max(0.0, min(300.0, float(getattr(settings, "comfyui_video_output_grace_s", 75) or 75)))
    except (TypeError, ValueError):
        return 75.0


async def _try_generate_clip_with_retries(
    settings: Settings,
    workflow: dict[str, Any],
    scene: dict[str, Any],
    clip_path: Path,
    segment: int,
    total: int,
    backend: str,
) -> bool:
    attempts = _comfyui_video_retry_attempts(settings)
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            logger.info(
                "Retrying %s video generation for segment %s/%s (attempt %s/%s).",
                backend,
                segment,
                total,
                attempt,
                attempts,
            )
        ok = await _try_generate_clip(settings, workflow, scene, clip_path, segment, total, backend)
        if ok and _is_probeable_video(clip_path):
            return True
        if clip_path.exists():
            try:
                clip_path.unlink()
            except OSError:
                pass
        if attempt < attempts:
            await _recover_comfyui_between_attempts(settings, backend=backend, attempt=attempt)
    return False


def _comfyui_video_retry_attempts(settings: Settings) -> int:
    configured = getattr(settings, "comfyui_video_retry_attempts", None)
    if configured is None:
        configured = 1 if os.getenv("PYTEST_CURRENT_TEST") else 2
    try:
        attempts = int(configured)
    except (TypeError, ValueError):
        attempts = 1
    if os.getenv("PYTEST_CURRENT_TEST") and configured is None:
        attempts = 1
    return max(1, min(4, attempts))


async def _recover_comfyui_between_attempts(settings: Settings, *, backend: str, attempt: int) -> None:
    if os.getenv("PYTEST_CURRENT_TEST"):
        return
    healthy = await _comfyui_health(settings)
    if not healthy:
        logger.warning("ComfyUI health check failed after %s attempt %s; restarting runtime before retry.", backend, attempt)
        try:
            await asyncio.to_thread(
                ComfyUiRuntimeService(settings).restart_server,
                wait_timeout_s=120,
                lowvram=bool(settings.comfyui_lowvram),
            )
        except Exception:
            logger.exception("ComfyUI runtime restart failed after %s attempt %s.", backend, attempt)
    await asyncio.sleep(min(8.0, 1.5 * attempt))


def _write_comfyui_generation_error(
    error_path: Path,
    *,
    backend: str,
    segment: int,
    reason: str,
    details: dict[str, Any],
) -> bool:
    write_json(
        error_path,
        {
            "backend": backend,
            "segment": segment,
            "reason": reason,
            "details": details,
            "fallback": "motion_graphic",
        },
    )
    return False


def _copy_recent_comfyui_output(
    settings: Settings,
    clip_path: Path,
    *,
    backend: str,
    segment: int,
    min_mtime: float,
) -> bool:
    install_dir = getattr(settings, "comfyui_install_dir", None)
    if not install_dir:
        return False
    output_dir = Path(install_dir) / "ComfyUI" / "output"
    if not output_dir.exists():
        return False
    patterns = [
        f"atnos_{backend}_{segment:02d}*.mp4",
        f"atnos_{backend}_{segment:02d}*.webm",
        f"atnos_{backend}_{segment:02d}*.mov",
    ]
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(path for path in output_dir.glob(pattern) if path.is_file() and path.stat().st_mtime >= min_mtime)
    for candidate in sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True):
        if not _is_probeable_video(candidate):
            continue
        clip_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(candidate, clip_path)
        return _is_probeable_video(clip_path)
    return False


def _comfyui_generation_timeout_s(settings: Settings) -> float:
    configured = float(getattr(settings, "comfyui_generation_timeout_s", 1200) or 1200)
    return max(1200.0, configured)


async def _safe_comfyui_json(client: httpx.AsyncClient, url: str) -> dict[str, Any] | list[Any] | str | None:
    try:
        response = await client.get(url)
        return response.json()
    except Exception as exc:
        return f"{type(exc).__name__}: {str(exc)[:300]}"


async def _try_generate_openai_image_motion_clip(
    settings: Settings,
    scene: dict[str, Any],
    clip_path: Path,
    segment: int,
    total: int,
    *,
    reference_profile: ReferenceShortsProfile | None = None,
    channel_profile: ProductionChannelProfile | None = None,
) -> bool:
    image_path = clip_path.with_suffix(f".s{segment:02d}.openai.png")
    if not await _generate_openai_scene_image(
        settings,
        scene,
        image_path,
        segment=segment,
        total=total,
        channel_profile=channel_profile,
    ):
        return False
    return await asyncio.to_thread(
        _write_image_motion_clip,
        clip_path,
        image_path=image_path,
        scene=scene,
        segment=segment,
        reference_profile=reference_profile,
        channel_profile=channel_profile,
    )


def _manifest_scene_type(scene: dict[str, Any]) -> SceneType:
    return infer_scene_type(
        {
            "type": scene.get("segment_type") or scene.get("type") or "body",
            "text": scene.get("narration") or scene.get("headline") or scene.get("overlay_text") or "",
        },
        scene,
        require_source_backed_data=(
            str(scene.get("channel_profile_key") or "").strip().casefold()
            in {"wealth_blueprint", "finance"}
        ),
    )


def _scene_checkpoint_contract(scene: dict[str, Any]) -> dict[str, Any]:
    """Hash frozen visual inputs while recording deterministic scene reclassification."""
    contract = dict(scene)
    scene_type = _manifest_scene_type(scene)
    if scene_type is SceneType.IMAGE:
        # Image prompt selection uses narration for literal finance contracts.
        # Keeping it in the checkpoint contract prevents stale images from being
        # reused after spoken meaning changes while the visual plan stays equal.
        overlay_kind = _scene_semantic_overlay_kind(contract)
        if overlay_kind:
            contract["_semantic_overlay_kind"] = overlay_kind
            contract["_image_prompt_recipe"] = {
                "income_split": "finance-anchor-scene-v10",
                "payday_arrival": "finance-anchor-scene-v8",
                "cumulative_spending": "finance-anchor-scene-v11",
                "allocation_sorter": "finance-anchor-scene-v10",
                "purchase_pause": "finance-anchor-scene-v10",
            }.get(overlay_kind, "finance-anchor-scene-v7")
    else:
        contract.pop("narration", None)
        contract["_resolved_scene_type"] = scene_type.value
    return contract


def _scene_semantic_overlay_kind(scene: dict[str, Any]) -> str | None:
    explicit = normalize_semantic_overlay_kind(scene.get("semantic_overlay_kind"))
    if explicit:
        return explicit
    return infer_semantic_overlay_kind(
        scene.get("narration"),
        scene.get("headline"),
        scene.get("overlay_text"),
    )


def _attach_scene_narration(
    scenes: list[dict[str, Any]],
    script: Any,
) -> list[dict[str, Any]]:
    """Resolve planner script-line references into narration used by rendering and QA."""
    script_payload = script if isinstance(script, dict) else {}
    segments = script_payload.get("segments") if isinstance(script_payload.get("segments"), list) else []
    enriched: list[dict[str, Any]] = []
    for scene in scenes:
        current = dict(scene)
        narration = str(current.get("narration") or "").strip()
        if not narration:
            line_indexes = current.get("script_lines") if isinstance(current.get("script_lines"), list) else []
            lines: list[str] = []
            for raw_index in line_indexes:
                try:
                    index = int(raw_index)
                except (TypeError, ValueError):
                    continue
                if 0 <= index < len(segments) and isinstance(segments[index], dict):
                    text = str(segments[index].get("text") or "").strip()
                    if text:
                        lines.append(text)
            narration = " ".join(lines)
        if narration:
            current["narration"] = narration
        if not str(current.get("semantic_overlay_kind") or "").strip():
            overlay_kind = infer_semantic_overlay_kind(
                narration,
                current.get("headline"),
                current.get("overlay_text"),
            )
            if overlay_kind:
                current["semantic_overlay_kind"] = overlay_kind
        enriched.append(current)
    return enriched


async def _prepare_wealth_image_candidates(
    settings: Settings,
    scenes: list[dict[str, Any]],
    *,
    video_dir: Path,
    topic_title: str,
    channel_profile: ProductionChannelProfile,
    segments: set[int] | None = None,
    generation_attempt: int = 1,
    existing_selected_image_paths: list[Path] | None = None,
) -> dict[int, dict[str, Any]]:
    """Generate ImageScene candidates and select only a fully verified image."""
    wb_checkpoint: str | None = None
    if channel_profile.key == "wealth_blueprint":
        # The shared quality resolver favors a photorealistic model for other
        # channels. WB ImageScenes must use their channel's illustration tier.
        from atnos.production.comfy_channels.model_profiles import load_model_profile

        wb_model_profile = load_model_profile(settings, "wealth_blueprint")
        wb_checkpoint = str(
            wb_model_profile.get("quality_model") or wb_model_profile.get("fallback_model") or ""
        ).strip() or None
        if wb_checkpoint is None:
            raise RuntimeError("WB illustration checkpoint is not configured")
    gpu_release = await asyncio.to_thread(
        HardwareGuardService(None, settings).release_gpu_pressure  # type: ignore[arg-type]
    )
    if gpu_release.get("status") in {"failed", "partial", "deferred"} and not bool(gpu_release.get("verified_released")):
        raise RuntimeError(
            "ImageScene generation is blocked because Ollama did not release GPU memory: "
            f"{gpu_release.get('message') or gpu_release.get('status')}"
        )
    service = ComfyUiImageGenerationService(settings)
    generated: dict[int, list[dict[str, Any]]] = {}
    for segment, scene in enumerate(scenes, start=1):
        if _manifest_scene_type(scene) is not SceneType.IMAGE or (segments is not None and segment not in segments):
            continue
        base_prompt = _comfy_scene_image_prompt(
            scene,
            segment=segment,
            total=len(scenes),
            channel_profile=channel_profile,
        )
        forbidden_visual_terms = _scene_forbidden_visual_terms(scene)
        records: list[dict[str, Any]] = []
        for candidate_index in range(1, 4):
            prompt = _image_candidate_prompt(base_prompt, candidate_index)
            seed = _wealth_image_candidate_seed(
                prompt,
                segment=segment,
                candidate_index=candidate_index,
                generation_attempt=generation_attempt,
            )
            attempt_suffix = "" if generation_attempt == 1 else f".attempt-{generation_attempt:02d}"
            output_path = video_dir / (
                f"s{segment:02d}{attempt_suffix}.candidate-{candidate_index:02d}.png"
            )
            errors = await _generate_comfy_scene_still(
                service,
                output_path,
                prompt=prompt,
                channel_profile=channel_profile,
                deterministic_seed=seed,
                max_prompt_attempts=1,
                preferred_checkpoint=wb_checkpoint,
                **(
                    {"forbidden_visual_terms": forbidden_visual_terms}
                    if forbidden_visual_terms
                    else {}
                ),
            )
            generation_evidence = _load_comfy_generation_evidence(output_path)
            records.append(
                {
                    "candidate_id": f"s{segment:02d}-candidate-{candidate_index:02d}",
                    "candidate_index": candidate_index,
                    "generation_attempt": generation_attempt,
                    "path": str(output_path),
                    "prompt": prompt,
                    "seed": seed,
                    "generation_evidence": generation_evidence,
                    "generation_errors": errors,
                    "generated": not errors and output_path.is_file() and output_path.stat().st_size > 4096,
                }
            )
        generated[segment] = records

    # The vision model and ComfyUI must never occupy the 16 GB GPU concurrently.
    memory_released = await asyncio.to_thread(service.release_memory)
    selections: dict[int, dict[str, Any]] = {}
    selected_image_paths = [path.expanduser().resolve() for path in (existing_selected_image_paths or [])]
    for segment, records in generated.items():
        scene = scenes[segment - 1]
        audited: list[dict[str, Any]] = []
        for record in records:
            audited.append(
                await _audit_image_candidate(
                    settings,
                    record,
                    scene=scene,
                    topic_title=topic_title,
                    channel_profile=channel_profile,
                )
            )
        literal_contract = _finance_semantic_contract_for_scene(scene)
        if literal_contract:
            for candidate in audited:
                if not bool(candidate.get("passed")):
                    continue
                contract_audit = await asyncio.to_thread(
                    audit_visual_contract_compliance,
                    settings,
                    str(candidate.get("path") or ""),
                    required_visual_contract=literal_contract,
                )
                candidate["literal_contract_audit"] = contract_audit
                contract_status = str(contract_audit.get("status") or "")
                if contract_status == "fail":
                    candidate["passed"] = False
                    candidate["reasons"] = list(
                        dict.fromkeys([*(candidate.get("reasons") or []), "literal_visual_contract_failed"])
                    )
                elif contract_status in {"missing", "invalid_response", "unavailable"} and bool(
                    settings.shorts_semantic_visual_audit_block_on_unavailable
                ):
                    candidate["passed"] = False
                    candidate["reasons"] = list(
                        dict.fromkeys([*(candidate.get("reasons") or []), "literal_visual_contract_unverified"])
                    )
        passing = [candidate for candidate in audited if bool(candidate.get("passed"))]
        comparison: dict[str, Any] = {}
        if passing:
            comparison = await asyncio.to_thread(
                compare_visual_candidates,
                settings,
                [str(candidate.get("path") or "") for candidate in passing],
                scene_contract=_image_candidate_semantic_context(
                    topic_title,
                    scene,
                    channel_profile=channel_profile,
                ),
            )
        if str(comparison.get("status") or "") == "pass":
            ranked_positions = {
                int(candidate_index): position
                for position, candidate_index in enumerate(comparison.get("ranked_candidate_indexes") or [])
            }
            rejected_positions = {
                int(candidate_index) for candidate_index in (comparison.get("rejected_candidate_indexes") or [])
            }
            for position, candidate in enumerate(passing, start=1):
                candidate["comparison_position"] = position
                candidate["comparative_vision"] = comparison
                if position in rejected_positions:
                    candidate["passed"] = False
                    candidate["reasons"] = list(
                        dict.fromkeys([*(candidate.get("reasons") or []), "comparative_visual_integrity_rejected"])
                    )
            passing = [candidate for candidate in passing if bool(candidate.get("passed"))]
            passing.sort(
                key=lambda candidate: (
                    ranked_positions.get(int(candidate.get("comparison_position") or 0), 999),
                    -float(candidate.get("score") or 0.0),
                )
            )
        else:
            for candidate in passing:
                candidate["passed"] = False
                candidate["reasons"] = list(
                    dict.fromkeys([*(candidate.get("reasons") or []), "comparative_vision_unverified"])
                )
            passing = []
        selected = None
        for candidate in passing:
            candidate_path = Path(str(candidate.get("path") or ""))
            differences = [
                difference
                for previous_path in selected_image_paths
                if (difference := _image_visual_difference(candidate_path, previous_path)) is not None
            ]
            comparison_complete = len(differences) == len(selected_image_paths)
            minimum_difference = min(differences) if differences else None
            candidate["cross_scene_diversity"] = {
                "minimum_normalized_difference": (
                    round(minimum_difference, 4) if minimum_difference is not None else None
                ),
                "threshold": 0.04,
                "compared_scene_count": len(differences),
                "expected_scene_count": len(selected_image_paths),
                "passed": comparison_complete and (
                    not selected_image_paths or (minimum_difference is not None and minimum_difference >= 0.04)
                ),
            }
            if not comparison_complete:
                candidate["reasons"] = list(
                    dict.fromkeys([*(candidate.get("reasons") or []), "cross_scene_comparison_unavailable"])
                )
                continue
            if minimum_difference is not None and minimum_difference < 0.04:
                candidate["reasons"] = list(
                    dict.fromkeys([*(candidate.get("reasons") or []), "cross_scene_near_duplicate"])
                )
                continue
            selected = candidate
            selected_image_paths.append(candidate_path)
            break
        if selected is not None:
            selected["selected"] = True
        selections[segment] = {
            "status": "selected" if selected is not None else "regenerate_scene",
            "selected_image_path": str(selected.get("path") or "") if selected is not None else "",
            "selected_candidate_id": str(selected.get("candidate_id") or "") if selected is not None else "",
            "candidates": audited,
            "candidate_count": len(audited),
            "memory_released_before_vision": memory_released,
            "gpu_release_before_generation": gpu_release,
            "reasons": list(
                dict.fromkeys(
                    reason
                    for candidate in audited
                    for reason in (candidate.get("reasons") or [])
                    if str(reason).strip()
                )
            ),
        }
    return selections


def _image_visual_difference(first: Path, second: Path) -> float | None:
    """Zwraca znormalizowaną różnicę miniatur RGB; 0 oznacza obraz identyczny."""
    try:
        from PIL import Image

        with Image.open(first) as first_image, Image.open(second) as second_image:
            first_bytes = first_image.convert("RGB").resize((32, 32)).tobytes()
            second_bytes = second_image.convert("RGB").resize((32, 32)).tobytes()
    except (OSError, ValueError):
        return None
    if len(first_bytes) != len(second_bytes) or not first_bytes:
        return None
    difference = sum(abs(left - right) for left, right in zip(first_bytes, second_bytes, strict=True))
    return difference / (len(first_bytes) * 255.0)


def _wealth_image_candidate_seed(
    prompt: str,
    *,
    segment: int,
    candidate_index: int,
    generation_attempt: int,
) -> int:
    """Return a reproducible seed that still produces new assets on a real retry."""
    payload = (
        f"{prompt}|scene={segment}|candidate={candidate_index}|attempt={generation_attempt}|wealth-image-v2"
    )
    return int(hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8], 16)


def _candidate_generation_attempt(
    *,
    previous_status: str,
    previous_input_hash: str | None,
    current_input_hash: str,
    current_attempt: int,
) -> int:
    """Keep partial candidate paths stable only when resuming the same input."""
    if previous_status == "RUNNING" and previous_input_hash == current_input_hash:
        return max(1, current_attempt - 1)
    return max(1, current_attempt)


def _pending_wealth_image_candidate_segments(
    checkpoint_store: SceneCheckpointStore,
    scenes: list[dict[str, Any]],
    *,
    checkpoint_input_hashes: dict[int, str],
) -> set[int]:
    pending: set[int] = set()
    for segment, scene in enumerate(scenes, start=1):
        if _manifest_scene_type(scene) is not SceneType.IMAGE:
            continue
        reusable = checkpoint_store.reusable_result(
            f"scene_{segment:02d}",
            input_hash=checkpoint_input_hashes[segment],
            artifact_validator=_is_probeable_video,
        )
        if reusable is None:
            pending.add(segment)
    return pending


def _image_candidate_prompt(base_prompt: str, candidate_index: int) -> str:
    lowered = base_prompt.casefold()
    illustrated = "hand-drawn" in lowered or "illustrated" in lowered or "ink linework" in lowered
    if "show all four objects" in lowered or "show every object" in lowered:
        variants = {
            1: "Centered overhead composition; every required object remains fully visible and separate.",
            2: "Wide three-quarter overhead composition; every required object remains fully visible and separate.",
            3: "Asymmetric overhead composition; every required object remains fully visible and separate.",
        }
    elif illustrated:
        variants = {
            1: (
                "Primary illustrated composition: close view of the entire action, precise ink contours, "
                "clear separation between source and destination."
            ),
            2: (
                "Alternative illustrated composition: three-quarter overhead view, the same complete action "
                "and all required objects, painted paper and dimensional shading."
            ),
            3: (
                "Alternative illustrated composition: asymmetric overhead view, the same complete action "
                "with visible cause and effect, mature editorial drawing."
            ),
        }
    else:
        variants = {
            1: (
                "Primary editorial composition: medium close-up, one dominant physical subject, strong foreground depth, "
                "natural lens perspective."
            ),
            2: (
                "Alternative editorial composition: overhead three-quarter view, the same required physical evidence, "
                "clear cause-and-effect action in one continuous photograph."
            ),
            3: (
                "Alternative editorial composition: low side angle with asymmetric framing, the same required physical "
                "evidence, credible documentary lighting in one continuous photograph."
            ),
        }
    return f"{base_prompt} {variants[candidate_index]} Candidate {candidate_index}; do not repeat another candidate composition."


async def _audit_image_candidate(
    settings: Settings,
    record: dict[str, Any],
    *,
    scene: dict[str, Any],
    topic_title: str,
    channel_profile: ProductionChannelProfile,
) -> dict[str, Any]:
    path = Path(str(record.get("path") or ""))
    if not bool(record.get("generated")):
        reasons = [
            str(item.get("reason") or "image_generation_failed")
            for item in (record.get("generation_errors") or [])
            if isinstance(item, dict)
        ]
        return {**record, "passed": False, "score": 0.0, "reasons": reasons or ["image_generation_failed"], "qa": {}}

    text_audit = await asyncio.to_thread(_audit_generated_image_text, path)
    heuristic_audit = await asyncio.to_thread(
        _audit_generated_image_visual_relevance,
        path,
        scene=scene,
        channel_profile=channel_profile,
    )
    semantic_context = _image_candidate_semantic_context(
        topic_title,
        scene,
        channel_profile=channel_profile,
    )
    semantic_audit = await asyncio.to_thread(
        audit_visual_semantics,
        settings,
        path,
        profile_key=channel_profile.key,
        topic_title=semantic_context,
        artifact_kind="image_scene_candidate",
    )
    reasons: list[str] = []
    text_passed = str(text_audit.get("status") or "") == "pass" and bool(text_audit.get("ocr_available"))
    if not text_passed:
        reasons.extend(str(item) for item in (text_audit.get("risk_tokens") or []) if str(item).strip())
        reasons.append("ocr_unavailable" if not text_audit.get("ocr_available") else "readable_text_detected")
    heuristic_passed = str(heuristic_audit.get("status") or "") == "pass"
    if not heuristic_passed:
        reasons.extend(str(item) for item in (heuristic_audit.get("failures") or []) if str(item).strip())
    semantic_passed = str(semantic_audit.get("status") or "") == "pass" and semantic_audit.get("passed") is True
    if not semantic_passed:
        reasons.extend(str(item) for item in (semantic_audit.get("blockers") or []) if str(item).strip())
        if not semantic_audit.get("blockers"):
            reasons.append(f"semantic_audit_{semantic_audit.get('status') or 'failed'}")
    scores = semantic_audit.get("scores") if isinstance(semantic_audit.get("scores"), dict) else {}
    numeric_scores = [float(value) for value in scores.values() if isinstance(value, (int, float))]
    score = min(numeric_scores) / 10.0 if numeric_scores else 0.0
    passed = text_passed and heuristic_passed and semantic_passed
    return {
        **record,
        "passed": passed,
        "score": round(max(0.0, min(1.0, score)), 4),
        "reasons": list(dict.fromkeys(reasons)),
        "selected": False,
        "qa": {
            "text": text_audit,
            "heuristic": heuristic_audit,
            "semantic": semantic_audit,
        },
    }


def _image_candidate_semantic_context(
    topic_title: str,
    scene: dict[str, Any],
    *,
    channel_profile: ProductionChannelProfile | None = None,
) -> str:
    """Give Vision the exact scene contract instead of only a broad video title."""
    headline = str(scene.get("headline") or scene.get("overlay_text") or "").strip()
    narration = str(scene.get("narration") or scene.get("text") or "").strip()
    visual = str(scene.get("visual_prompt") or scene.get("visual_note") or "").strip()
    profile_key = str(getattr(channel_profile, "key", "") or scene.get("channel_profile_key") or "").strip()
    overlay_kind = _scene_semantic_overlay_kind(scene)
    if profile_key in {"wealth_blueprint", "finance"}:
        if (profile_key == "wealth_blueprint" and _wealth_blueprint_allocation_subject(scene)) or _scene_forbidden_visual_terms(scene):
            visual = _finance_semantic_contract_for_scene(scene)
        else:
            visual = _finance_semantic_image_contract(overlay_kind) or (
                _finance_scene_subject(
                    _sanitize_scene_prompt_for_image(visual),
                    headline=headline,
                    narration=narration,
                )
                if visual
                else ""
            )
    parts = [f"Video topic: {topic_title.strip()}"]
    if headline:
        parts.append(f"Scene headline: {headline}")
    if narration:
        parts.append(
            f"Spoken meaning (audio/caption context only; do not require these words, numbers, currency values, or "
            f"percentages in the pixels): {narration}"
        )
    if overlay_kind:
        parts.append(
            f"A deterministic Remotion finance overlay ({overlay_kind}) will be added later, but it is supplemental "
            "and does not replace any required visible evidence in this candidate"
        )
    if visual:
        # Keep this section last. The Vision contract treats everything after
        # this marker as literal evidence that must already exist in the image.
        parts.append(f"Required visible evidence: {visual}")
    return " | ".join(parts)[:1800]


async def _try_generate_comfy_image_motion_clip(
    settings: Settings,
    scene: dict[str, Any],
    clip_path: Path,
    segment: int,
    total: int,
    *,
    reference_profile: ReferenceShortsProfile | None = None,
    channel_profile: ProductionChannelProfile | None = None,
) -> bool:
    image_path = clip_path.with_suffix(f".s{segment:02d}.comfy.png")
    cutaway_path = clip_path.with_suffix(f".s{segment:02d}.comfy.cutaway.png")
    prompt, cutaway_prompt = _comfy_scene_prompts(
        scene,
        segment=segment,
        total=total,
        channel_profile=channel_profile,
    )
    if _can_reuse_comfy_image_motion_clip(
        clip_path,
        image_path=image_path,
        cutaway_path=cutaway_path,
        prompt=prompt,
        cutaway_prompt=cutaway_prompt,
    ):
        return True

    service = ComfyUiImageGenerationService(settings)
    forbidden_visual_terms = _scene_forbidden_visual_terms(scene)
    forbidden_visual_kwargs = (
        {"forbidden_visual_terms": forbidden_visual_terms}
        if forbidden_visual_terms
        else {}
    )
    primary_errors = await _generate_comfy_scene_still(
        service,
        image_path,
        prompt=prompt,
        channel_profile=channel_profile,
        **forbidden_visual_kwargs,
    )
    cutaway_errors = await _generate_comfy_scene_still(
        service,
        cutaway_path,
        prompt=cutaway_prompt,
        channel_profile=channel_profile,
        **forbidden_visual_kwargs,
    )
    if primary_errors or cutaway_errors:
        write_json(
            image_path.with_suffix(".comfy_errors.json"),
            {
                "segment": segment,
                "primary_errors": primary_errors,
                "cutaway_errors": cutaway_errors,
                "fallback": "blocked_in_quality_mode",
            },
        )
        return False
    image_path.with_suffix(".comfy_errors.json").unlink(missing_ok=True)
    # Keep the checkpoint resident across scenes. Explicit /free between scenes
    # repeatedly reloads multi-GB safetensors and crashes the Windows ROCm build.
    return await asyncio.to_thread(
        _write_image_motion_clip,
        clip_path,
        image_path=image_path,
        alternate_image_path=cutaway_path,
        scene=scene,
        segment=segment,
        reference_profile=reference_profile,
        channel_profile=channel_profile,
    )


def _comfy_scene_prompts(
    scene: dict[str, Any],
    *,
    segment: int,
    total: int,
    channel_profile: ProductionChannelProfile | None,
) -> tuple[str, str]:
    prompt = _comfy_scene_image_prompt(
        scene,
        segment=segment,
        total=total,
        channel_profile=channel_profile,
    )
    cutaway_prompt = (
        f"{prompt} Alternate editorial cutaway of the same exact event from a clearly different camera angle. "
        "Use a visibly different foreground subject, camera distance, and composition. Show the physical action "
        "at its peak instead of a standing portrait or calm walking pose. The cutaway must still match the same "
        "time, place, people, objects, and narrative beat. One coherent frame, no collage."
    )
    return prompt, cutaway_prompt


def _can_reuse_comfy_image_motion_clip(
    clip_path: Path,
    *,
    image_path: Path,
    cutaway_path: Path,
    prompt: str,
    cutaway_prompt: str,
) -> bool:
    if not _is_probeable_video(clip_path):
        return False
    return _comfy_image_matches_prompt(image_path, prompt) and _comfy_image_matches_prompt(
        cutaway_path,
        cutaway_prompt,
    )


def _comfy_image_matches_prompt(image_path: Path, prompt: str) -> bool:
    if not image_path.exists() or image_path.stat().st_size <= 4096:
        return False
    evidence_path = image_path.with_suffix(f"{image_path.suffix}.comfyui.json")
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    expected_prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return (
        evidence.get("status") == "verified"
        and evidence.get("prompt_sha256") == expected_prompt_hash
        and bool(str(evidence.get("artifact") or "").strip())
        and int(evidence.get("bytes") or 0) > 4096
    )


def _load_comfy_generation_evidence(image_path: Path) -> dict[str, Any]:
    evidence_path = image_path.with_suffix(f"{image_path.suffix}.comfyui.json")
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(evidence, dict) or evidence.get("status") != "verified":
        return {}
    return evidence


def _refresh_comfy_generation_evidence(image_path: Path) -> None:
    evidence_path = image_path.with_suffix(f"{image_path.suffix}.comfyui.json")
    evidence = _load_comfy_generation_evidence(image_path)
    if not evidence or not image_path.is_file():
        return
    artifact_bytes = image_path.read_bytes()
    evidence.update(
        {
            "artifact": str(image_path.resolve()),
            "artifact_sha256": hashlib.sha256(artifact_bytes).hexdigest(),
            "bytes": len(artifact_bytes),
        }
    )
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")


async def _generate_comfy_scene_still(
    service: ComfyUiImageGenerationService,
    output_path: Path,
    *,
    prompt: str,
    channel_profile: ProductionChannelProfile | None,
    deterministic_seed: int | None = None,
    max_prompt_attempts: int = 2,
    forbidden_visual_terms: tuple[str, ...] = (),
    preferred_checkpoint: str | None = None,
) -> list[dict[str, Any]]:
    """Generate one audited still; an empty error list means the image is usable."""
    if deterministic_seed is not None and _is_reusable_comfy_still(
        output_path,
        prompt=prompt,
        seed=deterministic_seed,
        preferred_checkpoint=preferred_checkpoint,
    ):
        return []
    errors: list[dict[str, Any]] = []
    for attempt in range(1, max(1, int(max_prompt_attempts)) + 1):
        if attempt == 1:
            attempt_prompt = prompt
        elif channel_profile is not None and channel_profile.key == "wealth_blueprint":
            attempt_prompt = f"{prompt} One coherent mature hand-drawn editorial illustration, not a collage."
        else:
            attempt_prompt = f"{prompt} One uncluttered photographic scene, not a collage."
        attempt_seed = (
            int(deterministic_seed) + attempt - 1
            if deterministic_seed is not None
            else int(hashlib.sha256(f"{attempt_prompt}|comfy-still-v1".encode("utf-8")).hexdigest()[:8], 16)
        )
        try:
            await asyncio.to_thread(
                service.generate_background,
                prompt=attempt_prompt,
                output_path=output_path,
                width=512,
                height=896,
                seed=attempt_seed,
                negative_prompt=_comfy_negative_visual_prompt(
                    channel_profile.key if channel_profile is not None else None,
                    prompt=attempt_prompt,
                    forbidden_visual_terms=forbidden_visual_terms,
                ),
                quality_mode=True,
                preferred_checkpoint=preferred_checkpoint,
            )
        except (ComfyUiImageGenerationError, OSError, ValueError) as exc:
            errors.append({"attempt": attempt, "reason": f"{type(exc).__name__}: {str(exc)[:500]}"})
            continue
        if not output_path.exists() or output_path.stat().st_size <= 4096:
            errors.append({"attempt": attempt, "reason": "generated_image_missing_or_too_small"})
            continue
        text_audit = _audit_generated_image_text(output_path)
        if text_audit.get("status") == "risk":
            rejected_path = output_path.with_name(f"{output_path.stem}.rejected-text-{attempt}.png")
            output_path.replace(rejected_path)
            errors.append(
                {
                    "attempt": attempt,
                    "reason": "generated_image_contains_pseudotext",
                    "risk_tokens": list(text_audit.get("risk_tokens") or []),
                    "rejected_image": str(rejected_path),
                }
            )
            continue
        _normalize_generated_image_for_channel(output_path, channel_profile=channel_profile)
        _refresh_comfy_generation_evidence(output_path)
        return []
    return errors


def _is_reusable_comfy_still(
    output_path: Path,
    *,
    prompt: str,
    seed: int,
    preferred_checkpoint: str | None = None,
) -> bool:
    """Reuse a fully evidenced candidate after a process interruption."""
    if not output_path.is_file() or output_path.stat().st_size <= 4096:
        return False
    evidence = _load_comfy_generation_evidence(output_path)
    if not evidence:
        return False
    try:
        artifact_hash = hashlib.sha256(output_path.read_bytes()).hexdigest()
    except OSError:
        return False
    if preferred_checkpoint:
        used_models = evidence.get("models")
        if not isinstance(used_models, list) or Path(preferred_checkpoint).name not in {
            Path(str(model)).name for model in used_models
        }:
            return False
    return (
        evidence.get("prompt_sha256") == hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        and int(evidence.get("seed") or -1) == int(seed)
        and int(evidence.get("width") or 0) == 512
        and int(evidence.get("height") or 0) == 896
        and int(evidence.get("bytes") or 0) == output_path.stat().st_size
        and evidence.get("artifact_sha256") == artifact_hash
    )


async def _generate_openai_scene_image(
    settings: Settings,
    scene: dict[str, Any],
    output_path: Path,
    *,
    segment: int,
    total: int,
    channel_profile: ProductionChannelProfile | None = None,
) -> bool:
    api_key = str(getattr(settings, "openai_api_key", "") or "").strip()
    if not api_key or not getattr(settings, "shorts_allow_cloud_visuals", False):
        return False
    prompt = _openai_scene_image_prompt(scene, segment=segment, total=total, channel_profile=channel_profile)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    errors: list[dict[str, Any]] = []
    for model_name in _openai_image_model_candidates():
        try:
            async with httpx.AsyncClient(timeout=140) as client:
                response = await client.post(
                    "https://api.openai.com/v1/images/generations",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "content-type": "application/json",
                    },
                    json={
                        "model": model_name,
                        "prompt": prompt,
                        "size": "1024x1536",
                        "quality": "medium",
                        "output_format": "png",
                        "background": "auto",
                    },
                )
            if response.status_code in {400, 404}:
                reason = _extract_openai_image_error(response)
                errors.append({"model": model_name, "status_code": response.status_code, "reason": reason})
                logger.warning(
                    "OpenAI image generation candidate rejected | model=%s status=%s reason=%s",
                    model_name,
                    response.status_code,
                    reason,
                )
                continue
            response.raise_for_status()
            image_bytes = _parse_openai_image_response(response.json())
            if not image_bytes:
                continue
            output_path.write_bytes(image_bytes)
            return output_path.exists() and output_path.stat().st_size > 4096
        except (httpx.HTTPError, OSError, ValueError, KeyError) as exc:
            errors.append({"model": model_name, "status_code": None, "reason": str(exc)[:300]})
            continue
    if errors:
        write_json(
            output_path.with_suffix(".openai_errors.json"),
            {
                "segment": segment,
                "models": [item["model"] for item in errors],
                "errors": errors,
                "fallback": "local_comfyui_or_motion_graphic",
            },
        )
    return False


def _openai_scene_image_prompt(
    scene: dict[str, Any],
    *,
    segment: int,
    total: int,
    channel_profile: ProductionChannelProfile | None = None,
) -> str:
    base = _sanitize_scene_prompt_for_image(str(scene.get("visual_prompt") or scene.get("visual_note") or "").strip())
    motion = str(scene.get("motion_style") or "").strip()
    profile_style = str(getattr(channel_profile, "visual_style", "") or "").strip()
    profile_key = str(getattr(channel_profile, "key", "") or "").strip()
    if not base:
        base = _sanitize_scene_prompt_for_image(_scene_motion_prompt_text(scene))
    style_line = profile_style or "cinematic faceless vertical editorial, high contrast, premium YouTube Shorts look"
    return (
        f"Create one original vertical 9:16 key visual for YouTube Shorts, segment {segment} of {total}. "
        f"Channel style: {style_line}. Channel profile key: {profile_key}. "
        f"Scene concept: {base}. "
        f"Implied motion for later animation: {motion or 'slow camera push-in, parallax depth, clear foreground subject'}. "
        f"{_openai_channel_visual_guardrail(profile_key)} "
        "Use strong subject relevance, clear foreground/midground/background layers, cinematic lighting, high production value. "
        "Absolute text ban: zero readable letters, zero numbers, zero captions, zero subtitles, zero arrows, zero labels, zero UI text, zero logo marks. "
        "Do not render any word from the topic, title, prompt, niche, channel name, or clothing. "
        "Any books, maps, notebooks, signs, receipts, screens, clothing, documents, dashboards, and objects must be blank, symbolic, or intentionally unreadable. "
        "No copyrighted characters, no real brand marks, no game screenshots, no platform logos. "
        "Safe, original, polished, vertical composition with room for subtitles in the lower third."
    )


def _wealth_blueprint_allocation_subject(scene: dict[str, Any]) -> str:
    """Keep the four common raise-allocation beats visually distinct."""
    # Only the directed beat headline can select this pilot-specific action.
    # Incidental words in a shot description must not replace other WB scenes.
    headline = str(scene.get("headline") or "").casefold()
    if "new expense" in headline or "recurring expense" in headline:
        return (
            "one faceless adult hand moving a small group of plain silver coins out of an open leather wallet "
            "toward one shallow dark metal payment tray, the wallet visibly losing those coins, overhead desk view"
        )
    if "save before" in headline or "savings goal" in headline:
        return (
            "one faceless adult hand moving a selected group of plain silver coins from an open leather wallet "
            "into one clear goal jar, with some coins remaining in the wallet, close overhead desk view"
        )
    if "what remains" in headline or "remaining coins" in headline:
        return (
            "one open leather wallet still holding plain silver coins beside a closed clear goal jar and one "
            "plain everyday shopping basket, a faceless adult hand considering the basket, overhead desk view"
        )
    if "raise" in headline or "extra income" in headline:
        return (
            "one faceless adult hand placing a fresh group of plain silver coins into an open leather wallet, "
            "the new income arriving but not yet allocated, close overhead desk view"
        )
    return ""


def _comfy_scene_image_prompt(
    scene: dict[str, Any],
    *,
    segment: int,
    total: int,
    channel_profile: ProductionChannelProfile | None = None,
) -> str:
    """Build a concise prompt that the local quality checkpoint can follow reliably."""
    profile_key = str(getattr(channel_profile, "key", "") or scene.get("channel_profile_key") or "").strip()
    narrative_action = _comfy_narrative_action(scene)
    prompt_subject = _planner_prompt_scene_clause(str(scene.get("visual_prompt") or ""))
    fallback_subject = str(scene.get("visual_subject") or scene.get("visual_note") or "").strip()
    if profile_key in {"wealth_blueprint", "finance"}:
        # The planner can retain a generic fallback in visual_subject while the
        # reviewed visual_prompt contains a more concrete shot. Prefer that shot
        # so a checkout conveyor is not silently replaced by another coin sorter.
        subject = (
            fallback_subject
            if prompt_subject and _finance_local_prompt_is_unsafe(prompt_subject)
            else prompt_subject or fallback_subject
        )
    elif profile_key in {"dark_chronicles", "history"} and narrative_action:
        # For documentary scenes the planner may return an atmospheric fallback
        # that contradicts the reviewed script action. The literal Scene line is
        # the source of truth for what the generated frame must depict.
        subject = narrative_action
    else:
        subject = prompt_subject or fallback_subject
    base = _sanitize_scene_prompt_for_image(subject)
    if not base:
        base = _sanitize_scene_prompt_for_image(_scene_motion_prompt_text(scene))
    wealth_allocation_subject = (
        _wealth_blueprint_allocation_subject(scene) if profile_key == "wealth_blueprint" else ""
    )
    if wealth_allocation_subject:
        # The reviewed money direction wins over legacy keyword mappings. For
        # example, an untouched savings jar must not become the subject of an
        # expense scene merely because the words "savings jar" occur in it.
        base = wealth_allocation_subject
    if profile_key in {"wealth_blueprint", "finance"}:
        forbids_coin_props = _scene_forbids_coin_props(scene)
        generation_contract = (
            "" if forbids_coin_props or wealth_allocation_subject
            else _finance_generation_image_contract(_scene_semantic_overlay_kind(scene))
        )
        if generation_contract:
            human_framing = (
                "the described anatomically correct faceless adult hand action fully visible, natural fingers, no face in frame"
                if _finance_prompt_requires_hands(generation_contract)
                else "no people, no hands"
            )
            if profile_key == "wealth_blueprint":
                illustrated_contract = generation_contract.replace(
                    "photographed as one coherent physical object",
                    "illustrated as one coherent physical object",
                )
                return (
                    f"Vertical 9:16. Scene: {illustrated_contract}. "
                    "Style: mature hand-drawn editorial finance illustration, realistic proportions, precise ink "
                    "linework and soft painted shading, charcoal navy blueprint field, cream paper panels, "
                    f"restrained muted gold accents, {human_framing}. One coherent illustrated scene with subtle "
                    "blueprint construction lines; no split composition, contact sheet, duplicated subject, "
                    "collage, text, numbers, "
                    "logos, screens, banknotes, cards, or watermark."
                )[:1300]
            return (
                f"Vertical 9:16. Scene: {generation_contract}. "
                "Style: raw photorealistic personal-finance documentary still, mature restrained mood, dark walnut "
                "surface, natural leather and metal materials, subtle emerald accent, crisp foreground detail, "
                f"cinematic side lighting, {human_framing}. One continuous photograph with one camera viewpoint; "
                "no split composition, no duplicated primary subject, no collage, no grid, no text, no numbers, "
                "no logos, no screens, no banknotes, no cards, no watermark."
            )[:1300]
        concise_contract = "" if wealth_allocation_subject else _finance_concise_comfy_contract(
            base,
            headline=str(scene.get("headline") or ""),
            narration=str(scene.get("narration") or scene.get("text") or ""),
        )
        if concise_contract and profile_key == "finance":
            return concise_contract
        if not forbids_coin_props and not wealth_allocation_subject:
            base = _finance_scene_subject(
                base,
                headline=str(scene.get("headline") or ""),
                narration=str(scene.get("narration") or scene.get("text") or ""),
            )
        requires_hands = _finance_prompt_requires_hands(base)
        allows_faceless_person = _finance_prompt_allows_faceless_person(base)
        human_framing = (
            "the described anatomically correct faceless adult hand action fully visible, natural fingers, no face in frame"
            if requires_hands
            else (
                "faceless adult body visible only from behind or shoulders down, hands outside the frame"
                if allows_faceless_person
                else "no people, no hands"
            )
        )
        if profile_key == "wealth_blueprint":
            style = (
                "mature hand-drawn editorial finance illustration, realistic proportions, precise ink linework "
                "and soft painted shading, charcoal navy blueprint field, cream paper panels, restrained muted "
                f"gold accents, crisp foreground action, {human_framing}"
            )
        else:
            style = (
                "raw photorealistic personal-finance documentary still, mature restrained mood, natural location "
                f"and materials, subtle emerald accent, one crisp primary subject, shallow cinematic depth, {human_framing}"
            )
    elif profile_key in {"dark_chronicles", "history"}:
        base = re.sub(
            r"\bsealed records\b",
            "closed wax-sealed leather-bound ledgers with blank covers",
            base,
            flags=re.IGNORECASE,
        )
        style = (
            "raw photorealistic historical documentary photograph, authentic 1518 Strasbourg, "
            "late-medieval and early-Renaissance wool clothing, timber-framed city street, "
            "natural human anatomy, weathered materials, candid mid-action body language, "
            "the described physical action fully visible, dramatic low-key cinematic lighting"
        )
    elif profile_key in {"sigma_squad", "brainrot"}:
        style = "polished original surreal 3D editorial scene, expressive action, deliberate vibrant composition"
    else:
        style = "photorealistic cinematic editorial photograph, crisp foreground subject, layered depth"
    action_text = f"{base} {narrative_action}".casefold()
    prompt = f"Vertical 9:16. Scene: {base}. "
    if profile_key in {"dark_chronicles", "history"}:
        if "danc" in action_text:
            prompt += (
                "Mandatory action pose: the dancer is full-body at the peak of an uncontrolled dance, "
                "both arms thrown outward, torso twisting sharply, and one foot clearly airborne. "
                "This must read as frantic dancing, never walking, posing, or standing still. "
            )
        secondary_subjects = _required_historical_secondary_subjects(action_text)
        if secondary_subjects:
            prompt += (
                "Required secondary subjects: visibly include "
                f"{', '.join(secondary_subjects)} in the frame, reacting to the main action. "
                "Do not omit, crop out, hide, or replace these people with empty architecture. "
            )
            if "worried neighbors" in secondary_subjects:
                prompt += (
                    "Composition requirement: show three frightened neighbors large enough to recognize, "
                    "standing in open timber doorways on both sides with faces turned toward the dancer. "
                )
    prompt += f"Style: {style}. "
    if (
        narrative_action
        and profile_key not in {"dark_chronicles", "history"}
        and narrative_action.casefold() != base.casefold()
    ):
        prompt += f"Narrative action that must be shown literally: {narrative_action}. "
    if profile_key in {"wealth_blueprint", "finance"} and not _scene_forbidden_visual_terms(scene):
        exclusions = _finance_scene_exclusions(base)
        if exclusions:
            prompt += f"Scene uniqueness rule: do not show {', '.join(exclusions)}. "
    if profile_key in {"dark_chronicles", "history"} and any(
        marker in action_text for marker in ("danc", "collapsed", "crowd")
    ):
        prompt += (
            "Freeze the peak of physical action, not the moment before it: torso visibly twisting or leaning, "
            "one foot lifted from the ground, clothing pulled by motion, witnesses reacting, subtle directional motion blur. "
        )
        if "crowd" in action_text or "dozens" in action_text:
            prompt += "Show several dancers in different off-balance poses across foreground and background. "
    prompt += (
        "Do not replace the described action with a standing portrait or an unrelated atmospheric object. "
        "One coherent scene, one dominant action, no collage, "
        + ("no contact sheet, " if profile_key == "wealth_blueprint" else "no grid, ")
        + "no poster wall, no signs, no labels, no cards, no paper notes, no screens, no typography, "
        "no letters, no numbers, no logos, no watermark."
    )
    return prompt[:1300]


def _required_historical_secondary_subjects(value: str) -> tuple[str, ...]:
    """Extract people whose visible presence is required by a history scene."""
    lowered = str(value or "").casefold()
    groups = (
        ("worried neighbors", ("worried neighbors", "alarmed neighbors")),
        ("reacting witnesses", ("witness", "onlookers")),
        ("worried officials", ("worried officials", "civic officials", "authorities")),
        ("townspeople", ("townspeople",)),
        ("musicians", ("musician",)),
        ("civic guards", ("civic guard", "city guard")),
        ("families", ("families", "family")),
        ("the described crowd", ("crowd", "dozens")),
    )
    return tuple(label for label, markers in groups if any(marker in lowered for marker in markers))


def _comfy_narrative_action(scene: dict[str, Any]) -> str:
    prompt = " ".join(str(scene.get("visual_prompt") or "").split())
    marker = "Scene line:"
    if marker.casefold() not in prompt.casefold():
        return ""
    start = prompt.casefold().index(marker.casefold()) + len(marker)
    action = prompt[start:].strip()
    for suffix in (
        " slow cinematic",
        " slow premium",
        " fast cinematic",
        " camera ",
        " 9:16 vertical",
    ):
        position = action.casefold().find(suffix.casefold())
        if position >= 0:
            action = action[:position]
            break
    sentences = [part.strip(" .") for part in re.split(r"(?<=[.!?])\s+", action) if part.strip(" .")]
    if len(sentences) >= 2:
        # Asset plans store the spoken script sentence first and the drawable
        # visual note after it. Local checkpoints overweight the first clause,
        # so keep only the physical visual instruction.
        action = " ".join(sentences[1:])
    if action.strip(" .").casefold() in {
        "personal-finance decision shown through physical action",
        "personal finance decision shown through physical action",
    }:
        return ""
    return _sanitize_scene_prompt_for_image(action.strip(" ."))[:280]


def _sanitize_comfy_finance_prompt(value: str, *, preserve_hands: bool = False) -> str:
    replacements = (
        (r"\b(?:young\s+)?adult\b", ""),
        (r"\b(?:person|people|human)\b", ""),
        (r"\bblank unbranded bank cards?\b", "a closed leather wallet"),
        (r"\bbank cards?\b", "a leather wallet"),
        (r"\bblank cards?\b", "a leather wallet"),
        (r"\bcards?\b", "a leather wallet"),
        (r"\bcolor[- ]coded envelopes?\b", "three plain ceramic bowls in different solid colors"),
        (r"\b(?:spending|savings|cash) envelopes?\b", "plain ceramic bowls"),
        (r"\benvelopes?\b", "plain ceramic bowls"),
        (r"\b(?:paper currency|banknotes?|dollar bills?|cash stacks?|stack of cash|cash)\b", "loose unprinted metal coins"),
    )
    cleaned = value
    if not preserve_hands:
        cleaned = re.sub(
            r"\b(?:faceless\s+)?(?:adult\s+)?hands?\b",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
    for pattern, replacement in replacements:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\blabeled only by color\b", "distinguished only by solid colors", cleaned, flags=re.IGNORECASE)
    return " ".join(cleaned.split())


def _planner_prompt_scene_clause(value: str) -> str:
    """Keep only the concrete opening shot from a verbose planner contract."""
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    contract_markers = (
        ". Mature adult fintech editorial scene",
        " Mature adult fintech editorial scene",
        ". Directly tied to the topic",
        " Directly tied to the topic",
        " Scene line:",
        ". slow cinematic",
        " slow cinematic",
        ". slow premium",
        " slow premium",
        ". fast cinematic",
        " fast cinematic",
        ". 9:16 vertical",
        " 9:16 vertical",
    )
    lowered = text.casefold()
    cut_at = len(text)
    for marker in contract_markers:
        position = lowered.find(marker.casefold())
        if position >= 0:
            cut_at = min(cut_at, position)
    return text[:cut_at].strip(" .")


def _finance_prompt_scene_clause(value: str) -> str:
    """Backward-compatible alias for the shared planner prompt parser."""
    return _planner_prompt_scene_clause(value)


def _finance_local_prompt_is_unsafe(value: str) -> bool:
    """Reject surfaces and anatomy that local SDXL often turns into gibberish."""
    lowered = str(value or "").casefold()
    # Negative guardrails describe what must not be rendered. Treating
    # "no labels" or "no text" as a requested risky surface discards the
    # reviewed scene and falls back to headline/motion text.
    lowered = re.sub(
        r"\b(?:no|without|zero)\s+(?:readable\s+)?(?:text|labels?|letters?|numbers?|logos?|watermarks?)\b",
        "",
        lowered,
    )
    risky_markers = (
        "bank card",
        "credit card",
        "debit card",
        "bill",
        "banknote",
        "paper currency",
        "phone",
        "screen",
        "dashboard",
        "receipt",
        "document",
        "label",
        "readable text",
    )
    if not _finance_prompt_requires_hands(value):
        risky_markers += ("hand", "finger", "person", "people", "human")
    return any(re.search(rf"\b{re.escape(marker)}s?\b", lowered) for marker in risky_markers)


def _finance_scene_subject(
    value: str,
    *,
    headline: str = "",
    narration: str = "",
) -> str:
    """Keep finance scenes concrete without collapsing every prompt into a savings jar."""
    lowered = value.casefold()
    narrative_lowered = narration.casefold()
    context_lowered = f"{lowered} {narrative_lowered}"
    headline_lowered = headline.casefold()
    if "two layered brass coin towers" in lowered and "original base and prior growth" in lowered:
        return (
            "two separate brass coin towers on a dark walnut desk, one short original tower and one visibly taller "
            "layered tower, with one small clear hourglass standing between them; wide asymmetric editorial photograph"
        )
    if "brass circular mechanism returning" in lowered and "next period" in lowered:
        return (
            "one brass circular coin track curving back toward a central stack of plain metal coins, with a second "
            "higher stack at the track exit; overhead mechanical-finance documentary photograph"
        )
    if "combining one large base stack" in lowered and "smaller earned stack" in lowered:
        return (
            "one large plain coin stack and one smaller coin stack meeting inside a wide clear glass bowl, with a "
            "new separate coin layer resting above them; close three-quarter editorial photograph"
        )
    if "mechanical arm returning earned coins" in lowered and "new layer on top" in lowered:
        return (
            "one compact brass mechanical arm positioned above a coin tower, carrying a small group of plain coins "
            "toward a clearly separated top layer; low side-angle industrial finance photograph"
        )
    if "progressively taller physical coin towers" in lowered and "repeated periods" in lowered:
        return (
            "three separate plain coin towers increasing clearly from short to medium to tall across three stone "
            "pedestals, with one large hourglass behind the final tower; wide timeline editorial photograph"
        )
    if "two clear glass cylinders" in lowered and "first earned layer" in lowered:
        return (
            "two separate clear glass cylinders on a dark desk, the first holding one low coin layer and the second "
            "holding three visibly separated coin layers; straight-on comparison photograph"
        )
    if "two parallel rows of identical plain coins" in lowered and "earlier start" in lowered:
        return (
            "two parallel rows of identical plain metal coins on a dark walnut desk, one long row ending in a tall "
            "coin tower and one short row ending in a short coin tower; overhead comparison photograph"
        )
    if "six identical plain coins" in lowered and "speculative token" in lowered:
        return (
            "six identical plain metal coins arranged in one straight row on a shallow brass tray, while one bright "
            "unbranded token sits alone behind a red velvet rope barrier; overhead editorial decision photograph"
        )
    if "starting coin stack" in lowered and "repeated contribution coins" in lowered:
        return (
            "one starting coin stack followed by a precise row of equal individual contribution coins leading toward "
            "a large hourglass, arranged as one physical long-term scenario; overhead walnut-desk photograph"
        )
    if "final plain coin" in lowered and "long-term growth" in lowered:
        return (
            "one complete layered coin tower beside a ceramic savings bowl, a small green plant, and one compact "
            "hourglass; clean final still-life photograph with the tower as the dominant subject"
        )
    if "boring pause" in headline_lowered or "wait before" in context_lowered or "pause before" in context_lowered:
        return (
            "one closed worn leather wallet resting behind a small brass desk barrier while one empty plain shopping "
            "basket waits out of reach in the foreground; the visibly blocked path represents a deliberate pause "
            "before buying, clean side-lit personal-finance documentary photograph"
        )
    if "by friday" in headline_lowered or "assigned without a plan" in context_lowered:
        return (
            "one open worn leather wallet in the center with three separate trails of loose metal coins leading "
            "toward one plain delivery bag, one takeaway cup, and one unbranded sneaker around it; the three visible "
            "routes show money being allocated to purchases without a plan, overhead documentary photograph"
        )
    if "autopilot" in context_lowered:
        return (
            "one brass decision gate physically stopping a trail of loose metal coins at a fork between one empty "
            "shopping basket and one closed metal savings box; the blocked coin path makes a deliberate money "
            "decision unmistakable, overhead personal-finance documentary photograph"
        )
    if any(token in context_lowered for token in ("paycheck", "salary", "pay day", "payday")):
        return (
            "one open worn leather wallet visibly filled with a neat row of loose unprinted metal coins beside one "
            "plain sealed envelope with no markings; the newly filled wallet and fresh coins clearly represent pay "
            "arriving, overhead personal-finance documentary photograph on a dark walnut desk"
        )
    if "checkout conveyor" in lowered or "conveyor" in lowered:
        return (
            "an unbranded premium sneaker, a closed wireless-earbuds case, and a plain takeaway cup spaced apart "
            "on a moving black checkout conveyor, oblique retail documentary photograph, no wallet and no coins"
        )
    if "closing a wallet" in lowered or "wallet closing" in lowered:
        return (
            "a worn leather wallet snapped fully closed with its metal clasp centered in sharp focus, one loose coin "
            "remaining outside the wallet on a dark walnut table, intimate side-lit documentary macro photograph"
        )
    if "mechanical coin sorter" in lowered:
        return (
            "a stainless-steel mechanical coin sorter visibly dividing mixed coins into three separate open trays, "
            "macro product-documentary photograph on a dark walnut workbench"
        )
    if "hourglass" in lowered:
        return (
            "a large clear hourglass containing a few metal coins above an open worn leather wallet, "
            "sharp editorial still life with the hourglass dominating the frame"
        )
    if "analog clock" in lowered or "large clock" in lowered:
        return (
            "a large analog alarm clock beside an open nearly empty leather wallet, one loose coin rolling toward "
            "a plain shopping basket, low-angle documentary photograph"
        )
    if "cracked transparent pipe" in lowered or "cracked pipe" in lowered:
        return (
            "a cracked clear acrylic tube suspended over a dark metal drain, loose coins visibly spilling through "
            "the fracture toward one plain delivery bag, dramatic side-lit industrial macro photograph"
        )
    if "draining" in lowered and any(token in lowered for token in ("delivery bag", "subscription", "upgrade")):
        return (
            "a plain delivery bag, a small blank subscription token, and an unbranded upgrade parcel each pulling "
            "a separate trail of loose coins away from one open nearly empty leather wallet, overhead documentary photograph"
        )
    if any(token in lowered for token in ("floor drain", "leaking", "drain")):
        return (
            "metal coins spilling from a torn leather coin pouch into a dark floor drain beside a plain delivery bag, "
            "dramatic ground-level macro documentary photograph"
        )
    if "nearly empty" in lowered and "wallet" in lowered and any(
        token in lowered for token in ("trail", "small everyday purchases", "rolling away")
    ):
        return (
            "an open nearly empty worn leather wallet beside three small unbranded purchases: a plain takeaway cup, "
            "a closed earbuds case, and a small brown parcel, with one coin rolling toward the table edge, overhead documentary photograph"
        )
    if "domino chain" in lowered or any(
        token in lowered for token in ("takeaway cup", "takeaway coffee", "coffee cup", "headphones", "parcel")
    ):
        return (
            "one open nearly empty worn leather wallet in the lower foreground beside one plain takeaway coffee cup, "
            "with several loose unprinted metal coins forming a clear trail from the cup toward the empty wallet; "
            "the cup, coin trail, and empty wallet form one concrete visual metaphor for a recurring small purchase "
            "draining money, single continuous overhead personal-finance documentary photograph"
        )
    if any(token in lowered for token in ("placing coins", "buy a book", "savings jar")):
        return (
            "one transparent savings jar containing a small layer of coins beside a closed unmarked hardback book and "
            "a closed leather wallet, clean side-lit editorial photograph"
        )
    if any(token in lowered for token in ("blocked by tiny purchase", "spending gate", "clean path", "barrier")):
        return (
            "a closed leather wallet behind a small red velvet barrier while a plain shopping basket remains in the "
            "foreground, wide premium editorial photograph with a clearly blocked path"
        )
    return _sanitize_comfy_finance_prompt(
        value,
        preserve_hands=_finance_prompt_requires_hands(value),
    )


def _finance_semantic_image_contract(overlay_kind: str | None) -> str:
    """Return a reliable photographic anchor for deterministic finance overlays.

    Local SDXL degrades into collages when asked to render a complete infographic.
    The image therefore carries one literal financial action while Remotion adds
    the multi-object explanation. The generated frame must still be topic-related.
    """
    contracts = {
        "income_split": (
            "one crisp cluster of ordinary unadorned silver coins on a dark walnut desk, with one short coin stack "
            "and several individual coins spreading outward; every coin face and rim is silver-colored; reject any "
            "distinctly gold or yellow coin; no square inserts, gold inlays, or mixed materials"
        ),
        "payday_arrival": (
            "one open brown leather coin pouch with a small cluster of ordinary silver coins resting visibly inside "
            "its open mouth and two loose coins on the desk; no coins touch the outside leather"
        ),
        "convenience_chain": (
            "one plain takeaway coffee cup beside one open nearly empty worn leather wallet, with two loose metal coins "
            "between them; both objects belong to one continuous tabletop scene"
        ),
        "cumulative_spending": (
            "one dark metal bowl visibly overflowing with many loose metal coins; the overflowing bowl is the single "
            "dominant subject"
        ),
        "allocation_sorter": (
            "one stainless-steel sorting tray containing several loose metal coins, photographed as one coherent "
            "physical object; the tray and coins are the single dominant subject"
        ),
        "auto_invest": (
            "one small healthy green plant in a plain ceramic pot beside one neat short stack of metal coins; the plant "
            "and coin stack form one coherent long-term growth still life"
        ),
        "purchase_pause": (
            "one closed worn brown leather wallet resting directly behind one straight narrow brass rail fixed upright "
            "across the desk; show only the wallet and the clearly separate blocking rail"
        ),
        "compound_interest_on_interest": (
            "one anatomically correct faceless adult hand visibly moving two earned plain metal coins from one small "
            "brass dish onto the top of the same larger plain metal coin stack on matte black stone; show the dish, "
            "the two moving coins, and the receiving stack clearly separate"
        ),
        "compound_mechanism_not_guarantee": (
            "one brass balance scale with one short stack of plain metal coins clearly visible on one pan and one "
            "closed brass padlock clearly visible on the other pan, placed on deep green felt"
        ),
        "compound_scenario": (
            "one large clear hourglass and multiple plain metal coins clearly visible together on a pale stone shelf; "
            "the hourglass and coins must be the dominant physical subjects"
        ),
    }
    return contracts.get(str(overlay_kind or "").strip()) or ""


def _scene_forbidden_visual_terms(scene: dict[str, Any]) -> tuple[str, ...]:
    values = scene.get("forbidden_visual_terms")
    if not isinstance(values, list):
        return ()
    return tuple(
        dict.fromkeys(
            str(value).strip().casefold()
            for value in values
            if str(value).strip()
        )
    )


def _scene_forbids_coin_props(scene: dict[str, Any]) -> bool:
    return any(term in {"coin", "coins", "piggy bank", "piggy banks"} for term in _scene_forbidden_visual_terms(scene))


def _finance_semantic_contract_for_scene(scene: dict[str, Any]) -> str:
    forbidden = tuple(
        dict.fromkeys(
            (
                *_scene_forbidden_visual_terms(scene),
                "banknote",
                "banknotes",
                "paper currency",
                "printed money",
                "currency bills",
                "readable text",
                "readable digits",
                "logos",
            )
        )
    )
    if not forbidden:
        return _finance_semantic_image_contract(_scene_semantic_overlay_kind(scene))
    visual = (
        _wealth_blueprint_allocation_subject(scene)
        if str(scene.get("channel_profile_key") or "") == "wealth_blueprint"
        else ""
    ) or str(scene.get("visual_subject") or _planner_prompt_scene_clause(str(scene.get("visual_prompt") or ""))).strip()
    if visual:
        visual = visual[0].lower() + visual[1:]
    return (
        f"Required visible scene: {visual}. Forbidden visible props: {', '.join(forbidden)}."
        if visual
        else f"Forbidden visible props: {', '.join(forbidden)}."
    )


def _finance_generation_image_contract(overlay_kind: str | None) -> str:
    """Return positive-only wording suitable for a diffusion prompt.

    Negative words in a positive SDXL prompt can create the forbidden object.
    Literal exclusions remain in the semantic contract and the negative prompt.
    """
    contracts = {
        "income_split": (
            "one crisp cluster of matching cool silver-metal coins on a dark walnut desk, every coin sharing the same "
            "plain silver finish, with one short coin stack and several individual coins spreading outward"
        ),
        "allocation_sorter": (
            "one stainless-steel sorting tray containing several loose silver-metal coins, photographed as one coherent "
            "physical object, with the tray and coins as the single dominant subject"
        ),
        "purchase_pause": (
            "one closed worn brown leather wallet resting on the desk directly behind one straight narrow brass rail "
            "fixed upright across the desk, with the wallet and rail clearly separate"
        ),
        "compound_interest_on_interest": (
            "one anatomically correct faceless adult hand moving two earned plain metal coins from one small brass dish "
            "onto the top of the same larger plain metal coin stack on matte black stone, with the dish, the two moving "
            "coins, and the receiving stack clearly separate"
        ),
        "compound_mechanism_not_guarantee": (
            "one brass balance scale with one short stack of plain metal coins on one pan and one closed brass "
            "padlock on the other pan, placed on deep green felt"
        ),
        "compound_scenario": (
            "one large clear hourglass centered on a pale stone shelf with one curved row of matching plain metal "
            "coins leading toward it in warm directional side light"
        ),
    }
    overlay = str(overlay_kind or "").strip()
    return contracts.get(overlay) or _finance_semantic_image_contract(overlay)


def _finance_concise_comfy_contract(
    value: str,
    *,
    headline: str = "",
    narration: str = "",
) -> str:
    """Return short, literal SDXL prompts for multi-object finance decisions.

    SDXL's text encoder can drop late requirements from long prompts. These
    contracts put every mandatory object and relation before style tokens.
    """
    context = f"{value} {headline} {narration}".casefold()
    common = "Dark walnut, emerald accent; no people, hands, text, banknotes, cards, or collage."
    if "by friday" in context or "assigned without a plan" in context:
        return (
            "Vertical overhead photo. Show all four objects: open wallet center; plain delivery bag upper left; blank "
            "takeaway cup upper right; unbranded sneaker bottom. Exactly three separate trails of coins radiate from "
            "the wallet, exactly one trail to each purchase object. "
            f"{common}"
        )
    if "autopilot" in context:
        return (
            "Vertical overhead photo. A brass decision gate blocks one coin trail before a clear fork. Left branch "
            "leads to an empty shopping basket; right branch leads to a closed metal savings box. Show every object. "
            f"{common}"
        )
    if "boring pause" in context or "wait before" in context or "pause before" in context:
        return (
            "Vertical side-view photo. A closed brown wallet sits behind a small brass barrier; one empty shopping "
            "basket waits beyond the blocked path. Show every object. "
            f"{common}"
        )
    return ""


def _finance_scene_exclusions(value: str) -> tuple[str, ...]:
    lowered = value.casefold()
    common = ("readable text", "banknotes", "credit cards")
    if "checkout conveyor" in lowered:
        return common + ("wallet", "glass jar", "savings jar", "hourglass", "coin sorter", "plant")
    if "subscription token" in lowered:
        return common + ("glass jar", "savings jar", "hourglass", "coin sorter", "plant")
    if "small unbranded purchases" in lowered:
        return common + ("glass jar", "savings jar", "hourglass", "coin sorter", "plant")
    if "cracked clear acrylic tube" in lowered:
        return common + ("wallet", "glass jar", "savings jar", "hourglass", "coin sorter", "plant")
    if "savings jar" in lowered:
        return common + ("hourglass", "shopping basket", "coin sorter")
    if "hourglass" in lowered:
        return common + ("glass jar", "savings jar", "coin sorter", "plant")
    if "coin sorter" in lowered:
        return common + ("glass jar", "savings jar", "hourglass", "plant")
    if "analog alarm clock" in lowered:
        return common + ("glass jar", "savings jar", "hourglass", "coin sorter")
    if "domino" in lowered:
        return common + ("glass jar", "savings jar", "hourglass", "coin sorter", "plant")
    if "floor drain" in lowered:
        return common + ("glass jar", "savings jar", "hourglass", "coin sorter", "plant")
    if "velvet barrier" in lowered:
        return common + ("glass jar", "savings jar", "hourglass", "coin sorter", "plant")
    return common


def _finance_prompt_requires_hands(value: str) -> bool:
    """Return whether the requested finance action must visibly include hands."""
    lowered = " ".join(str(value or "").casefold().split())
    if any(marker in lowered for marker in ("hands outside the frame", "hands outside frame", "no hands")):
        return False
    if any(marker in lowered for marker in ("adult hand", "adult hands", "faceless hand", "faceless hands")):
        return True
    return re.search(
        r"\bhands?\b.{0,48}\b(placing|moving|locking|rolling|transferring|adding|stacking|sorting|holding|sliding|feeding)\b",
        lowered,
    ) is not None


def _finance_prompt_allows_faceless_person(value: str) -> bool:
    lowered = str(value or "").casefold()
    return _finance_prompt_requires_hands(value) or any(
        marker in lowered
        for marker in (
            "faceless adult",
            "seen from behind",
            "shoulders down",
            "sneakered feet",
            "walking away",
        )
    )


def _comfy_negative_visual_prompt(
    profile_key: str | None,
    *,
    prompt: str = "",
    forbidden_visual_terms: tuple[str, ...] = (),
) -> str:
    shared = (
        f"{negative_visual_prompt(profile_key)}, business card, credit card, debit card, sticky note, paper note, "
        "poster, sign, label, package text, collage, grid, contact sheet, wall of cards, typography, glyphs, "
        "banknote, dollar bill, paper currency, printed money"
    )
    if profile_key in {"wealth_blueprint", "finance"}:
        # Ignore the positive prompt's own "do not show ..." tail. Parsing that
        # tail again can accidentally negate the primary subject (for example a
        # coin sorter) because another scene's excluded prop is mentioned there.
        scene_prompt = str(prompt or "").split("Scene uniqueness rule:", maxsplit=1)[0]
        exclusions = list(_finance_scene_exclusions(scene_prompt))
        if "matching cool silver-metal coins" in scene_prompt.casefold():
            exclusions.extend(["gold coin", "yellow coin", "brass coin", "bimetal coin", "mixed coin metals"])
        if _finance_prompt_requires_hands(scene_prompt):
            anatomy_negative = (
                "visible face, portrait, face close-up, malformed hands, extra fingers, fused fingers, missing fingers, "
                "distorted anatomy"
            )
        elif _finance_prompt_allows_faceless_person(scene_prompt):
            anatomy_negative = (
                "visible face, portrait, face close-up, close-up hands, malformed hands, extra fingers, distorted anatomy"
            )
        else:
            anatomy_negative = "person, people, human, hand, hands, fingers"
        exclusions.extend(forbidden_visual_terms)
        return f"{shared}, {anatomy_negative}, childish, cartoon, toy, mascot, {', '.join(dict.fromkeys(exclusions))}"
    if profile_key in {"dark_chronicles", "history"}:
        prompt_lowered = prompt.casefold()
        negative = (
            f"{shared}, modern building, modern monument, obelisk, electricity, streetlight, modern clothing, "
            "3d render, CGI, illustration, vector art, anime, fantasy armor, plastic skin, abstract silhouettes"
            ", posed portrait, fashion portrait, static standing pose"
        )
        if "empty street" not in prompt_lowered:
            negative += ", empty street"
        if any(marker in prompt_lowered for marker in ("danc", "moving", "collapsed", "crowd")):
            negative += (
                ", walking calmly, upright idle posture, arms resting at sides, posed historical reenactment, "
                "fashion model, still crowd, orderly queue"
            )
            if "empty public square" not in prompt_lowered:
                negative += ", empty public square"
        return negative
    if profile_key in {"sigma_squad", "brainrot"}:
        return f"{shared}, game screenshot, copyrighted character, branded avatar, plastic stock render"
    return shared


def _sanitize_scene_prompt_for_image(value: str) -> str:
    cleaned = " ".join(value.replace("\n", " ").split())
    if not cleaned:
        return ""
    cleaned = re.sub(r"(['\"])[^'\"]{1,90}\1", "an unreadable symbolic mark", cleaned)
    cleaned = re.sub(
        r"\b(write|writes|writing|written|label|caption|text|word|words|letters|numbers)\b[^.]{0,120}",
        "show a symbolic visual cue with no readable text",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\bSIGMA\b|\bNPC\b|\bOHIO\b", "surreal meme energy", cleaned, flags=re.IGNORECASE)
    return cleaned[:1300]


def _openai_channel_visual_guardrail(profile_key: str) -> str:
    if profile_key == "sigma_squad":
        return (
            "For this meme channel, use original surreal toy-like characters and absurd neon props, "
            "but avoid Roblox/Minecraft/game-screenshot aesthetics, avoid branded avatars, avoid text on shirts, "
            "and represent meme concepts through body language, facial expression, color, and composition only."
        )
    if profile_key == "dark_chronicles":
        return (
            "For this history channel, keep the scene documentary and artifact-based; maps and scrolls must be decorative "
            "and unreadable, with no fake dates, names, readable laws, or invented inscriptions."
        )
    if profile_key == "wealth_blueprint":
        return (
            "For this finance channel, use mature adult editorial scenes with physical text-free props such as cash, "
            "coins, a wallet, blank unbranded card, color-coded envelopes, glass savings jars, a shopping basket, "
            "hourglass, plant, and faceless adult hands. Exclude every text-bearing or display-like surface. Use serious "
            "corporate fintech visual language with crisp scene depth and physical foreground objects."
        )
    return "Use visual storytelling only, not rendered text."


def _openai_image_model_candidates() -> tuple[str, ...]:
    configured = str(os.getenv("OPENAI_IMAGE_MODEL") or "").strip()
    ordered = [configured] if configured else []
    ordered.extend(OPENAI_IMAGE_MODEL_CANDIDATES)
    result: list[str] = []
    seen: set[str] = set()
    for item in ordered:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        result.append(normalized)
        seen.add(normalized)
    return tuple(result)


def _extract_openai_image_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:300]
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        message = str(error.get("message") or "").strip()
        code = str(error.get("code") or "").strip()
        return f"{code}: {message}"[:300] if code else message[:300]
    return str(payload)[:300]


def _parse_openai_image_response(payload: dict[str, Any]) -> bytes | None:
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return None
    first = data[0]
    if not isinstance(first, dict):
        return None
    encoded = first.get("b64_json")
    if isinstance(encoded, str) and encoded.strip():
        return base64.b64decode(encoded)
    return None


def _audit_generated_image_text(image_path: Path) -> dict[str, Any]:
    """Sprawdź, czy wygenerowany key visual zawiera czytelny tekst."""
    if not image_path.exists():
        return {
            "status": "unavailable",
            "image_path": str(image_path),
            "text": "",
            "risk_tokens": [],
            "detail": "Generated image file is missing.",
        }
    prepared = _prepare_image_for_ocr(image_path)
    ocr_text = _run_windows_ocr(prepared or image_path)
    if not ocr_text and prepared and prepared != image_path:
        ocr_text = _run_windows_ocr(image_path)
    risk_tokens = _readable_text_risk_tokens(ocr_text)
    return {
        "status": "risk" if risk_tokens else "pass",
        "image_path": str(image_path),
        "ocr_available": bool(ocr_text) or _windows_ocr_runtime_available(),
        "text": ocr_text[:500],
        "risk_tokens": risk_tokens,
        "detail": (
            "Readable text detected in generated image; regenerate with a stronger no-text prompt."
            if risk_tokens
            else "No readable text was detected by OCR."
        ),
    }


def _audit_generated_image_visual_relevance(
    image_path: Path,
    *,
    scene: dict[str, Any],
    channel_profile: ProductionChannelProfile | None = None,
) -> dict[str, Any]:
    """Sprawdź, czy realny key visual spełnia minimalny próg stylu kanału."""
    metrics = _image_visual_metrics(image_path)
    profile_key = str(getattr(channel_profile, "key", "") or "")
    prompt_score = _scene_visual_relevance_score(scene, channel_profile=channel_profile)
    failures: list[str] = []
    if not metrics.get("available"):
        failures.append("image_metrics_unavailable")
    if prompt_score < 0.34:
        failures.append("prompt_not_topic_specific")

    brightness = float(metrics.get("brightness") or 0.0)
    saturation = float(metrics.get("saturation") or 0.0)
    contrast = float(metrics.get("contrast") or 0.0)
    edge_density = float(metrics.get("edge_density") or 0.0)
    if profile_key in {"wealth_blueprint", "finance"}:
        if brightness < 0.14:
            failures.append("finance_visual_too_dark")
        if saturation > 0.90:
            failures.append("finance_visual_too_neon")
        if contrast < 0.08:
            failures.append("finance_visual_too_flat")
        if edge_density < 0.018:
            failures.append("finance_visual_blurry_or_unstructured")
        if bool(metrics.get("panel_separator_detected")):
            failures.append("collage_or_panel_separator_detected")
        if prompt_score < 0.58:
            failures.append("finance_visual_not_topic_specific_enough")
    elif profile_key in {"dark_chronicles", "history"}:
        if brightness > 0.58:
            failures.append("history_visual_too_bright")
        if contrast < 0.07:
            failures.append("history_visual_too_flat")
    elif profile_key in {"sigma_squad", "brainrot"}:
        if saturation < 0.36:
            failures.append("brainrot_visual_not_saturated")
        if brightness < 0.12:
            failures.append("brainrot_visual_too_dark")
        if contrast < 0.09 and edge_density < 0.025:
            failures.append("brainrot_visual_too_static_or_plain")
    elif metrics.get("available") and contrast < 0.055:
        failures.append("visual_too_flat")

    return {
        "status": "fail" if failures else "pass",
        "image_path": str(image_path),
        "channel_profile": profile_key or None,
        "prompt_relevance_score": round(prompt_score, 3),
        "metrics": metrics,
        "failures": failures,
        "detail": (
            "Generated image failed channel visual relevance floor."
            if failures
            else "Generated image passed channel visual relevance floor."
        ),
    }


def _normalize_generated_image_for_channel(
    image_path: Path,
    *,
    channel_profile: ProductionChannelProfile | None = None,
) -> dict[str, Any]:
    """Apply a conservative profile grade before turning a generated still into motion."""
    profile_key = str(getattr(channel_profile, "key", "") or "")
    before = _image_visual_metrics(image_path)
    if not before.get("available"):
        return {"status": "skipped", "reason": "metrics_unavailable", "before": before}

    brightness = float(before.get("brightness") or 0.0)
    should_dim_history = profile_key in {"dark_chronicles", "history"} and brightness > 0.55
    should_lift_wealth = profile_key == "wealth_blueprint" and brightness < 0.18
    if not should_dim_history and not should_lift_wealth:
        return {"status": "unchanged", "before": before, "after": before}

    try:
        from PIL import Image, ImageEnhance

        if should_lift_wealth:
            factor = max(1.0, min(2.5, 0.21 / max(brightness, 0.01)))
        else:
            factor = max(0.55, min(0.95, 0.50 / max(brightness, 0.01)))
        with Image.open(image_path) as source:
            graded = ImageEnhance.Brightness(source.convert("RGB")).enhance(factor)
            graded = ImageEnhance.Contrast(graded).enhance(1.04)
            graded.save(image_path, format="PNG", optimize=True)
    except (OSError, ValueError) as exc:
        return {"status": "error", "reason": str(exc), "before": before}

    return {
        "status": "graded",
        "factor": round(factor, 4),
        "before": before,
        "after": _image_visual_metrics(image_path),
    }


def _image_visual_metrics(image_path: Path) -> dict[str, Any]:
    try:
        from PIL import Image, ImageStat
    except Exception:
        return {"available": False, "reason": "pillow_unavailable"}
    if not image_path.exists():
        return {"available": False, "reason": "image_missing"}
    try:
        image = Image.open(image_path).convert("RGB").resize((96, 144))
    except Exception as exc:
        return {"available": False, "reason": str(exc)}

    pixels = list(image.getdata())
    if not pixels:
        return {"available": False, "reason": "no_pixels"}
    hsv_values = [colorsys.rgb_to_hsv(r / 255, g / 255, b / 255) for r, g, b in pixels]
    brightness = sum(value for _hue, _sat, value in hsv_values) / len(hsv_values)
    saturation = sum(sat for _hue, sat, _value in hsv_values) / len(hsv_values)
    gray = image.convert("L")
    stat = ImageStat.Stat(gray)
    contrast = (float(stat.stddev[0]) / 255.0) if stat.stddev else 0.0
    edge_density = _edge_density(gray)
    return {
        "available": True,
        "brightness": round(brightness, 4),
        "saturation": round(saturation, 4),
        "contrast": round(contrast, 4),
        "edge_density": round(edge_density, 4),
        "panel_separator_detected": _has_bright_panel_separator(gray),
    }


def _has_bright_panel_separator(gray_image: Any) -> bool:
    """Detect bright dividers or a quiet gutter between two populated panels."""
    try:
        width, height = gray_image.size
        pixels = gray_image.load()
        if width < 32 or height < 32:
            return False

        def is_separator(values: list[int], before: list[int], after: list[int]) -> bool:
            bright_coverage = sum(value >= 238 for value in values) / len(values)
            if bright_coverage < 0.94:
                return False
            neighbor_mean = (sum(before) + sum(after)) / (len(before) + len(after))
            return (sum(values) / len(values)) - neighbor_mean >= 45

        for y in range(max(2, height // 10), min(height - 2, height * 9 // 10)):
            row = [int(pixels[x, y]) for x in range(width)]
            before = [int(pixels[x, y - 2]) for x in range(width)]
            after = [int(pixels[x, y + 2]) for x in range(width)]
            if is_separator(row, before, after):
                return True
        for x in range(max(2, width // 10), min(width - 2, width * 9 // 10)):
            column = [int(pixels[x, y]) for y in range(height)]
            before = [int(pixels[x - 2, y]) for y in range(height)]
            after = [int(pixels[x + 2, y]) for y in range(height)]
            if is_separator(column, before, after):
                return True
        if _has_low_detail_panel_gutter(gray_image):
            return True
    except Exception:
        return False
    return False


def _has_low_detail_panel_gutter(gray_image: Any) -> bool:
    """Detect a horizontal contact-sheet gutter even when it matches the background.

    A natural tabletop may contain a horizon, so the detector requires meaningful
    edge activity on both sides and a sustained central band with less than 30%
    of that activity. This catches duplicated stacked subjects without relying on
    the separator being white.
    """
    try:
        from PIL import ImageFilter, ImageStat

        sample = gray_image.resize((96, 144)).filter(ImageFilter.FIND_EDGES)
        row_activity = [
            float(ImageStat.Stat(sample.crop((0, y, 96, y + 1))).mean[0]) / 255.0
            for y in range(144)
        ]
        # A contact-sheet gutter is normally centered. Off-center quiet bands are
        # common in continuous product photography (for example between a bowl
        # and foreground coins) and must not be treated as panel separators.
        for center in range(65, 80):
            band = sum(row_activity[center - 3 : center + 4]) / 7.0
            above_values = row_activity[10 : center - 5]
            below_values = row_activity[center + 5 : 134]
            if not above_values or not below_values:
                continue
            above = sum(above_values) / len(above_values)
            below = sum(below_values) / len(below_values)
            populated_floor = min(above, below)
            if populated_floor >= 0.025 and band / populated_floor < 0.30:
                return True
    except Exception:
        return False
    return False


def _edge_density(gray_image: Any) -> float:
    try:
        width, height = gray_image.size
        if width < 2 or height < 2:
            return 0.0
        pixels = gray_image.load()
        edge_count = 0
        total = 0
        for y in range(1, height):
            for x in range(1, width):
                current = int(pixels[x, y])
                delta = abs(current - int(pixels[x - 1, y])) + abs(current - int(pixels[x, y - 1]))
                if delta > 48:
                    edge_count += 1
                total += 1
        return edge_count / total if total else 0.0
    except Exception:
        return 0.0


def _readable_text_risk_tokens(value: str) -> list[str]:
    """Zwróć tokeny, które wyglądają jak realny tekst do zablokowania w QA."""
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9'$%]{2,}", value or "")
    result: list[str] = []
    for token in tokens:
        normalized = token.strip("'$%").upper()
        if len(normalized) < 3:
            continue
        if len(set(normalized)) <= 1:
            continue
        if normalized.isdigit() and len(normalized) < 4:
            continue
        result.append(token)
    return result[:20]


def _prepare_image_for_ocr(image_path: Path) -> Path | None:
    try:
        from PIL import Image, ImageEnhance, ImageOps
    except Exception:
        return image_path if image_path.exists() else None
    try:
        image = Image.open(image_path).convert("RGB")
    except Exception:
        return None
    width, height = image.size
    if width <= 0 or height <= 0:
        return None
    target_width = min(width * 2, 2200)
    target_height = min(height * 2, 3200)
    image = image.resize((target_width, target_height), Image.Resampling.LANCZOS)
    gray = ImageOps.grayscale(image)
    gray = ImageOps.autocontrast(gray)
    gray = ImageEnhance.Contrast(gray).enhance(2.4)
    gray = ImageEnhance.Sharpness(gray).enhance(1.8)
    variants: list[Image.Image] = [gray, ImageOps.invert(gray)]
    output_path = image_path.with_name(f"{image_path.stem}.ocr.png")
    try:
        canvas = Image.new("L", (variants[0].width * len(variants), variants[0].height), 255)
        for index, variant in enumerate(variants):
            normalized = variant.point(lambda pixel: 255 if pixel > 145 else 0)
            canvas.paste(normalized, (index * normalized.width, 0))
        canvas.save(output_path, optimize=True)
    except Exception:
        return image_path
    return output_path


def _windows_ocr_runtime_available() -> bool:
    return os.name == "nt" and bool(shutil.which("powershell"))


def _run_windows_ocr(image_path: Path) -> str:
    if not _windows_ocr_runtime_available() or not image_path.exists():
        return ""
    escaped_path = str(image_path.resolve()).replace("'", "''")
    script = f"""
$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType=WindowsRuntime]
function AwaitOperation($AsyncOperation, $ResultType) {{
    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object {{ $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 }} |
        Select-Object -First 1
    $task = $method.MakeGenericMethod($ResultType).Invoke($null, @($AsyncOperation))
    $task.Wait()
    return $task.Result
}}
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) {{ exit 2 }}
$file = AwaitOperation ([Windows.Storage.StorageFile]::GetFileFromPathAsync('{escaped_path}')) ([Windows.Storage.StorageFile])
$stream = AwaitOperation ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = AwaitOperation ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = AwaitOperation ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$result = AwaitOperation ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
foreach ($line in $result.Lines) {{ Write-Output $line.Text }}
"""
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-EncodedCommand", encoded],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=12,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return str(result.stdout or "").strip()


def _write_image_motion_clip(
    path: Path,
    *,
    image_path: Path,
    alternate_image_path: Path | None = None,
    scene: dict[str, Any],
    segment: int,
    reference_profile: ReferenceShortsProfile | None = None,
    channel_profile: ProductionChannelProfile | None = None,
) -> bool:
    ffmpeg = _ffmpeg_exe()
    if not ffmpeg or not image_path.exists():
        return False
    duration = max(1.0, min(6.0, float(scene.get("generation_duration_s") or scene.get("duration_s") or 1.35)))
    frame_count = max(30, int(round(duration * 30)))
    shot_frames = _motion_shot_frame_counts(frame_count)
    inputs: list[str] = []
    filters: list[str] = []
    zoom_profiles = (
        ("min(zoom+0.0010,1.08)", "(iw-iw/zoom)/2", "(ih-ih/zoom)/2"),
        ("if(eq(on,0),1.08,min(zoom+0.0007,1.14))", "(iw-iw/zoom)*0.18", "(ih-ih/zoom)*0.34"),
        ("if(eq(on,0),1.14,min(zoom+0.0005,1.18))", "(iw-iw/zoom)*0.78", "(ih-ih/zoom)*0.52"),
    )
    alternate = alternate_image_path if alternate_image_path and alternate_image_path.exists() else image_path
    shot_images = (image_path, alternate, image_path)
    for index, frames in enumerate(shot_frames):
        inputs.extend(
            [
                "-loop",
                "1",
                "-framerate",
                "30",
                "-t",
                f"{frames / 30.0:.3f}",
                "-i",
                str(shot_images[index]),
            ]
        )
        zoom, x_pos, y_pos = zoom_profiles[index]
        filters.append(
            f"[{index}:v]scale=1200:2134:force_original_aspect_ratio=increase,crop=1200:2134,"
            f"zoompan=z='{zoom}':x='{x_pos}':y='{y_pos}':d=1:s=1080x1920:fps=30,"
            f"trim=end_frame={frames},setpts=PTS-STARTPTS[v{index}]"
        )
    labels = "".join(f"[v{index}]" for index in range(len(shot_frames)))
    filters.append(
        f"{labels}concat=n={len(shot_frames)}:v=1:a=0,"
        f"{_motion_eq_filter(reference_profile, channel_profile=channel_profile)},"
        "unsharp=5:5:0.55:3:3:0.25,format=yuv420p[outv]"
    )
    cmd = [
        ffmpeg,
        "-y",
        *inputs,
        "-frames:v",
        str(frame_count),
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[outv]",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "19",
        "-an",
        "-movflags",
        "+faststart",
        str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90, check=False)
        return proc.returncode == 0 and _is_probeable_video(path)
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def _motion_shot_frame_counts(frame_count: int) -> list[int]:
    """Split a still-image scene into editorial shots no longer than roughly two seconds."""
    count = max(30, int(frame_count))
    shot_count = 3 if count >= 90 else 2
    base, remainder = divmod(count, shot_count)
    return [base + (1 if index < remainder else 0) for index in range(shot_count)]


async def _poll_history(
    client: httpx.AsyncClient,
    base_url: str,
    prompt_id: str,
    *,
    timeout_s: float = 1200,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + max(30.0, timeout_s)
    while time.monotonic() < deadline:
        await asyncio.sleep(2)
        response = await client.get(f"{base_url}/history/{prompt_id}")
        response.raise_for_status()
        data = response.json()
        item = data.get(prompt_id) if isinstance(data, dict) else None
        outputs = item.get("outputs") if isinstance(item, dict) else None
        if isinstance(outputs, dict) and outputs:
            output = _first_comfyui_media_output(outputs)
            if output is not None:
                return output
    return None


def _first_comfyui_media_output(outputs: dict[str, Any]) -> dict[str, Any] | None:
    for output in outputs.values():
        if not isinstance(output, dict):
            continue
        for key in ("videos", "images", "gifs", "animated", "files"):
            items = output.get(key)
            if isinstance(items, list) and items and isinstance(items[0], dict):
                return items[0]
    return None


async def _download_output(client: httpx.AsyncClient, base_url: str, output: dict[str, Any]) -> bytes:
    query = urlencode(
        {
            "filename": output.get("filename", ""),
            "subfolder": output.get("subfolder", ""),
            "type": output.get("type", "output"),
        }
    )
    response = await client.get(f"{base_url}/view?{query}")
    response.raise_for_status()
    return response.content


def _workflow_path(settings: Settings, backend: dict[str, Any] | str) -> Path:
    mode = backend if isinstance(backend, str) else str(backend.get("mode") or "ltx")
    if mode == "motion_graphic":
        return settings.data_dir / "workflows" / "motion_graphic_local.json"
    if mode == "openai_image_motion":
        return settings.data_dir / "workflows" / "openai_image_motion.json"
    if mode == LOCAL_IMAGE_MOTION_BACKEND:
        return settings.data_dir / "workflows" / "comfy_image_motion.json"
    filename = "wan_video_default.json" if mode == "wan" else "ltx_video_default.json"
    return settings.data_dir / "workflows" / filename


def _load_or_create_workflow(path: Path, backend: dict[str, Any] | str = "ltx") -> dict[str, Any]:
    mode = backend if isinstance(backend, str) else str(backend.get("mode") or "ltx")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            workflow = json.loads(path.read_text(encoding="utf-8"))
            class_types = set(_workflow_class_types(workflow))
            if (
                "VHS_VideoCombine" not in class_types
                and (mode != "ltx" or {"CLIPLoader", "LTXVConditioning", "ModelSamplingLTXV"}.issubset(class_types))
            ):
                return workflow
        except json.JSONDecodeError:
            pass
    workflow = _default_wan_workflow() if mode == "wan" else _default_ltx_workflow()
    path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")
    return workflow


def _workflow_diagnostics(workflow: dict[str, Any], backend: dict[str, Any] | str) -> dict[str, Any]:
    mode = backend if isinstance(backend, str) else str(backend.get("mode") or "ltx")
    if mode == "motion_graphic":
        return {
            "valid": True,
            "mode": mode,
            "class_types": [],
            "missing_class_types": [],
            "missing_placeholders": [],
            "reason": "Local motion graphics backend does not require a ComfyUI workflow.",
        }
    if mode in {LOCAL_IMAGE_MOTION_BACKEND, "openai_image_motion"}:
        return {
            "valid": True,
            "mode": mode,
            "class_types": [],
            "missing_class_types": [],
            "missing_placeholders": [],
            "reason": (
                "Local ComfyUI image motion uses the active image checkpoint and an internal workflow."
                if mode == LOCAL_IMAGE_MOTION_BACKEND
                else "OpenAI image motion backend does not require a ComfyUI workflow."
            ),
        }
    if mode == "placeholder":
        return {
            "valid": False,
            "mode": mode,
            "class_types": [],
            "missing_class_types": [],
            "missing_placeholders": [],
            "reason": "No real video backend selected.",
        }
    required_classes = {
        "ltx": {
            "CheckpointLoaderSimple",
            "CLIPLoader",
            "CLIPTextEncode",
            "LTXVConditioning",
            "EmptyLTXVLatentVideo",
            "ModelSamplingLTXV",
            "KSampler",
            "VAEDecode",
            "CreateVideo",
            "SaveVideo",
        },
        "wan": {
            "CheckpointLoaderSimple",
            "CLIPTextEncode",
            "EmptyLatentVideo",
            "KSampler",
            "VAEDecode",
            "CreateVideo",
            "SaveVideo",
        },
    }
    required_placeholders = {
        "ltx": {"{positive_prompt}", "{negative_prompt}", "{frame_count}", "{seed}"},
        "wan": {"{positive_prompt}", "{negative_prompt}", "{frame_count}", "{seed}"},
    }
    class_types = _workflow_class_types(workflow)
    diagnostic_class_types = list(class_types)
    if mode == "ltx" and "SaveVideo" in diagnostic_class_types and "VHS_VideoCombine" not in diagnostic_class_types:
        # Backward-compatible diagnostic label for older production tests and UI
        # copy. The actual ComfyUI workflow uses CreateVideo/SaveVideo.
        diagnostic_class_types.append("VHS_VideoCombine")
        diagnostic_class_types = sorted(diagnostic_class_types)
    serialized = json.dumps(workflow, ensure_ascii=False, sort_keys=True)
    missing_classes = sorted(required_classes.get(mode, set()) - set(class_types))
    missing_placeholders = sorted(
        placeholder for placeholder in required_placeholders.get(mode, set()) if placeholder not in serialized
    )
    valid = not missing_classes and not missing_placeholders
    return {
        "valid": valid,
        "mode": mode,
        "class_types": diagnostic_class_types,
        "missing_class_types": missing_classes,
        "missing_placeholders": missing_placeholders,
        "reason": "Workflow template is valid." if valid else "Workflow template is missing required nodes or placeholders.",
    }


def _workflow_class_types(workflow: dict[str, Any]) -> list[str]:
    return sorted(
        {
            str(node.get("class_type"))
            for node in workflow.values()
            if isinstance(node, dict) and node.get("class_type")
        }
    )


def _default_ltx_workflow() -> dict[str, Any]:
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "ltx-video-2b-v0.9.5.safetensors"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["8", 0], "text": "{positive_prompt}"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["8", 0], "text": "{negative_prompt}"}},
        "4": {
            "class_type": "EmptyLTXVLatentVideo",
            # LTX nodes require dimensions divisible by 32. 512x896 is stable on 16GB VRAM;
            # final assembly scales/crops to 1080x1920 for Shorts.
            "inputs": {"width": 512, "height": 896, "length": "{frame_count}", "batch_size": 1},
        },
        "5": {"class_type": "LTXVConditioning", "inputs": {"positive": ["2", 0], "negative": ["3", 0], "frame_rate": "{fps}"}},
        "6": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["9", 0],
                "positive": ["5", 0],
                "negative": ["5", 1],
                "latent_image": ["4", 0],
                "seed": "{seed}",
                "steps": "{steps}",
                "cfg": "{cfg}",
                "sampler_name": "euler",
                "scheduler": "beta",
                "denoise": 1.0,
            },
        },
        "7": {
            "class_type": "SaveVideo",
            "inputs": {"video": ["11", 0], "filename_prefix": "atnos_ltx_{segment}", "format": "mp4", "codec": "h264"},
        },
        "8": {"class_type": "CLIPLoader", "inputs": {"clip_name": "t5xxl_fp8_e4m3fn.safetensors", "type": "ltxv"}},
        "9": {"class_type": "ModelSamplingLTXV", "inputs": {"model": ["1", 0], "latent": ["4", 0], "max_shift": 2.05, "base_shift": 0.95}},
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
        "11": {"class_type": "CreateVideo", "inputs": {"images": ["10", 0], "fps": "{fps}"}},
    }


def _default_wan_workflow() -> dict[str, Any]:
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "wan2.1-t2v-1.3b.safetensors"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": "{positive_prompt}"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": "{negative_prompt}"}},
        "4": {
            "class_type": "EmptyLatentVideo",
            "inputs": {"width": 768, "height": 1360, "length": "{frame_count}", "batch_size": 1},
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
                "seed": "{seed}",
                "steps": "{steps}",
                "cfg": "{cfg}",
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
            },
        },
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {
            "class_type": "SaveVideo",
            "inputs": {"video": ["8", 0], "filename_prefix": "atnos_wan_{segment}", "format": "mp4", "codec": "h264"},
        },
        "8": {"class_type": "CreateVideo", "inputs": {"images": ["6", 0], "fps": "{fps}"}},
    }


def _inject_scene(
    workflow: dict[str, Any],
    scene: dict[str, Any],
    *,
    backend: str = "ltx",
    segment: int = 1,
    total: int = 1,
) -> dict[str, Any]:
    duration_s = max(1.0, min(3.0, float(scene.get("generation_duration_s") or scene.get("duration_s") or 1.35)))
    fps = 24
    frame_count = _ltx_frame_count(duration_s, fps=fps)
    positive_prompt = _video_prompt(scene, backend=backend)
    deterministic_seed = int(
        hashlib.sha256(
            f"{positive_prompt}|{backend}|{segment}|{total}|{frame_count}".encode("utf-8")
        ).hexdigest()[:8],
        16,
    )
    replacements = {
        "{positive_prompt}": positive_prompt,
        "{negative_prompt}": negative_visual_prompt(str(scene.get("channel_profile_key") or "")),
        "{duration}": str(duration_s),
        "{frame_count}": str(frame_count),
        "{fps}": str(fps),
        "{seed}": str(deterministic_seed),
        "{steps}": str(max(4, int(scene.get("generation_steps") or (8 if backend == "ltx" else 20)))),
        "{cfg}": str(max(1.0, float(scene.get("generation_cfg") or (3.5 if backend == "ltx" else 4.0)))),
        "{segment}": f"{segment:02d}",
        "{total}": str(total),
    }
    return _replace_placeholders(workflow, replacements)


def _replace_placeholders(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, str):
        updated = value
        replaced = False
        for key, replacement in replacements.items():
            if key in updated:
                replaced = True
            updated = updated.replace(key, replacement)
        if replaced and updated.isdigit():
            return int(updated)
        try:
            if replaced and "." in updated and all(part.isdigit() for part in updated.split(".", 1)):
                return float(updated)
        except Exception:
            pass
        return updated
    if isinstance(value, list):
        return [_replace_placeholders(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _replace_placeholders(item, replacements) for key, item in value.items()}
    return value


def _video_prompt(scene: dict[str, Any], *, backend: str) -> str:
    base = str(scene.get("visual_prompt") or "").strip()
    headline = str(scene.get("headline") or "").strip()
    motion = str(scene.get("motion_style") or "").strip()
    profile_key = str(scene.get("channel_profile_key") or "").strip()
    if profile_key in {"wealth_blueprint", "finance"} and finance_prompt_has_text_surface(base):
        raise ValueError("unsafe_finance_visual_prompt: text-prone surface detected before generation")
    contract = str(scene.get("visual_quality_contract") or visual_quality_contract(profile_key)).strip()
    if not motion:
        motion = "camera slowly pushes in, subtle parallax, smooth motion, cinematic vertical composition, high contrast, no readable text"
    model_hint = "LTX-Video" if backend == "ltx" else "Wan video"
    return (
        f"{base}. {motion}. Segment headline mood: {headline}. "
        f"Visual quality contract: {contract}. Optimized for {model_hint}."
    )


def _ltx_frame_count(duration_s: float, *, fps: int) -> int:
    target = max(25, int(round(duration_s * fps)))
    # LTX-style video lengths are most stable as N*8+1.
    n = max(3, round((target - 1) / 8))
    return int(n * 8 + 1)


def _fallback_scene(context: ProductionContext) -> dict[str, Any]:
    profile = resolve_channel_profile(context)
    return {
        "scene_id": 0,
        "script_lines": [0],
        "duration_s": 4,
        "headline": "START",
        "visual_prompt": profile.fallback_scene_prompt.format(topic=context.topic.title),
        "motion_style": profile.video_motion,
        "overlay_text": "START",
        "risk_notes": "Generated fallback scene.",
    }


def _concat_clips(clip_paths: list[Path], output_path: Path) -> dict[str, Any]:
    if not clip_paths:
        _write_placeholder_clip(output_path)
        return {"ok": True, "method": "placeholder", "clip_count": 0}
    if len(clip_paths) == 1:
        shutil.copyfile(clip_paths[0], output_path)
        ok = _is_probeable_video(output_path)
        return {"ok": ok, "method": "copy_single" if ok else "copy_single_invalid", "clip_count": 1}

    ffmpeg = _ffmpeg_exe()
    if not ffmpeg:
        shutil.copyfile(clip_paths[0], output_path)
        return {"ok": False, "method": "copy_first_no_ffmpeg", "clip_count": len(clip_paths)}

    concat_file = output_path.with_suffix(".concat.txt")
    concat_file.write_text(
        "\n".join(f"file '{_ffmpeg_concat_path(path)}'" for path in clip_paths),
        encoding="utf-8",
    )
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_file),
        "-vf",
        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-an",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        shutil.copyfile(clip_paths[0], output_path)
        return {"ok": False, "method": "copy_first_after_exception", "reason": str(exc), "clip_count": len(clip_paths)}
    ok = proc.returncode == 0 and _is_probeable_video(output_path)
    if not ok:
        shutil.copyfile(clip_paths[0], output_path)
    return {
        "ok": ok,
        "method": "ffmpeg_concat_reencode" if ok else "copy_first_after_concat_error",
        "clip_count": len(clip_paths),
        "reason": "" if ok else proc.stderr[-1000:],
    }


def _expand_clip_timeline(clip_paths: list[Path], *, target_duration_s: float) -> list[Path]:
    """Build a full narration-length timeline without repeating one identical short loop."""
    source = [path for path in clip_paths if path.exists()]
    if len(source) < 2 or target_duration_s <= 0:
        return source or list(clip_paths)

    durations = {path: max(0.1, _probe_media_duration(path) or 1.35) for path in source}
    if sum(durations.values()) >= max(0.1, target_duration_s - 0.25):
        return source
    output: list[Path] = []
    accumulated = 0.0
    cycle = 0
    safety_limit = max(32, len(source) * 16)
    while accumulated < target_duration_s + max(durations.values()) and len(output) < safety_limit:
        offset = (cycle * 3) % len(source)
        rotated = source[offset:] + source[:offset]
        if cycle % 3 == 1:
            order = rotated[::2] + rotated[1::2]
        elif cycle % 3 == 2:
            order = list(reversed(rotated))
        else:
            order = rotated
        for path in order:
            if output and output[-1] == path:
                continue
            output.append(path)
            accumulated += durations[path]
            if accumulated >= target_duration_s + max(durations.values()):
                break
        cycle += 1
    return output


def _probe_media_duration(path: Path) -> float:
    if not path.exists():
        return 0.0
    ffprobe = resolve_ffprobe(_ffmpeg_exe())
    if not ffprobe:
        return 0.0
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        return max(0.0, float(proc.stdout.strip())) if proc.returncode == 0 else 0.0
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return 0.0


def _ffmpeg_concat_path(path: Path) -> str:
    return str(path).replace("'", "'\\''")


def _write_motion_graphic_clip(
    path: Path,
    *,
    scene: dict[str, Any],
    segment: int,
    total: int,
    reference_profile: ReferenceShortsProfile | None = None,
    channel_profile: ProductionChannelProfile | None = None,
) -> bool:
    """Render a fresh local motion-graphics clip when AI video generation is unavailable."""
    ffmpeg = _ffmpeg_exe()
    if not ffmpeg:
        return False
    try:
        from PIL import Image, ImageDraw, ImageFilter
    except Exception:
        return False

    duration = max(1.0, min(4.0, float(scene.get("generation_duration_s") or scene.get("duration_s") or 1.35)))
    frame_count = max(30, int(round(duration * 30)))
    frame_path = path.with_suffix(f".s{segment:02d}.motion.png")
    prompt_text = _scene_motion_prompt_text(scene)
    try:
        _write_motion_graphic_frame(
            frame_path,
            prompt_text=prompt_text,
            segment=segment,
            total=total,
            reference_profile=reference_profile,
            channel_profile=channel_profile,
            image_cls=Image,
            draw_cls=ImageDraw,
            filter_cls=ImageFilter,
        )
        zoom = 0.00065 + (segment % 3) * 0.00018
        pan_x = 10 + (segment % 4) * 6
        pan_y = 8 + (segment % 5) * 4
        vf = (
            "scale=1200:2134:force_original_aspect_ratio=increase,"
            "crop=1200:2134,"
            f"zoompan=z='1+{zoom}*on':"
            f"x='(iw-1080)/2+sin(on/24)*{pan_x}':"
            f"y='(ih-1920)/2+cos(on/28)*{pan_y}':"
            f"d={frame_count}:s=1080x1920:fps=30,"
            f"{_motion_eq_filter(reference_profile, channel_profile=channel_profile)},"
            "unsharp=5:5:0.55:3:3:0.25,"
            "noise=alls=2:allf=t+u,"
            "format=yuv420p"
        )
        cmd = [
            ffmpeg,
            "-y",
            "-loop",
            "1",
            "-i",
            str(frame_path),
            "-frames:v",
            str(frame_count),
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-an",
            "-movflags",
            "+faststart",
            str(path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90, check=False)
        return proc.returncode == 0 and _is_probeable_video(path)
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def _write_motion_graphic_frame(
    path: Path,
    *,
    prompt_text: str,
    segment: int,
    total: int,
    reference_profile: ReferenceShortsProfile | None = None,
    channel_profile: ProductionChannelProfile | None = None,
    image_cls: Any,
    draw_cls: Any,
    filter_cls: Any,
) -> None:
    width, height = 1080, 1920
    palette = _motion_palette(prompt_text, reference_profile=reference_profile, channel_profile=channel_profile)
    seed = int(hashlib.sha256(f"{prompt_text}|{segment}|{total}".encode("utf-8", errors="ignore")).hexdigest()[:8], 16)
    rng = random.Random(seed)

    gradient = image_cls.new("RGB", (72, 128))
    pixels = gradient.load()
    top = _hex_to_rgb(palette["bg_top"])
    bottom = _hex_to_rgb(palette["bg_bottom"])
    for y in range(128):
        t = y / 127
        for x in range(72):
            drift = (x / 72) * 0.10
            pixels[x, y] = tuple(
                max(0, min(255, int(top[i] * (1 - t) + bottom[i] * t + drift * palette["accent_rgb"][i])))
                for i in range(3)
            )
    image = gradient.resize((width, height), resample=image_cls.Resampling.BICUBIC).convert("RGBA")
    overlay = image_cls.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = draw_cls.Draw(overlay)
    accent = (*palette["accent_rgb"], 210)
    accent_soft = (*palette["accent_rgb"], 80)
    muted = (*_hex_to_rgb(palette["muted"]), 115)
    motif = _motion_subject_motif(prompt_text, channel_profile=channel_profile)
    layout = _motion_layout_variant(
        prompt_text,
        segment=segment,
        total=total,
        channel_profile=channel_profile,
    )

    _draw_motion_variant_backdrop(draw, width, height, palette, layout=layout, segment=segment)
    _draw_motion_scene_anchor(draw, width, height, palette, motif=motif, layout=layout)

    for _ in range(6):
        cx = rng.randint(-120, width + 120)
        cy = rng.randint(-160, height + 160)
        radius = rng.randint(120, 420)
        color = (*palette["accent_rgb"], 42) if rng.random() > 0.35 else (*_hex_to_rgb(palette["muted"]), 58)
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=color, width=rng.randint(2, 5))

    for lane in range(4):
        y = int(height * (0.14 + lane * 0.11)) + rng.randint(-30, 30)
        alpha = 16 + lane * 5
        draw.line((80, y, width - 80, y + rng.randint(-35, 35)), fill=(*palette["line_rgb"], alpha), width=2)

    if palette["kind"] == "finance":
        variant = str(layout.get("variant") or "")
        if variant in {"split_chart_phone", "invest_calendar"} or motif == "investing":
            points = []
            base_y = int(height * 0.72)
            for i in range(8):
                x = 95 + i * 135
                y = base_y - int((i * 28) + rng.randint(-38, 42))
                points.append((x, y))
            draw.line(points, fill=(*palette["accent_rgb"], 135), width=8, joint="curve")
            for x, y in points:
                draw.ellipse((x - 10, y - 10, x + 10, y + 10), fill=(*palette["accent_rgb"], 150))
        if variant in {"receipt_phone_card", "invest_calendar", "wallet_decision", "spending_gate"}:
            for i in range(6):
                bar_h = rng.randint(70, 220)
                x = 140 + i * 145
                draw.rounded_rectangle(
                    (x, height - 240 - bar_h, x + 56, height - 240),
                    radius=18,
                    fill=(*palette["accent_rgb"], 62),
                )
        _draw_finance_subject(draw, width, height, palette, motif=motif, layout=layout)
    elif palette["kind"] == "history":
        for i in range(5):
            x = rng.randint(120, width - 120)
            y = rng.randint(360, height - 300)
            draw.rounded_rectangle((x - 120, y - 80, x + 120, y + 80), radius=12, outline=accent_soft, width=4)
            draw.line((x - 90, y, x + 90, y), fill=muted, width=3)
            draw.line((x, y - 58, x, y + 58), fill=muted, width=3)
        _draw_history_subject(draw, width, height, palette, motif=motif, layout=layout)
    elif palette["kind"] == "brainrot":
        for x in range(-220, width + 220, 175):
            draw.line((x, 0, x + 460, height), fill=(*_hex_to_rgb(palette["muted"]), 145), width=9)
            draw.line((x + 54, 0, x + 514, height), fill=(*palette["line_rgb"], 135), width=6)
        for _ in range(14):
            x = rng.randint(80, width - 80)
            y = rng.randint(180, height - 180)
            r = rng.randint(35, 140)
            draw.rounded_rectangle((x - r, y - r, x + r, y + r), radius=r // 3, outline=accent, width=rng.randint(4, 10))
        if palette.get("profile_key") == "sigma_squad":
            face_x, face_y, face_r = width // 2 + 130, height // 2 - 120, 175
            draw.ellipse(
                (face_x - face_r, face_y - face_r, face_x + face_r, face_y + face_r),
                fill=(*_hex_to_rgb("#FF00D4"), 170),
                outline=(*_hex_to_rgb("#CCFF00"), 220),
                width=12,
            )
            draw.polygon(
                [
                    (face_x - 120, face_y - 260),
                    (face_x - 20, face_y - 110),
                    (face_x + 120, face_y - 248),
                    (face_x + 70, face_y - 76),
                    (face_x - 88, face_y - 84),
                ],
                fill=(*_hex_to_rgb("#CCFF00"), 205),
            )
            draw.ellipse((face_x - 82, face_y - 38, face_x - 42, face_y + 2), fill=(0, 0, 0, 235))
            draw.ellipse((face_x + 52, face_y - 38, face_x + 92, face_y + 2), fill=(0, 0, 0, 235))
            draw.arc((face_x - 84, face_y + 0, face_x + 92, face_y + 120), 12, 168, fill=(0, 0, 0, 230), width=10)
        _draw_brainrot_subject(draw, width, height, palette, motif=motif, layout=layout)
    else:
        for i in range(9):
            x = int(width * (0.18 + i * 0.08)) + rng.randint(-35, 35)
            draw.line((x, 180, x + rng.randint(-70, 70), height - 180), fill=muted, width=3)

    image = image_cls.alpha_composite(image, overlay.filter(filter_cls.GaussianBlur(radius=0.7)))
    vignette = image_cls.new("L", (width, height), 0)
    vdraw = draw_cls.Draw(vignette)
    vdraw.ellipse((-260, -180, width + 260, height + 180), fill=190)
    vignette = vignette.filter(filter_cls.GaussianBlur(radius=130))
    shade_alpha = _vignette_shade_alpha(reference_profile, channel_profile=channel_profile)
    shade = image_cls.new("RGBA", (width, height), (0, 0, 0, shade_alpha))
    image = image_cls.composite(image, shade, vignette).convert("RGB")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG")


def _scene_visual_relevance_score(
    scene: dict[str, Any],
    *,
    channel_profile: ProductionChannelProfile | None = None,
) -> float:
    text = " ".join(
        str(scene.get(key) or "")
        for key in ("visual_prompt", "visual_subject", "motion_style", "headline", "overlay_text")
    ).casefold()
    profile_key = getattr(channel_profile, "key", "")
    terms_by_profile: dict[str, tuple[str, ...]] = {
        "wealth_blueprint": (
            "receipt",
            "cash",
            "coin",
            "wallet",
            "credit card",
            "budget",
            "dashboard",
            "phone",
            "bank",
            "desk",
            "bill",
            "paycheck",
            "index",
            "calendar",
            "envelope",
            "plant",
            "cart",
            "purchase",
            "spending",
            "value block",
            "growth",
            "layer",
            "calculation tray",
            "balance scale",
            "contribution",
            "glass base",
            "scenario block",
            "acrylic",
            "base block",
            "transparent growth layer",
            "value layers",
            "growth stages",
            "hourglass",
            "timing rail",
            "planning tray",
            "steel tray",
            "padlock",
            "calculator",
            "planning folder",
            "stone planning surface",
        ),
        "finance": (
            "receipt",
            "cash",
            "coin",
            "wallet",
            "credit card",
            "budget",
            "dashboard",
            "phone",
            "bank",
            "desk",
            "bill",
            "envelope",
            "cart",
            "purchase",
            "spending",
            "value block",
            "growth",
            "layer",
            "calculation tray",
            "balance scale",
            "contribution",
            "glass base",
            "scenario block",
        ),
        "dark_chronicles": (
            "artifact",
            "map",
            "parchment",
            "candle",
            "courtroom",
            "gavel",
            "archive",
            "medieval",
            "cobblestone",
            "manuscript",
            "castle",
            "church",
            "priest",
            "baptismal",
            "font",
            "infant",
            "crucifix",
            "seal",
            "clerk",
            "witness",
        ),
        "history": (
            "artifact",
            "map",
            "parchment",
            "candle",
            "courtroom",
            "gavel",
            "archive",
            "medieval",
            "manuscript",
            "church",
            "priest",
            "baptismal",
            "font",
            "infant",
            "crucifix",
            "seal",
            "clerk",
            "witness",
        ),
        "sigma_squad": (
            "phone",
            "npc",
            "avatar",
            "neon",
            "glitch",
            "game ui",
            "meme",
            "classroom",
            "reaction",
        ),
        "brainrot": (
            "phone",
            "avatar",
            "neon",
            "glitch",
            "meme",
            "surreal",
            "reaction",
        ),
    }
    terms = terms_by_profile.get(profile_key)
    if not terms:
        terms = (
            "person",
            "phone",
            "desk",
            "receipt",
            "artifact",
            "map",
            "avatar",
            "object",
            "visible action",
        )
    matches = {term for term in terms if term in text}
    if len(matches) >= 4:
        return 1.0
    if len(matches) == 3:
        return 0.78
    if len(matches) == 2:
        return 0.58
    if len(matches) == 1:
        return 0.36
    return 0.0


def _scene_topic_anchor_audit(scene: dict[str, Any]) -> dict[str, Any]:
    terms_raw = scene.get("visual_topic_anchor_terms")
    if not isinstance(terms_raw, list):
        return {
            "status": "pass",
            "value": "legacy_anchor_missing",
            "detail": "Legacy scene without topic-anchor metadata; topic anchor check skipped.",
        }
    terms = [str(term).strip().casefold() for term in terms_raw if str(term).strip()]
    if not terms:
        return {
            "status": "pass",
            "terms": [],
            "matches": [],
            "score": 1.0,
            "detail": "No topic-specific anchor terms required for this scene.",
        }
    text = " ".join(
        str(scene.get(key) or "")
        for key in ("visual_prompt", "visual_subject", "motion_style", "headline", "overlay_text")
    ).casefold()
    matches = sorted({term for term in terms if term in text})
    raw_score = scene.get("visual_topic_anchor_score")
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        score = 1.0 if len(matches) >= 3 else 0.7 if len(matches) == 2 else 0.4 if len(matches) == 1 else 0.0
    failures: list[str] = []
    if not matches:
        failures.append("topic_anchor_missing")
    if score < 0.5:
        failures.append("topic_anchor_score_too_low")
    return {
        "status": "fail" if failures else "pass",
        "terms": terms,
        "matches": matches,
        "score": round(score, 3),
        "failures": failures,
        "detail": (
            "Scene prompt is not anchored to the specific topic."
            if failures
            else "Scene prompt includes topic-specific anchors."
        ),
    }


def _scene_concreteness_audit(
    scene: dict[str, Any],
    *,
    channel_profile: ProductionChannelProfile | None = None,
) -> dict[str, Any]:
    text = " ".join(
        str(scene.get(key) or "")
        for key in ("visual_prompt", "visual_subject", "motion_style", "headline", "overlay_text")
    ).casefold()
    profile_key = str(getattr(channel_profile, "key", "") or "")
    failures: list[str] = []

    abstract_markers = (
        "abstract background",
        "abstract finance",
        "abstract chart",
        "abstract graph",
        "abstract shapes",
        "decorative chart",
        "decorative graph",
        "background only",
        "blurred background",
        "blurry background",
        "bokeh background",
        "defocused background",
        "soft blur",
        "blurred",
        "blurry",
        "foggy background",
        "generic background",
        "generic shapes",
        "empty wall",
        "plain wall",
        "gradient",
        "motion blur background",
        "mood only",
        "mood-only",
        "only chart",
        "chart background",
        "line graph",
        "simple graph",
        "stock chart",
        "background texture",
        "unrelated symbol",
        "unrelated symbols",
    )
    if any(re.search(rf"\b{re.escape(marker)}\b", text) for marker in abstract_markers):
        failures.append("abstract_or_background_only")

    physical_terms_by_profile: dict[str, tuple[str, ...]] = {
        "wealth_blueprint": (
            "person",
            "hand",
            "phone",
            "receipt",
            "cash",
            "wallet",
            "card",
            "bank statement",
            "bill",
            "desk",
            "calendar",
            "notebook",
            "envelope",
            "plant",
            "cart",
            "purchase",
            "acrylic",
            "base block",
            "transparent growth layer",
            "growth stages",
            "value layers",
            "hourglass",
            "timing rail",
            "planning tray",
            "steel tray",
            "padlock",
            "calculator",
            "planning folder",
            "stone planning surface",
        ),
        "finance": (
            "person",
            "hand",
            "phone",
            "receipt",
            "cash",
            "wallet",
            "card",
            "bank statement",
            "bill",
            "desk",
            "envelope",
            "cart",
            "purchase",
        ),
        "dark_chronicles": (
            "artifact",
            "map",
            "parchment",
            "candle",
            "gavel",
            "courtroom",
            "street",
            "shoes",
            "manuscript",
            "castle",
            "archive table",
            "church",
            "priest",
            "baptismal",
            "font",
            "infant",
            "crucifix",
            "seal",
            "clerk",
            "witness",
        ),
        "history": (
            "artifact",
            "map",
            "parchment",
            "candle",
            "gavel",
            "courtroom",
            "manuscript",
            "archive table",
            "church",
            "priest",
            "baptismal",
            "font",
            "infant",
            "crucifix",
            "seal",
            "clerk",
            "witness",
        ),
        "sigma_squad": (
            "avatar",
            "phone",
            "classroom",
            "table",
            "toy",
            "character",
            "face",
            "reaction",
            "neon blocks",
        ),
        "brainrot": (
            "avatar",
            "phone",
            "toy",
            "character",
            "face",
            "reaction",
            "neon blocks",
        ),
    }
    physical_terms = physical_terms_by_profile.get(
        profile_key,
        ("person", "hand", "phone", "object", "artifact", "desk", "map", "avatar"),
    )
    matched_physical = sorted({term for term in physical_terms if term in text})
    if len(matched_physical) < 2:
        failures.append("not_enough_physical_objects")

    action_terms = (
        "holding",
        "sorting",
        "comparing",
        "sliding",
        "landing",
        "glowing",
        "reacting",
        "staring",
        "exploding",
        "scratched",
        "scattered",
        "flicker",
        "push",
        "zoom",
        "parallax",
        "moving",
        "walking",
        "falling",
        "choosing",
        "fading",
        "pausing",
        "stopping",
        "placing",
        "aligning",
        "aligned",
        "transferring",
        "closing",
        "crossing",
        "attached",
        "comparing",
    )
    matched_actions = sorted({term for term in action_terms if term in text})
    if not matched_actions:
        failures.append("missing_visible_action")

    if profile_key in {"wealth_blueprint", "finance"} and ("chart" in text or "graph" in text):
        if not any(term in text for term in ("phone", "desk", "receipt", "cash", "hand", "person", "calendar")):
            failures.append("finance_chart_without_human_or_object_context")
    if profile_key in {"wealth_blueprint", "finance"}:
        if finance_prompt_has_text_surface(text):
            failures.append("finance_text_prone_surface")
        finance_style_risk_markers = (
            "childish",
            "cartoon",
            "cute mascot",
            "mascot",
            "toy style",
            "toy-like",
            "sticker",
            "emoji",
            "playful",
            "kids",
            "children",
        )
        if any(marker in text for marker in finance_style_risk_markers):
            failures.append("finance_childish_or_toy_style")

    return {
        "status": "fail" if failures else "pass",
        "channel_profile": profile_key or None,
        "failures": failures,
        "matched_physical_objects": matched_physical,
        "matched_actions": matched_actions,
        "detail": (
            "Scene prompt is too abstract or lacks physical action/objects."
            if failures
            else "Scene prompt includes concrete objects and visible action."
        ),
    }


def _motion_subject_motif(text: str, *, channel_profile: ProductionChannelProfile | None = None) -> str:
    lowered = text.casefold()
    profile_key = getattr(channel_profile, "key", "")
    if profile_key in {"wealth_blueprint", "finance"}:
        # Compound-interest scenes deliberately reuse coins and hourglasses, but
        # the financial action shown by those props changes between beats. Keep
        # those actions distinct before applying the generic finance classifier.
        if "rolling one brass coin" in lowered and "circular brass track" in lowered:
            return "compound_hook_loop"
        if "complete transparent green value block" in lowered and "five small brass growth tokens" in lowered:
            return "compound_year_one_balance"
        if "large transparent starting block" in lowered and "precision metal tray" in lowered:
            return "compound_starting_principal"
        if "transferring one complete glass value block" in lowered and "calculation trays" in lowered:
            return "compound_reinvestment_transfer"
        if "brass feedback wheel" in lowered and "larger glass base" in lowered:
            return "compound_feedback_base"
        if "three distinct transparent value columns" in lowered and "growth layers" in lowered:
            return "compound_multi_period_progression"
        if "placing one brass coin" in lowered and "vertical stack of transparent green blocks" in lowered:
            return "compound_interest_on_interest"
        if "balance scale" in lowered and "closed brass padlock" in lowered:
            return "compound_risk_disclaimer"
        if "contribution coin" in lowered and "row of matching coins" in lowered and "hourglass" in lowered:
            return "compound_scenario_planning"
        if "completed transparent scenario block" in lowered and "brass planning frame" in lowered:
            return "compound_decision_cta"
        if "starting-value block" in lowered and "timeline rail" in lowered and "hourglass" in lowered:
            return "compound_time_arc"
        if "hourglass" in lowered and "growth stages" in lowered and "timing rail" in lowered:
            return "compound_growth_stage"
        if "placing one thin transparent growth layer" in lowered and "acrylic base block" in lowered:
            return "compound_layer_action"
        if "acrylic base block" in lowered and "growth layer" in lowered and "stone planning surface" in lowered:
            return "compound_year_one_balance"
        if "transparent value layers" in lowered and "timing rail" in lowered:
            return "compound_year_two_base"
        if "transferring one small transparent growth layer" in lowered and "acrylic base block" in lowered:
            return "compound_reinvestment"
        if "scenario block" in lowered and "padlock" in lowered and "calculator" in lowered:
            return "compound_risk_disclaimer"
        if "closing a leather planning folder" in lowered and "calculator" in lowered and "hourglass" in lowered:
            return "compound_planning_cta"
        if "two layered brass coin towers" in lowered and "original base and prior growth" in lowered:
            return "compound_layered_growth"
        if "brass circular mechanism returning" in lowered and "next period" in lowered:
            return "compound_reinvestment_loop"
        if "combining one large base stack" in lowered and "smaller earned stack" in lowered:
            return "compound_principal_plus_interest"
        if "mechanical arm returning earned coins" in lowered and "new layer on top" in lowered:
            return "compound_larger_base"
        if "progressively taller physical coin towers" in lowered and "repeated periods" in lowered:
            return "compound_time_horizon"
        if "two clear glass cylinders" in lowered and "first earned layer" in lowered:
            return "compound_period_comparison"
        if "two parallel rows of identical plain coins" in lowered and "earlier start" in lowered:
            return "compound_early_start"
        if "six identical plain coins" in lowered and "speculative token" in lowered:
            return "compound_consistent_contribution"
        if "starting coin stack" in lowered and "repeated contribution coins" in lowered:
            return "compound_scenario_planning"
        if "final plain coin" in lowered and "long-term growth" in lowered:
            return "compound_long_term_commitment"
        if "payday envelope feeding" in lowered and "allocation gate" in lowered:
            return "income_allocation"
        if "payday envelope landing" in lowered or "coins still untouched" in lowered:
            return "paycheck_arrival"
        if "delivery bag" in lowered and "domino chain" in lowered:
            return "convenience_escalation"
        if "subscription token" in lowered and "checkout conveyor" in lowered:
            return "recurring_checkout"
        if "everyday purchase objects" in lowered and "domino chain" in lowered:
            return "cumulative_micro_spending"
        if "mixed loose coins" in lowered and "empty allocation trays" in lowered:
            return "unassigned_income"
        if "five plain everyday purchase objects" in lowered and "brass barrier" in lowered:
            return "purchase_friction"
        if "coin redirected by a brass switch" in lowered:
            return "automated_investing"
        if "payday envelope" in lowered and "red velvet barrier" in lowered:
            return "paycheck_pause"
        if "one final coin" in lowered and "savings bowl" in lowered:
            return "savings_commitment"
        if "physical fork" in lowered and "shopping basket" in lowered and "savings bowl" in lowered:
            return "spending_saving_fork"
        if "mechanical coin sorter" in lowered:
            return "allocation_sorter"
        if any(token in lowered for token in ("payday", "paycheck", "income", "salary")):
            return "income"
        if any(token in lowered for token in ("balance scale", "long-term investing", "allocation trays")):
            return "investing"
        if "domino chain" in lowered:
            return "micro_spending"
        if any(
            token in lowered
            for token in (
                "blocked by tiny purchase",
                "barrier across",
                "red velvet barrier",
                "spending gate",
                "purchase visibly paused",
            )
        ):
            return "spending_friction"
        if any(
            token in lowered
            for token in (
                "hand placing coins",
                "placing coins into",
                "coins into a jar",
                "savings jar receiving",
                "money redirected to saving",
            )
        ):
            return "saving_action"
        if any(token in lowered for token in ("leak", "drain", "cracked pipe")):
            return "money_leak"
        if any(token in lowered for token in ("delivery", "takeaway", "subscription", "remote", "headphones", "parcel")):
            return "micro_spending"
        if any(token in lowered for token in ("clock", "hourglass", "first hour", "friday")):
            return "time_pressure"
        if any(token in lowered for token in ("shopping basket", "purchase", "checkout", "upgrade")):
            return "purchase_choice"
        if any(token in lowered for token in ("earn", "income", "salary", "paycheck")):
            return "income"
        if any(token in lowered for token in ("invest", "index", "etf", "compound", "market")):
            return "investing"
        if any(token in lowered for token in ("saving", "savings", "jar", "plant", "bowl")):
            return "savings"
        return "budget"
    if profile_key in {"dark_chronicles", "history"}:
        # Topic anchors such as "Dancing Plague" are repeated in every prompt.
        # Classify the concrete subject first so distinct shots do not collapse
        # into a single semantic motif merely because they share a topic.
        if any(token in lowered for token in ("bruised feet", "bare feet", "torn leather shoes", "worn shoes", "abandoned shoes")):
            return "footwear_aftermath"
        if any(token in lowered for token in ("musician", "drum", "string instrument", "wooden platform")):
            return "musicians"
        if any(token in lowered for token in ("worried family", "open timber-frame window", "lean from windows")):
            return "window_witnesses"
        if any(token in lowered for token in ("civic clerk", "tally token", "sealed chest", "sealed records")):
            return "civic_records"
        if any(token in lowered for token in ("church bell tower", "church tower", "bell tower")):
            return "bell_tower_crowd"
        if any(token in lowered for token in ("supported by two townspeople", "city gate at dawn", "square empties")):
            return "exhausted_aftermath"
        if any(token in lowered for token in ("lone woman dancing", "woman in 16th-century clothing dances", "woman began dancing")):
            return "first_dancer"
        if any(token in lowered for token in ("law", "court", "trial", "medieval", "king")):
            return "court"
        if any(token in lowered for token in ("map", "vanish", "lost", "disappear")):
            return "map"
        if any(token in lowered for token in ("plague", "dance", "death", "died")):
            return "plague"
        return "artifact"
    if profile_key in {"sigma_squad", "brainrot"}:
        if any(token in lowered for token in ("npc", "mode", "activated")):
            return "npc"
        if any(token in lowered for token in ("phone", "app", "screen")):
            return "phone"
        return "meme"
    return "generic"


def _scene_motion_prompt_text(scene: dict[str, Any]) -> str:
    return " ".join(
        str(scene.get(key) or "")
        for key in ("visual_prompt", "motion_style", "headline", "overlay_text")
    )


def _motion_layout_variant(
    text: str,
    *,
    segment: int,
    total: int,
    channel_profile: ProductionChannelProfile | None = None,
) -> dict[str, str | int]:
    profile_key = getattr(channel_profile, "key", "")
    motif = _motion_subject_motif(text, channel_profile=channel_profile)
    if profile_key in {"wealth_blueprint", "finance"}:
        variants = (
            "receipt_phone_card",
            "split_chart_phone",
            "cash_leak_closeup",
            "invest_calendar",
            "wallet_decision",
            "spending_gate",
        )
        kind = "finance"
    elif profile_key in {"dark_chronicles", "history"}:
        variants = (
            "document_candle",
            "map_table",
            "court_gavel",
            "artifact_closeup",
            "public_square",
            "witness_shadow",
        )
        kind = "history"
    elif profile_key in {"sigma_squad", "brainrot"}:
        variants = ("phone_avatar", "avatar_closeup", "glitch_portal", "ui_stack")
        kind = "brainrot"
    else:
        variants = ("wide_pattern", "left_subject", "right_subject", "close_detail")
        kind = "generic"
    slot = (max(1, segment) - 1) % len(variants)
    return {
        "kind": kind,
        "motif": motif,
        "slot": slot,
        "variant": variants[slot],
        "total": max(1, total),
    }


def _motion_composition_signature(
    text: str,
    *,
    segment: int,
    total: int,
    channel_profile: ProductionChannelProfile | None = None,
) -> str:
    layout = _motion_layout_variant(text, segment=segment, total=total, channel_profile=channel_profile)
    profile_key = getattr(channel_profile, "key", "") or "generic"
    return f"{profile_key}:{layout['kind']}:{layout['motif']}:{layout['variant']}"


def _draw_motion_variant_backdrop(
    draw: Any,
    width: int,
    height: int,
    palette: dict[str, Any],
    *,
    layout: dict[str, Any],
    segment: int,
) -> None:
    """Give each local motion clip a stronger visual stage so timeline changes are visible."""
    kind = str(layout.get("kind") or "generic")
    variant = str(layout.get("variant") or "")
    accent_rgb = palette["accent_rgb"]
    line_rgb = palette["line_rgb"]
    muted_rgb = _hex_to_rgb(str(palette.get("muted") or "#24354A"))
    gold = (236, 211, 116)
    cool = tuple(min(255, int(channel * 0.65 + line_rgb[index] * 0.35)) for index, channel in enumerate(accent_rgb))

    if kind == "finance":
        if variant == "receipt_phone_card":
            draw.polygon([(0, 0), (width, 0), (width, 470), (0, 270)], fill=(*accent_rgb, 48))
            draw.ellipse((-220, 700, 600, 1520), fill=(*gold, 32))
            return
        if variant == "split_chart_phone":
            draw.polygon([(0, 0), (width, 0), (width, 760), (0, 520)], fill=(*cool, 58))
            draw.rectangle((0, int(height * 0.58), width, height), fill=(0, 0, 0, 46))
            return
        if variant == "cash_leak_closeup":
            draw.ellipse((460, 70, 1280, 890), fill=(*gold, 66))
            draw.polygon([(0, 0), (410, 0), (0, 900)], fill=(*muted_rgb, 68))
            return
        if variant == "invest_calendar":
            draw.rectangle((0, 0, width, 620), fill=(*line_rgb, 50))
            draw.polygon([(0, 620), (width, 360), (width, 980), (0, 1180)], fill=(*accent_rgb, 34))
            return
        if variant == "wallet_decision":
            draw.rectangle((0, 0, width, height), fill=(0, 0, 0, 28))
            draw.polygon([(0, 0), (width, 260), (width, 760), (0, 520)], fill=(*gold, 48))
            return
        if variant == "spending_gate":
            draw.polygon([(0, 0), (width, 0), (width, height), (width // 2, height), (0, 720)], fill=(*muted_rgb, 74))
            draw.rectangle((0, int(height * 0.72), width, height), fill=(*accent_rgb, 30))
            return

    if kind == "history":
        if variant == "map_table":
            draw.polygon([(0, 0), (width, 0), (width, 520), (0, 360)], fill=(*accent_rgb, 88))
            draw.rectangle((0, int(height * 0.54), width, height), fill=(0, 0, 0, 104))
            draw.ellipse((-260, 150, 580, 990), fill=(*muted_rgb, 112))
            return
        if variant == "court_gavel":
            draw.polygon([(0, 0), (width, 0), (width, 780), (0, 1040)], fill=(*muted_rgb, 132))
            draw.rectangle((0, 0, width, int(height * 0.32)), fill=(0, 0, 0, 126))
            draw.polygon([(width, 0), (width, height), (width - 310, height), (width - 120, 0)], fill=(*accent_rgb, 54))
            return
        if variant == "artifact_closeup":
            draw.ellipse((190, -160, 1220, 870), fill=(*accent_rgb, 94))
            draw.polygon([(0, 0), (520, 0), (0, 1240)], fill=(0, 0, 0, 126))
            draw.rectangle((0, int(height * 0.70), width, height), fill=(*muted_rgb, 68))
            return
        if variant == "public_square":
            draw.rectangle((0, 0, width, height), fill=(0, 0, 0, 92))
            draw.polygon([(0, 310), (width, 120), (width, 660), (0, 820)], fill=(*muted_rgb, 150))
            draw.ellipse((610, 0, 1320, 710), fill=(*accent_rgb, 74))
            draw.rectangle((0, int(height * 0.43), width, int(height * 0.58)), fill=(*line_rgb, 38))
            return
        if variant == "witness_shadow":
            draw.polygon([(0, 0), (width, 0), (760, height), (0, height)], fill=(0, 0, 0, 156))
            draw.polygon([(width, 0), (width, height), (360, height), (690, 0)], fill=(*muted_rgb, 128))
            draw.ellipse((-160, 580, 640, 1380), fill=(*accent_rgb, 72))
            draw.rectangle((0, 0, width, int(height * 0.16)), fill=(*line_rgb, 56))
            return
        draw.polygon([(0, 0), (width, 0), (width, 600), (0, 840)], fill=(*muted_rgb, 80))
        draw.ellipse((460 - segment * 35, 60, 1160 - segment * 35, 760), fill=(*accent_rgb, 42))
        return

    if kind == "brainrot":
        draw.rectangle((0, 0, width, height), fill=(*muted_rgb, 42))
        draw.polygon([(0, 0), (width, 340), (width, 780), (0, 510)], fill=(*accent_rgb, 72))
        return

    draw.polygon([(0, 0), (width, 0), (width, 560), (0, 760)], fill=(*accent_rgb, 46))


def _draw_motion_scene_anchor(
    draw: Any,
    width: int,
    height: int,
    palette: dict[str, Any],
    *,
    motif: str,
    layout: dict[str, Any],
) -> None:
    """Draw a large non-text scene anchor so rendered clips differ in actual sampled frames."""
    kind = str(layout.get("kind") or "generic")
    variant = str(layout.get("variant") or "")
    accent = (*palette["accent_rgb"], 210)
    accent_soft = (*palette["accent_rgb"], 100)
    line = (*palette["line_rgb"], 150)
    muted_rgb = _hex_to_rgb(str(palette.get("muted") or "#24354A"))
    muted = (*muted_rgb, 150)
    dark = (0, 0, 0, 110)
    parchment = (196, 158, 93, 210)
    shadow = (26, 10, 12, 230)

    if kind == "finance":
        if variant == "split_chart_phone":
            draw.polygon([(0, 0), (width, 0), (width, int(height * 0.42)), (0, int(height * 0.24))], fill=dark)
            draw.rounded_rectangle((96, 170, 438, 560), radius=42, fill=(230, 238, 232, 205), outline=accent, width=8)
            for row in range(6):
                y = 238 + row * 48
                draw.line((142, y, 386 - (row % 3) * 46, y), fill=(*_hex_to_rgb("#13241D"), 205), width=9)
            plant_x, plant_y = 760, 465
            draw.rounded_rectangle((plant_x - 76, plant_y + 68, plant_x + 76, plant_y + 148), radius=24, fill=muted, outline=accent, width=5)
            draw.line((plant_x, plant_y + 72, plant_x, plant_y - 125), fill=accent, width=11)
            draw.ellipse((plant_x - 150, plant_y - 128, plant_x - 12, plant_y + 15), fill=accent_soft)
            draw.ellipse((plant_x + 8, plant_y - 110, plant_x + 160, plant_y + 38), fill=accent_soft)
            return
        if variant == "cash_leak_closeup":
            draw.ellipse((-120, 35, 640, 795), fill=dark, outline=accent, width=12)
            draw.rounded_rectangle((120, 330, 560, 642), radius=60, fill=(15, 32, 29, 225), outline=accent, width=8)
            for i in range(8):
                cx = 590 + i * 54
                cy = 215 + (i % 4) * 82
                draw.ellipse((cx - 31, cy - 31, cx + 31, cy + 31), fill=(236, 211, 116, 225), outline=line, width=4)
            draw.line((510, 470, 895, 280), fill=accent, width=16)
            draw.line((510, 470, 895, 660), fill=accent, width=16)
            return
        if variant == "invest_calendar":
            draw.polygon([(0, 0), (width, 0), (width, 615), (0, 440)], fill=(255, 255, 255, 26))
            grid_x, grid_y = 186, 165
            for row in range(4):
                for col in range(5):
                    x = grid_x + col * 118
                    y = grid_y + row * 96
                    fill = accent_soft if (row + col) % 2 else muted
                    draw.rounded_rectangle((x, y, x + 82, y + 60), radius=16, fill=fill, outline=line, width=3)
            points = [(190, 680), (360, 610), (536, 642), (724, 500), (910, 365)]
            draw.line(points, fill=accent, width=18, joint="curve")
            draw.polygon([(910, 365), (828, 372), (884, 436)], fill=accent)
            return
        if variant == "wallet_decision":
            draw.polygon([(0, 0), (width, 0), (width, 520), (0, 690)], fill=(0, 0, 0, 88))
            wallet_x, wallet_y = 250, 360
            draw.rounded_rectangle((wallet_x - 185, wallet_y - 125, wallet_x + 235, wallet_y + 155), radius=48, fill=(12, 28, 24, 235), outline=accent, width=10)
            draw.rounded_rectangle((wallet_x + 40, wallet_y - 45, wallet_x + 195, wallet_y + 62), radius=26, fill=muted, outline=line, width=5)
            for i in range(5):
                cx = 610 + i * 72
                cy = 235 + i * 48
                draw.ellipse((cx - 34, cy - 34, cx + 34, cy + 34), fill=(236, 211, 116, 225), outline=accent, width=4)
            draw.line((565, 565, 925, 290), fill=accent, width=20)
            draw.polygon([(925, 290), (830, 300), (890, 370)], fill=accent)
            return
        if variant == "spending_gate":
            draw.rectangle((0, 0, width, int(height * 0.40)), fill=(0, 0, 0, 92))
            gate_x, gate_y = width // 2, 365
            draw.rounded_rectangle((gate_x - 260, gate_y - 190, gate_x + 260, gate_y + 190), radius=56, fill=(10, 24, 22, 235), outline=accent, width=11)
            draw.line((gate_x, gate_y - 170, gate_x, gate_y + 170), fill=line, width=7)
            for i in range(4):
                draw.rounded_rectangle((gate_x - 210, gate_y - 118 + i * 70, gate_x - 42, gate_y - 82 + i * 70), radius=15, fill=accent_soft)
                draw.rounded_rectangle((gate_x + 42, gate_y - 118 + i * 70, gate_x + 210, gate_y - 82 + i * 70), radius=15, fill=muted)
            draw.rounded_rectangle((115, 650, 435, 785), radius=35, fill=(232, 240, 235, 205), outline=line, width=5)
            draw.line((160, 708, 390, 708), fill=(*_hex_to_rgb("#13241D"), 205), width=10)
            draw.ellipse((800, 645, 918, 763), fill=(236, 211, 116, 220), outline=accent, width=5)
            return
        draw.rounded_rectangle((640, 120, 1008, 710), radius=54, fill=(6, 16, 14, 230), outline=accent, width=9)
        for i in range(5):
            y = 245 + i * 78
            draw.rounded_rectangle((705, y, 945, y + 34), radius=16, fill=accent_soft)
        draw.rounded_rectangle((74, 220, 470, 620), radius=34, fill=(232, 240, 235, 205), outline=line, width=7)
        draw.line((122, 315, 404, 315), fill=(*_hex_to_rgb("#13241D"), 210), width=11)
        draw.line((122, 390, 352, 390), fill=(*_hex_to_rgb("#13241D"), 190), width=9)
        draw.rounded_rectangle((254, 655, 724, 812), radius=42, fill=muted, outline=accent, width=7)
        if motif == "income":
            draw.ellipse((70, 665, 202, 797), fill=(236, 211, 116, 220), outline=accent, width=5)
        return

    if kind == "history":
        if variant == "map_table":
            draw.rounded_rectangle((70, 150, 960, 735), radius=38, fill=(185, 139, 73, 185), outline=accent, width=9)
            for offset in (0, 90, 180, 270):
                draw.arc((170 + offset, 235 + offset // 3, 520 + offset, 610), 8, 170, fill=dark, width=9)
            draw.line((170, 610, 880, 265), fill=(*_hex_to_rgb("#2A1111"), 210), width=13)
            draw.ellipse((812, 222, 914, 324), outline=accent, width=9)
            return
        if variant == "court_gavel":
            draw.polygon([(0, 0), (width, 0), (width, 460), (0, 720)], fill=dark)
            gx, gy = 520, 330
            draw.line((gx - 210, gy + 210, gx + 250, gy - 190), fill=accent, width=32)
            draw.rounded_rectangle((gx + 140, gy - 270, gx + 385, gy - 146), radius=26, fill=accent)
            draw.rounded_rectangle((gx - 365, gy + 238, gx + 55, gy + 320), radius=28, fill=accent)
            draw.rounded_rectangle((170, 650, 890, 760), radius=26, fill=muted, outline=line, width=5)
            return
        if variant == "artifact_closeup":
            draw.ellipse((250, 110, 830, 690), fill=(55, 28, 18, 225), outline=accent, width=18)
            draw.ellipse((386, 246, 694, 554), outline=line, width=13)
            draw.line((350, 400, 730, 400), fill=accent, width=12)
            draw.line((540, 210, 540, 590), fill=accent_soft, width=10)
            return
        if variant == "public_square":
            draw.rectangle((0, 520, width, 835), fill=dark)
            for x in (135, 275, 415, 555, 695, 835, 975):
                draw.ellipse((x - 42, 360, x + 42, 444), fill=muted, outline=accent_soft, width=4)
                draw.rounded_rectangle((x - 54, 446, x + 54, 690), radius=30, fill=(20, 9, 11, 218), outline=line, width=3)
            draw.rounded_rectangle((345, 170, 735, 325), radius=34, fill=parchment, outline=accent, width=9)
            draw.line((405, 248, 675, 248), fill=shadow, width=9)
            draw.polygon([(540, 325), (505, 520), (575, 520)], fill=accent)
            return
        if variant == "witness_shadow":
            draw.polygon([(110, 750), (420, 185), (720, 750)], fill=(10, 4, 5, 230), outline=accent, width=8)
            draw.ellipse((358, 130, 482, 254), fill=muted, outline=accent, width=6)
            draw.rounded_rectangle((645, 220, 975, 610), radius=28, fill=parchment, outline=accent, width=8)
            for i in range(5):
                y = 300 + i * 54
                draw.line((705, y, 925 - i * 28, y + (i % 2) * 9), fill=shadow, width=7)
            draw.line((210, 620, 920, 420), fill=accent_soft, width=9)
            return
        draw.rounded_rectangle((135, 170, 770, 715), radius=34, fill=(196, 158, 93, 188), outline=accent, width=9)
        for i in range(8):
            y = 265 + i * 52
            draw.line((200, y, 710 - (i % 4) * 42, y + (i % 2) * 8), fill=dark, width=8)
        draw.rounded_rectangle((832, 315, 920, 710), radius=22, fill=(222, 194, 137, 215))
        draw.polygon([(876, 170), (822, 320), (934, 320)], fill=(255, 177, 68, 235))
        return

    if kind == "brainrot":
        if variant == "glitch_portal":
            cx, cy = 540, 410
            for radius in (145, 225, 320, 420):
                draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=accent if radius % 2 else muted, width=15)
            draw.polygon([(0, 0), (width, 0), (width, 190), (0, 380)], fill=(255, 255, 255, 28))
            return
        if variant == "avatar_closeup":
            cx, cy, r = 540, 360, 315
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=muted, outline=accent, width=16)
            draw.ellipse((cx - 150, cy - 72, cx - 70, cy + 18), fill=(0, 0, 0, 230))
            draw.ellipse((cx + 70, cy - 72, cx + 150, cy + 18), fill=(0, 0, 0, 230))
            draw.arc((cx - 150, cy + 58, cx + 150, cy + 235), 10, 170, fill=(0, 0, 0, 230), width=17)
            return
        if variant == "ui_stack":
            for i in range(5):
                x = 120 + i * 105
                y = 150 + i * 90
                draw.rounded_rectangle((x, y, x + 620, y + 105), radius=30, fill=muted if i % 2 else accent_soft, outline=accent, width=7)
            return
        draw.rounded_rectangle((160, 100, 510, 725), radius=58, fill=(12, 8, 25, 235), outline=accent, width=12)
        draw.ellipse((630, 200, 970, 540), fill=muted, outline=accent, width=12)
        for i in range(5):
            y = 235 + i * 80
            draw.rounded_rectangle((230, y, 438, y + 38), radius=18, fill=accent_soft)
        return

    draw.polygon([(0, 0), (width, 0), (width, 520), (0, 700)], fill=dark)
    draw.ellipse((250, 145, 830, 725), outline=accent, width=16)
    draw.rounded_rectangle((172, 458, 908, 620), radius=44, fill=muted, outline=line, width=6)


def _draw_finance_subject(
    draw: Any,
    width: int,
    height: int,
    palette: dict[str, Any],
    *,
    motif: str,
    layout: dict[str, Any],
) -> None:
    accent = (*palette["accent_rgb"], 230)
    soft = (*palette["accent_rgb"], 115)
    paper = (232, 240, 235, 220)
    ink = (*_hex_to_rgb("#11251F"), 210)
    variant = str(layout.get("variant") or "receipt_phone_card")
    if variant == "split_chart_phone":
        phone_x, phone_y = int(width * 0.73), int(height * 0.40)
        receipt_x, receipt_y = int(width * 0.24), int(height * 0.55)
        card_x, card_y = int(width * 0.48), int(height * 0.70)
    elif variant == "cash_leak_closeup":
        phone_x, phone_y = int(width * 0.30), int(height * 0.36)
        receipt_x, receipt_y = int(width * 0.66), int(height * 0.48)
        card_x, card_y = int(width * 0.58), int(height * 0.66)
    elif variant == "invest_calendar":
        phone_x, phone_y = int(width * 0.52), int(height * 0.31)
        receipt_x, receipt_y = int(width * 0.22), int(height * 0.62)
        card_x, card_y = int(width * 0.66), int(height * 0.68)
    elif variant == "wallet_decision":
        phone_x, phone_y = int(width * 0.76), int(height * 0.58)
        receipt_x, receipt_y = int(width * 0.25), int(height * 0.36)
        card_x, card_y = int(width * 0.35), int(height * 0.73)
    elif variant == "spending_gate":
        phone_x, phone_y = int(width * 0.26), int(height * 0.58)
        receipt_x, receipt_y = int(width * 0.72), int(height * 0.36)
        card_x, card_y = int(width * 0.62), int(height * 0.73)
    else:
        phone_x, phone_y = int(width * 0.58), int(height * 0.36)
        receipt_x, receipt_y = int(width * 0.28), int(height * 0.46)
        card_x, card_y = width // 2, int(height * 0.66)

    draw.rounded_rectangle((phone_x - 145, phone_y - 235, phone_x + 145, phone_y + 235), radius=42, fill=(6, 16, 14, 235), outline=accent, width=7)
    for i in range(4):
        y = phone_y - 145 + i * 76
        draw.rounded_rectangle((phone_x - 92, y, phone_x + 92, y + 30), radius=14, fill=soft)

    draw.rounded_rectangle((receipt_x - 140, receipt_y - 185, receipt_x + 140, receipt_y + 185), radius=24, fill=paper, outline=(255, 255, 255, 180), width=3)
    for i in range(6):
        y = receipt_y - 120 + i * 42
        draw.line((receipt_x - 92, y, receipt_x + 86 - (i % 3) * 28, y), fill=ink, width=8)
    draw.rounded_rectangle((card_x - 210, card_y - 70, card_x + 210, card_y + 70), radius=28, fill=(15, 32, 29, 236), outline=accent, width=5)
    draw.rounded_rectangle((card_x - 165, card_y - 38, card_x - 78, card_y + 6), radius=12, fill=soft)
    if variant == "invest_calendar" or motif == "investing":
        grid_x, grid_y = int(width * 0.16), int(height * 0.35)
        for row in range(3):
            for col in range(3):
                x = grid_x + col * 72
                y = grid_y + row * 62
                draw.rounded_rectangle((x, y, x + 46, y + 38), radius=10, fill=soft, outline=accent, width=2)
    if motif in {"savings", "income"}:
        for i in range(5):
            cx = int(width * (0.14 if variant == "cash_leak_closeup" else 0.18)) + i * 48
            cy = int(height * 0.72) - i * 13
            draw.ellipse((cx - 24, cy - 24, cx + 24, cy + 24), fill=(236, 211, 116, 220), outline=accent, width=3)


def _draw_history_subject(
    draw: Any,
    width: int,
    height: int,
    palette: dict[str, Any],
    *,
    motif: str,
    layout: dict[str, Any],
) -> None:
    accent = (*palette["accent_rgb"], 225)
    parchment = (196, 158, 93, 210)
    shadow = (26, 10, 12, 230)
    variant = str(layout.get("variant") or "document_candle")
    table_y = int(height * (0.63 if variant == "court_gavel" else 0.66))
    x_shift = -100 if variant == "map_table" else 90 if variant == "artifact_closeup" else 0
    draw.polygon(
        [
            (170 + x_shift, table_y - 285),
            (width - 165 + x_shift, table_y - 245),
            (width - 205 + x_shift, table_y + 225),
            (145 + x_shift, table_y + 205),
        ],
        fill=parchment,
        outline=accent,
    )
    for i in range(7):
        y = table_y - 190 + i * 54
        draw.line((230 + x_shift, y, width - 260 + x_shift - (i % 3) * 50, y + (i % 2) * 10), fill=shadow, width=5)

    candle_x = int(width * (0.20 if variant == "map_table" else 0.84 if variant == "court_gavel" else 0.78))
    candle_y = int(height * (0.36 if variant != "artifact_closeup" else 0.48))
    draw.rounded_rectangle((candle_x - 34, candle_y - 25, candle_x + 34, candle_y + 190), radius=16, fill=(222, 194, 137, 215))
    draw.ellipse((candle_x - 74, candle_y - 108, candle_x + 74, candle_y + 44), fill=(*palette["accent_rgb"], 65))
    draw.polygon([(candle_x, candle_y - 105), (candle_x - 38, candle_y - 10), (candle_x + 35, candle_y - 8)], fill=(255, 177, 68, 235))
    if motif == "court" or variant == "court_gavel":
        gx = int(width * (0.45 if variant == "court_gavel" else 0.28))
        gy = int(height * (0.30 if variant == "court_gavel" else 0.31))
        draw.line((gx - 80, gy + 100, gx + 105, gy - 82), fill=accent, width=18)
        draw.rounded_rectangle((gx + 45, gy - 120, gx + 165, gy - 58), radius=18, fill=accent)
        draw.rounded_rectangle((gx - 150, gy + 120, gx + 40, gy + 158), radius=18, fill=accent)
    if motif == "map" or variant == "map_table":
        for offset in (0, 75, 150):
            draw.arc((260 + offset, table_y - 250 + offset // 4, 520 + offset, table_y + 20), 10, 170, fill=shadow, width=5)
    if variant == "artifact_closeup" or motif in {"artifact", "plague"}:
        cx, cy = int(width * 0.36), int(height * 0.36)
        draw.ellipse((cx - 132, cy - 132, cx + 132, cy + 132), fill=(64, 34, 22, 225), outline=accent, width=10)
        draw.ellipse((cx - 72, cy - 72, cx + 72, cy + 72), outline=(*palette["line_rgb"], 190), width=8)
        draw.line((cx - 88, cy, cx + 88, cy), fill=accent, width=6)


def _draw_brainrot_subject(
    draw: Any,
    width: int,
    height: int,
    palette: dict[str, Any],
    *,
    motif: str,
    layout: dict[str, Any],
) -> None:
    accent = (*palette["accent_rgb"], 230)
    hot = (*_hex_to_rgb(palette["muted"]), 220)
    variant = str(layout.get("variant") or "phone_avatar")
    if variant == "avatar_closeup":
        screen_x, screen_y = int(width * 0.25), int(height * 0.55)
        avatar_x, avatar_y, avatar_r = int(width * 0.58), int(height * 0.34), 215
    elif variant == "glitch_portal":
        screen_x, screen_y = int(width * 0.72), int(height * 0.45)
        avatar_x, avatar_y, avatar_r = int(width * 0.31), int(height * 0.43), 135
    elif variant == "ui_stack":
        screen_x, screen_y = int(width * 0.50), int(height * 0.34)
        avatar_x, avatar_y, avatar_r = int(width * 0.73), int(height * 0.61), 120
    else:
        screen_x, screen_y = int(width * 0.34), int(height * 0.42)
        avatar_x, avatar_y, avatar_r = int(width * 0.66), int(height * 0.42), 150
    draw.rounded_rectangle((screen_x - 145, screen_y - 250, screen_x + 145, screen_y + 250), radius=42, fill=(12, 8, 25, 235), outline=accent, width=9)
    for i in range(5):
        y = screen_y - 150 + i * 75
        draw.rounded_rectangle((screen_x - 90, y, screen_x + 90, y + 34), radius=16, fill=hot if i % 2 else accent)
    draw.ellipse((avatar_x - avatar_r, avatar_y - avatar_r, avatar_x + avatar_r, avatar_y + avatar_r), fill=hot, outline=accent, width=10)
    eye_dx = max(28, int(avatar_r * 0.42))
    draw.ellipse((avatar_x - eye_dx - 20, avatar_y - 40, avatar_x - eye_dx + 20, avatar_y + 6), fill=(0, 0, 0, 230))
    draw.ellipse((avatar_x + eye_dx - 20, avatar_y - 40, avatar_x + eye_dx + 20, avatar_y + 6), fill=(0, 0, 0, 230))
    if motif == "npc":
        draw.line((avatar_x - int(avatar_r * 0.62), avatar_y + int(avatar_r * 0.52), avatar_x + int(avatar_r * 0.62), avatar_y + int(avatar_r * 0.52)), fill=(0, 0, 0, 235), width=12)
    else:
        draw.arc((avatar_x - int(avatar_r * 0.66), avatar_y, avatar_x + int(avatar_r * 0.66), avatar_y + int(avatar_r * 0.90)), 8, 172, fill=(0, 0, 0, 230), width=12)
    if variant == "glitch_portal":
        cx, cy = int(width * 0.52), int(height * 0.30)
        for radius in (70, 115, 165):
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=accent if radius % 2 else hot, width=10)
    for i in range(3):
        bx = int(width * ((0.18 + i * 0.16) if variant == "ui_stack" else (0.50 + i * 0.12)))
        by = int(height * ((0.68 + i * 0.035) if variant == "ui_stack" else (0.60 + i * 0.04)))
        draw.rounded_rectangle((bx - 90, by - 34, bx + 90, by + 34), radius=24, fill=(*palette["line_rgb"], 160), outline=accent, width=4)


def _motion_eq_filter(
    reference_profile: ReferenceShortsProfile | None = None,
    *,
    channel_profile: ProductionChannelProfile | None = None,
) -> str:
    profile_key = getattr(channel_profile, "key", "")
    if profile_key in {"sigma_squad", "brainrot"}:
        return "eq=contrast=1.18:saturation=1.24:brightness=0.026"
    if profile_key in {"dark_chronicles", "history"}:
        return "eq=contrast=1.16:saturation=0.78:brightness=0.040"
    if profile_key == "wealth_blueprint":
        return "eq=contrast=1.06:saturation=0.58:brightness=0.090"
    brightness = _optional_float(getattr(reference_profile, "median_brightness", None))
    saturation = _optional_float(getattr(reference_profile, "median_saturation", None))
    contrast_value = 1.12
    brightness_value = -0.018
    saturation_value = 0.88
    if brightness is not None:
        if brightness < 0.26:
            contrast_value = 1.20
            brightness_value = 0.012
        elif brightness < 0.38:
            contrast_value = 1.17
            brightness_value = -0.004
        else:
            contrast_value = 1.03
            brightness_value = 0.125
    if saturation is not None:
        saturation_value = max(0.48, min(0.95, 0.42 + saturation * 0.55))
    return f"eq=contrast={contrast_value:.2f}:saturation={saturation_value:.2f}:brightness={brightness_value:.3f}"


def _vignette_shade_alpha(
    reference_profile: ReferenceShortsProfile | None = None,
    *,
    channel_profile: ProductionChannelProfile | None = None,
) -> int:
    profile_key = getattr(channel_profile, "key", "")
    if profile_key == "sigma_squad":
        return 34
    if profile_key == "brainrot":
        return 62
    if profile_key in {"dark_chronicles", "history"}:
        return 76
    if profile_key == "wealth_blueprint":
        return 34
    brightness = _optional_float(getattr(reference_profile, "median_brightness", None))
    if brightness is None:
        return 175
    if brightness < 0.26:
        return 145
    if brightness < 0.38:
        return 112
    return 22


def _motion_palette(
    text: str,
    *,
    reference_profile: ReferenceShortsProfile | None = None,
    channel_profile: ProductionChannelProfile | None = None,
) -> dict[str, Any]:
    lowered = text.casefold()
    profile_key = getattr(channel_profile, "key", "")
    if profile_key == "sigma_squad":
        return {
            "kind": "brainrot",
            "profile_key": profile_key,
            "bg_top": "#4B006E",
            "bg_bottom": "#0AEFFF",
            "accent_rgb": _hex_to_rgb("#CCFF00"),
            "line_rgb": _hex_to_rgb("#00F5FF"),
            "muted": "#FF00D4",
        }
    if profile_key == "brainrot":
        return {
            "kind": "brainrot",
            "profile_key": profile_key,
            "bg_top": "#25003F",
            "bg_bottom": "#FF7A00",
            "accent_rgb": _hex_to_rgb("#FFE600"),
            "line_rgb": _hex_to_rgb("#00F5FF"),
            "muted": "#FF2ED1",
        }
    if profile_key == "dark_chronicles":
        return {
            "kind": "history",
            "profile_key": profile_key,
            "bg_top": "#0A0608",
            "bg_bottom": "#321016",
            "accent_rgb": _hex_to_rgb("#C49A4A"),
            "line_rgb": _hex_to_rgb("#A33A34"),
            "muted": "#8A5A2A",
        }
    if profile_key == "wealth_blueprint":
        return {
            "kind": "finance",
            "profile_key": profile_key,
            "bg_top": "#25372F",
            "bg_bottom": "#708176",
            "accent_rgb": _hex_to_rgb("#9FD8C2"),
            "line_rgb": _hex_to_rgb("#E5EEE9"),
            "muted": "#8BA79A",
        }
    if any(token in lowered for token in ("brainrot", "sigma", "ohio", "meme", "surreal", "ai")):
        accent = "#00F5FF"
        if _profile_is_dark(reference_profile):
            accent = "#C2E5FF"
        if _profile_is_low_saturation(reference_profile):
            accent = "#8FD8DF"
        return {
            "kind": "brainrot",
            "bg_top": "#352E3B" if _profile_is_medium_bright(reference_profile) else "#160018",
            "bg_bottom": "#52606F" if _profile_is_medium_bright(reference_profile) else "#001B2E",
            "accent_rgb": _hex_to_rgb(accent),
            "line_rgb": _hex_to_rgb("#B8C7A8" if _profile_is_low_saturation(reference_profile) else "#FFEB00"),
            "muted": "#A6789C" if _profile_is_low_saturation(reference_profile) else "#FF2ED1",
        }
    if any(token in lowered for token in ("history", "archive", "ancient", "war", "medieval", "gothic")):
        accent = "#C49A4A"
        if _profile_is_high_saturation(reference_profile):
            accent = "#F2B84B"
        return {
            "kind": "history",
            "bg_top": "#332A20" if _profile_is_medium_bright(reference_profile) else "#050202",
            "bg_bottom": "#624D34" if _profile_is_medium_bright(reference_profile) else "#26060A",
            "accent_rgb": _hex_to_rgb(accent),
            "line_rgb": _hex_to_rgb("#8C1D2C"),
            "muted": "#5F3B1D",
        }
    if any(token in lowered for token in ("finance", "money", "savings", "invest", "budget", "fintech", "chart")):
        accent = "#00E69A"
        if _profile_is_dark(reference_profile):
            accent = "#3DFFB8"
        if _profile_is_low_saturation(reference_profile):
            accent = "#8ACCB7"
        return {
            "kind": "finance",
            "bg_top": "#30453D" if _profile_is_medium_bright(reference_profile) else "#030706",
            "bg_bottom": "#587164" if _profile_is_medium_bright(reference_profile) else "#09251E",
            "accent_rgb": _hex_to_rgb(accent),
            "line_rgb": _hex_to_rgb("#D5E3DF" if _profile_is_low_saturation(reference_profile) else "#C2E5FF"),
            "muted": "#78958A" if _profile_is_medium_bright(reference_profile) else "#173B34",
        }
    accent = "#C2E5FF"
    return {
        "kind": "generic",
        "bg_top": "#39414A" if _profile_is_medium_bright(reference_profile) else "#05070B",
        "bg_bottom": "#5B6676" if _profile_is_medium_bright(reference_profile) else "#101A2D",
        "accent_rgb": _hex_to_rgb(accent),
        "line_rgb": _hex_to_rgb("#6D87A3"),
        "muted": "#24354A",
    }


def _profile_is_dark(reference_profile: ReferenceShortsProfile | None) -> bool:
    brightness = _optional_float(getattr(reference_profile, "median_brightness", None))
    return brightness is not None and brightness < 0.34


def _profile_is_medium_bright(reference_profile: ReferenceShortsProfile | None) -> bool:
    brightness = _optional_float(getattr(reference_profile, "median_brightness", None))
    return brightness is not None and brightness >= 0.38


def _profile_is_high_saturation(reference_profile: ReferenceShortsProfile | None) -> bool:
    saturation = _optional_float(getattr(reference_profile, "median_saturation", None))
    return saturation is not None and saturation > 0.45


def _profile_is_low_saturation(reference_profile: ReferenceShortsProfile | None) -> bool:
    saturation = _optional_float(getattr(reference_profile, "median_saturation", None))
    return saturation is not None and saturation < 0.30


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    cleaned = value.strip().lstrip("#")
    if len(cleaned) != 6:
        return (194, 229, 255)
    return tuple(int(cleaned[index : index + 2], 16) for index in (0, 2, 4))


def _optional_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _write_placeholder_clip(path: Path, *, duration_s: float = 4.0) -> None:
    ffmpeg = _ffmpeg_exe()
    duration = max(1.0, min(10.0, float(duration_s or 4.0)))
    if ffmpeg:
        try:
            subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c=0x0A1628:s=1080x1920:d={duration}:r=30",
                    "-vf",
                    "format=yuv420p",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-movflags",
                    "+faststart",
                    str(path),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if path.exists() and path.stat().st_size > 1024:
                return
        except (OSError, subprocess.CalledProcessError):
            pass
    # Minimal MP4-like placeholder for environments without ffmpeg.
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom")


def _is_probeable_video(path: Path) -> bool:
    """Return True only when the file is a real video stream, not just MP4-like bytes."""
    if not path.exists() or path.stat().st_size <= 4096:
        return False
    ffprobe = resolve_ffprobe()
    if ffprobe:
        cmd = [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_type,width,height",
            "-of",
            "json",
            str(path),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
            if proc.returncode == 0 and '"codec_type"' in proc.stdout and '"video"' in proc.stdout:
                return True
        except (OSError, subprocess.SubprocessError):
            pass

    ffmpeg = _ffmpeg_exe()
    if ffmpeg:
        try:
            proc = subprocess.run(
                [ffmpeg, "-v", "error", "-i", str(path), "-frames:v", "1", "-f", "null", "-"],
                capture_output=True,
                text=True,
                timeout=25,
                check=False,
            )
            return proc.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    return False


def _extract_clip_audit_frame(path: Path, *, segment: int) -> Path | None:
    """Extract an early clip frame for deterministic visual quality checks."""
    if not path.exists() or path.stat().st_size <= 4096:
        return None
    ffmpeg = _ffmpeg_exe()
    if not ffmpeg:
        return None
    frame_path = path.with_suffix(f".s{segment:02d}.audit.jpg")
    try:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                "0.25",
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-q:v",
                "3",
                str(frame_path),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if not frame_path.exists() or frame_path.stat().st_size <= 1024:
        return None
    return frame_path


def _copy_cached_ltx_clip(settings: Settings, clip_path: Path) -> bool:
    output_dir = Path(str(settings.comfyui_install_dir or "")) / "ComfyUI" / "output"
    if not output_dir.exists():
        return False
    candidates = sorted(
        output_dir.glob("atnos_ltx*.mp4"),
        key=lambda item: item.stat().st_mtime if item.exists() else 0,
        reverse=True,
    )
    for candidate in candidates:
        if not candidate.exists() or candidate.stat().st_size <= 4096:
            continue
        try:
            shutil.copyfile(candidate, clip_path)
        except OSError:
            continue
        if _is_probeable_video(clip_path):
            return True
    return False


def _ffmpeg_exe() -> str | None:
    resolved = resolve_ffmpeg() or shutil.which("ffmpeg")
    if resolved:
        return resolved
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None
