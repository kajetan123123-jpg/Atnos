from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from atnos.core.config import Settings
from atnos.model_manager.registry import build_model_registry
from atnos.model_manager.verifier import verify_all_models
from atnos.production.cost_guard import CostGuard
from atnos.production.models import ApprovedTopic, ProductionContext
from atnos.production.readiness import _effective_shorts_video_backend, _openai_image_motion_ready
from atnos.production.steps import video_generator
from atnos.services.comfyui_runtime_service import ComfyUiRuntimeService

COMFYUI_PUBLIC_QUALITY_BACKENDS = {"auto", "wan", "ltx"}
OPENAI_PUBLIC_QUALITY_BACKENDS = {"auto", "openai_image_motion"}


def visual_backend_report_paths(settings: Settings) -> tuple[Path, Path]:
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    report_dir = settings.data_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    return (
        report_dir / f"visual_backend_doctor_{timestamp}.json",
        report_dir / f"visual_backend_doctor_{timestamp}.md",
    )


async def build_visual_backend_doctor_report(settings: Settings) -> dict[str, Any]:
    """Sprawdza live status backendu wideo bez uruchamiania kosztownego renderu."""
    selected_backend = str(getattr(settings, "shorts_video_backend", "motion_graphic") or "motion_graphic").strip().lower()
    effective_backend = _effective_shorts_video_backend(settings)
    openai_ready = _openai_image_motion_ready(settings)
    openai_generation = _openai_image_motion_generation_evidence(settings)
    cloud_visual_budget = _cloud_visual_budget_status(settings)
    cloud_visual_budget_allowed = bool(cloud_visual_budget.get("allowed"))
    runtime = ComfyUiRuntimeService(settings)
    install_state = runtime.describe_install()
    video = {
        "selected_backend": selected_backend,
        "effective_backend": effective_backend,
        "openai_image_motion_ready": openai_ready,
        "endpoint": settings.comfyui_base_url,
        "install_dir": str(install_state.install_dir) if install_state.install_dir else None,
        "install_exists": install_state.exists,
        "run_script": str(install_state.run_script) if install_state.run_script else None,
        "fallback_script": str(install_state.fallback_script) if install_state.fallback_script else None,
        "python_executable": str(install_state.python_executable) if install_state.python_executable else None,
        "main_script": str(install_state.main_script) if install_state.main_script else None,
        "checkpoint_dir": str(install_state.checkpoint_dir) if install_state.checkpoint_dir else None,
        "auto_launch_supported": bool(
            install_state.exists
            and install_state.install_dir
            and (
                (install_state.python_executable and install_state.main_script)
                or install_state.run_script
                or install_state.fallback_script
            )
        ),
        "startup_service": "atnos.services.comfyui_runtime_service.ComfyUiRuntimeService.ensure_startup_ready",
    }
    status = "skipped"
    message = "ComfyUI live probe skipped because selected backend is not a ComfyUI public-quality backend."
    if effective_backend in COMFYUI_PUBLIC_QUALITY_BACKENDS:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"{settings.comfyui_base_url.rstrip('/')}/system_stats")
            status = "ready" if response.status_code == 200 else "degraded"
            message = "ComfyUI is reachable." if response.status_code == 200 else f"ComfyUI returned HTTP {response.status_code}."
        except Exception as exc:
            status = "degraded"
            message = str(exc)
    comfyui_capability = await _comfyui_public_backend_capability(
        settings=settings,
        effective_backend=effective_backend,
        probe_status=status,
    )
    comfyui_generation = _comfyui_generation_evidence(settings)
    if status == "ready" and not bool(comfyui_capability.get("ready")):
        status = "degraded"
        message = str(comfyui_capability.get("message") or "ComfyUI is reachable but no WAN/LTX public-quality path is ready.")
    if (
        status == "ready"
        and bool(comfyui_capability.get("ready"))
        and str(comfyui_generation.get("status") or "") != "verified_success"
    ):
        status = "degraded"
        message = str(comfyui_generation.get("message") or "ComfyUI WAN/LTX generation is not verified.")
    comfyui_ready = (
        bool(comfyui_capability.get("ready"))
        and str(comfyui_generation.get("status") or "") == "verified_success"
        and effective_backend in COMFYUI_PUBLIC_QUALITY_BACKENDS
    )
    openai_generation_ready = openai_generation["status"] == "verified_success"
    openai_fallback_ready = (
        effective_backend in OPENAI_PUBLIC_QUALITY_BACKENDS
        and openai_ready
        and bool(settings.shorts_allow_cloud_visuals)
        and openai_generation_ready
        and cloud_visual_budget_allowed
    )
    public_visual_ready = comfyui_ready or openai_fallback_ready
    if effective_backend == "openai_image_motion":
        public_visual_ready = (
            openai_ready
            and bool(settings.shorts_allow_cloud_visuals)
            and openai_generation_ready
            and cloud_visual_budget_allowed
        )
    openai_provider_status, openai_provider_error = _openai_provider_status(
        openai_ready=openai_ready,
        cloud_visuals_allowed=bool(settings.shorts_allow_cloud_visuals),
        cloud_visual_budget_allowed=cloud_visual_budget_allowed,
        cloud_visual_budget_status=str(cloud_visual_budget.get("status") or ""),
        generation=openai_generation,
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "public_visual_ready": bool(public_visual_ready),
        "selected_video_backend": selected_backend,
        "effective_video_backend": effective_backend,
        "providers": {
            "comfyui": {
                "configured": bool(video.get("install_exists") or video.get("auto_launch_supported")),
                "live_checked": effective_backend in COMFYUI_PUBLIC_QUALITY_BACKENDS,
                "live_ready": bool(comfyui_ready),
                "status": status,
                "error": None if status == "ready" else message,
                "endpoint": video.get("endpoint"),
                "install_exists": bool(video.get("install_exists")),
                "auto_launch_supported": bool(video.get("auto_launch_supported")),
                "backend_public_quality": effective_backend in COMFYUI_PUBLIC_QUALITY_BACKENDS,
            },
            "openai_image_motion": {
                "configured": bool(openai_ready),
                "live_checked": False,
                "live_ready": bool(openai_fallback_ready or (effective_backend == "openai_image_motion" and public_visual_ready)),
                "status": openai_provider_status,
                "error": openai_provider_error,
                "cloud_visuals_allowed": bool(settings.shorts_allow_cloud_visuals),
                "budget_allowed": cloud_visual_budget_allowed,
                "budget_status": cloud_visual_budget.get("status"),
                "estimated_generation_cost_pln": cloud_visual_budget.get("estimated_cost_pln"),
                "monthly_remaining_pln": cloud_visual_budget.get("monthly_remaining_pln"),
                "daily_remaining_pln": cloud_visual_budget.get("daily_remaining_pln"),
                "backend_public_quality": effective_backend in OPENAI_PUBLIC_QUALITY_BACKENDS,
                "last_success_at": openai_generation.get("last_success_at"),
                "last_error_at": openai_generation.get("last_error_at"),
            },
            "motion_graphic": {
                "configured": effective_backend == "motion_graphic",
                "live_checked": False,
                "live_ready": effective_backend == "motion_graphic",
                "status": "fallback_only",
                "error": "Motion graphics fallback is not accepted as public-quality visual backend.",
                "backend_public_quality": False,
            },
        },
        "evidence": {
            "readiness_status": status,
            "readiness_message": message,
            "comfyui_public_backend_capability": comfyui_capability,
            "comfyui_generation": comfyui_generation,
            "comfyui_required": bool(
                video.get(
                    "comfyui_required",
                    effective_backend in COMFYUI_PUBLIC_QUALITY_BACKENDS and not openai_fallback_ready,
                )
            ),
            "openai_image_motion_fallback_ready": bool(openai_fallback_ready),
            "openai_image_motion_generation": openai_generation,
            "cloud_visual_budget": cloud_visual_budget,
            "local_video_backend": video.get("local_video_backend"),
            "quality_gate": video.get("quality_gate"),
            "startup_service": video.get("startup_service"),
        },
        "next_action": visual_backend_next_action(
            effective_backend=effective_backend,
            status=status,
            public_visual_ready=bool(public_visual_ready),
            openai_ready=openai_ready,
            cloud_visual_budget_allowed=cloud_visual_budget_allowed,
            openai_generation_status=str(openai_generation.get("status") or ""),
            comfyui_generation_status=str(comfyui_generation.get("status") or ""),
        ),
    }


async def write_visual_backend_doctor_report(settings: Settings) -> dict[str, Any]:
    report = await build_visual_backend_doctor_report(settings)
    json_path, md_path = visual_backend_report_paths(settings)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(visual_backend_report_to_markdown(report), encoding="utf-8")
    return {**report, "report_path": str(json_path), "markdown_report_path": str(md_path)}


async def run_visual_backend_smoke_render(
    settings: Settings,
    *,
    mode: str = "ltx",
    prompt: str = "A cinematic finance dashboard comes alive with moving cashflow lines, smooth motion, no text.",
) -> dict[str, Any]:
    """Uruchom krótki smoke-render backendu wideo i odśwież raport visual doctor.

    To jest akcja operatorska: nie publikuje, nie uploaduje i nie tworzy pełnego joba.
    Służy wyłącznie do potwierdzenia, że public-quality backend potrafi zapisać realny klip.
    """
    selected_mode = str(mode or "ltx").strip().lower()
    if selected_mode not in {"auto", "ltx", "wan"}:
        selected_mode = "ltx"

    smoke_settings = settings.model_copy(
        update={
            "shorts_video_backend": selected_mode,
            "shorts_visual_max_generated_scenes": 1,
            "shorts_visual_min_real_clips": 1,
            "shorts_visual_clip_duration_s": 1.0,
            "video_generation_steps": max(4, min(int(getattr(settings, "video_generation_steps", 8) or 8), 8)),
        }
    )
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    artifact_dir = smoke_settings.data_dir / "tmp" / "visual-backend-smoke" / timestamp
    artifact_dir.mkdir(parents=True, exist_ok=True)
    context = ProductionContext(
        job_id=f"visual-smoke-{timestamp}",
        account_id="visual-backend-smoke",
        topic=ApprovedTopic(
            id=f"visual-smoke-{timestamp}",
            account_id="visual-backend-smoke",
            title="Visual backend smoke render",
            description="One-scene smoke render for public-quality visual backend verification.",
            total_score=0.0,
            content_profile="wealth_blueprint",
        ),
        artifact_dir=artifact_dir,
        artifacts={
            "visuals": {
                "scenes": [
                    {
                        "scene_id": "smoke-01",
                        "segment_id": "smoke-01",
                        "duration_s": 1.0,
                        "visual_note": prompt,
                        "visual_prompt": prompt,
                        "positive_prompt": prompt,
                        "negative_prompt": "blurry, low quality, watermark, text, distorted motion",
                    }
                ]
            }
        },
    )

    started = datetime.now(UTC)
    step = await video_generator.run(smoke_settings, context)
    elapsed_s = round((datetime.now(UTC) - started).total_seconds(), 3)
    doctor = await write_visual_backend_doctor_report(smoke_settings)
    return {
        "status": "completed",
        "mode": selected_mode,
        "artifact_dir": str(artifact_dir),
        "elapsed_s": elapsed_s,
        "video_manifest_path": str(step.artifact_path),
        "video_manifest": step.payload,
        "public_visual_ready": bool(doctor.get("public_visual_ready")),
        "visual_backend_report_path": doctor.get("report_path"),
        "next_action": doctor.get("next_action"),
    }


def visual_backend_next_action(
    *,
    effective_backend: str,
    status: str,
    public_visual_ready: bool,
    openai_ready: bool,
    cloud_visual_budget_allowed: bool = True,
    openai_generation_status: str = "",
    comfyui_generation_status: str = "",
) -> str:
    if public_visual_ready:
        return "visual_backend_ready_generate_private_public_quality_short"
    if effective_backend == "motion_graphic":
        return "switch_shorts_video_backend_to_auto_wan_or_ltx"
    if (
        effective_backend in OPENAI_PUBLIC_QUALITY_BACKENDS
        and openai_ready
        and not cloud_visual_budget_allowed
        and openai_generation_status == "verified_success"
    ):
        return "approve_cloud_visual_budget_or_use_local_wan_ltx"
    if (
        effective_backend in COMFYUI_PUBLIC_QUALITY_BACKENDS
        and comfyui_generation_status == "unverified"
        and openai_generation_status.startswith("blocked")
    ):
        return "run_wan_ltx_smoke_render_or_fix_openai_billing"
    if effective_backend in COMFYUI_PUBLIC_QUALITY_BACKENDS and comfyui_generation_status.startswith("blocked"):
        if openai_generation_status.startswith("blocked"):
            return "fix_comfyui_wan_ltx_generation_and_openai_billing"
        return "fix_comfyui_wan_ltx_generation_or_use_openai_image_motion"
    if effective_backend in COMFYUI_PUBLIC_QUALITY_BACKENDS and comfyui_generation_status == "unverified":
        return "run_wan_ltx_smoke_render_or_use_verified_openai_image_motion"
    if effective_backend in COMFYUI_PUBLIC_QUALITY_BACKENDS and status != "ready" and not openai_ready:
        return "start_or_fix_comfyui_wan_ltx_backend"
    if effective_backend in OPENAI_PUBLIC_QUALITY_BACKENDS and openai_ready and not cloud_visual_budget_allowed:
        return "approve_cloud_visual_budget_or_use_local_wan_ltx"
    if effective_backend in OPENAI_PUBLIC_QUALITY_BACKENDS and openai_ready:
        if openai_generation_status.startswith("blocked"):
            return "fix_openai_image_billing_or_use_local_wan_ltx"
        if openai_generation_status == "unverified":
            return "run_openai_image_motion_probe_or_configure_local_wan_ltx"
    if effective_backend == "openai_image_motion" and not openai_ready:
        return "enable_cloud_visuals_or_configure_openai_image_motion"
    if effective_backend == "openai_image_motion":
        return "allow_cloud_visuals_or_use_local_wan_ltx"
    return "run_visual_backend_doctor_after_backend_configuration"


def _openai_image_motion_generation_evidence(settings: Settings) -> dict[str, Any]:
    """Zwraca ostatni realny wynik generacji OpenAI image-motion zapisany przez pipeline."""
    production_dir = settings.data_dir / "production"
    latest_success = _latest_file(production_dir, "*.openai.png")
    latest_error = _latest_file(production_dir, "*.openai_errors.json")
    latest_success_at = _file_mtime_iso(latest_success)
    latest_error_at = _file_mtime_iso(latest_error)

    if latest_success and (latest_error is None or latest_success.stat().st_mtime >= latest_error.stat().st_mtime):
        return {
            "status": "verified_success",
            "last_success_path": str(latest_success),
            "last_success_at": latest_success_at,
            "last_error_path": str(latest_error) if latest_error else None,
            "last_error_at": latest_error_at,
            "last_error_reason": None,
        }

    if latest_error:
        reason = _openai_error_reason(latest_error)
        status = "blocked_recent_failure" if _is_blocking_openai_error(reason) else "degraded_recent_failure"
        return {
            "status": status,
            "last_success_path": str(latest_success) if latest_success else None,
            "last_success_at": latest_success_at,
            "last_error_path": str(latest_error),
            "last_error_at": latest_error_at,
            "last_error_reason": reason,
        }

    return {
        "status": "unverified",
        "last_success_path": None,
        "last_success_at": None,
        "last_error_path": None,
        "last_error_at": None,
        "last_error_reason": None,
    }


def _latest_file(root: Path, pattern: str) -> Path | None:
    if not root.exists():
        return None
    candidates = [path for path in root.rglob(pattern) if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _file_mtime_iso(path: Path | None) -> str | None:
    if path is None:
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()


def _openai_error_reason(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "Unable to read OpenAI image-motion error evidence."
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if not isinstance(errors, list):
        return "Unknown OpenAI image-motion generation error."
    reasons = []
    for item in errors:
        if isinstance(item, dict) and item.get("reason"):
            reasons.append(str(item["reason"]))
    return " | ".join(reasons)[:600] if reasons else "Unknown OpenAI image-motion generation error."


def _is_blocking_openai_error(reason: str) -> bool:
    normalized = reason.casefold()
    blocking_terms = (
        "billing",
        "hard_limit",
        "insufficient_quota",
        "quota",
        "unauthorized",
        "forbidden",
        "invalid_api_key",
        "permission",
        "account",
        "payment",
    )
    return any(term in normalized for term in blocking_terms)


def _openai_provider_status(
    *,
    openai_ready: bool,
    cloud_visuals_allowed: bool,
    cloud_visual_budget_allowed: bool,
    cloud_visual_budget_status: str,
    generation: dict[str, Any],
) -> tuple[str, str | None]:
    if not openai_ready:
        return "missing", "OpenAI key/cloud visual access missing."
    if not cloud_visuals_allowed:
        return "disabled", "Cloud visuals are disabled in settings."
    if not cloud_visual_budget_allowed:
        return "budget_blocked", f"Cloud visual budget is not approved: {cloud_visual_budget_status or 'blocked'}."
    status = str(generation.get("status") or "unverified")
    if status == "verified_success":
        return "verified_success", None
    if status.startswith("blocked"):
        return status, str(generation.get("last_error_reason") or "Recent OpenAI image-motion generation failed.")
    if status.startswith("degraded"):
        return status, str(generation.get("last_error_reason") or "Recent OpenAI image-motion generation failed.")
    return "configured_unverified", "No successful OpenAI image-motion generation has been recorded."


def _cloud_visual_budget_status(settings: Settings) -> dict[str, Any]:
    """Sprawdza lokalny limit kosztów dla płatnej generacji wizualnej."""
    estimate = round(max(float(getattr(settings, "atnos_estimated_visual_cost_per_short_pln", 0.0) or 0.0), 0.0), 4)
    if not bool(getattr(settings, "shorts_allow_cloud_visuals", False)):
        return {
            "required": False,
            "allowed": False,
            "status": "cloud_visuals_disabled",
            "estimated_cost_pln": estimate,
            "blockers": ["cloud_visuals_disabled"],
            "message": "Cloud visuals are disabled in settings.",
            "monthly_remaining_pln": None,
            "daily_remaining_pln": None,
        }
    decision = CostGuard(settings).check_can_spend(
        estimate,
        operation="openai_image_motion_visual_generation",
    )
    blockers = [str(item) for item in decision.get("blockers") or [] if str(item).strip()]
    allowed = bool(decision.get("allowed"))
    return {
        "required": estimate > 0,
        "allowed": allowed,
        "status": "allowed" if allowed else "budget_blocked",
        "estimated_cost_pln": estimate,
        "blockers": blockers,
        "message": str(decision.get("message") or ""),
        "monthly_remaining_pln": decision.get("monthly_remaining_pln"),
        "daily_remaining_pln": decision.get("daily_remaining_pln"),
        "monthly_spent_pln": decision.get("monthly_spent_pln"),
        "daily_spent_pln": decision.get("daily_spent_pln"),
    }


async def _comfyui_public_backend_capability(
    *,
    settings: Settings,
    effective_backend: str,
    probe_status: str,
) -> dict[str, Any]:
    if effective_backend not in COMFYUI_PUBLIC_QUALITY_BACKENDS:
        return {"ready": False, "status": "not_required", "modes": [], "message": "ComfyUI backend is not selected."}
    if probe_status != "ready":
        return {"ready": False, "status": "comfyui_unreachable", "modes": [], "message": "ComfyUI live probe is not ready."}

    modes = ("wan", "ltx") if effective_backend == "auto" else (effective_backend,)
    mode_results = []
    for mode in modes:
        workflow = video_generator._load_or_create_workflow(video_generator._workflow_path(settings, mode), mode)
        workflow_diagnostics = video_generator._workflow_diagnostics(workflow, mode)
        workflow_node_classes = video_generator._workflow_class_types(workflow)
        model_ids = ["wan_video_1_3b"] if mode == "wan" else ["ltx_video", "ltx_vae", "t5_encoder"]
        model_status = await verify_all_models(model_ids, registry=build_model_registry(settings))
        models_ready = all(item.get("status") == "ok" for item in model_status)
        node_status = await _comfyui_node_status(settings, workflow_node_classes)
        nodes_ready = all(item.get("ready") for item in node_status.values()) if node_status else False
        ready = bool(workflow_diagnostics.get("valid")) and models_ready and nodes_ready
        mode_results.append(
            {
                "mode": mode,
                "ready": ready,
                "workflow": workflow_diagnostics,
                "models_ready": models_ready,
                "models": model_status,
                "nodes_ready": nodes_ready,
                "nodes": node_status,
            }
        )
        if ready:
            return {
                "ready": True,
                "status": "ready",
                "selected_mode": mode,
                "modes": mode_results,
                "message": f"ComfyUI {mode.upper()} workflow, nodes and models are ready.",
            }

    return {
        "ready": False,
        "status": "missing_capability",
        "selected_mode": None,
        "modes": mode_results,
        "message": "ComfyUI is reachable but no WAN/LTX path has valid workflow, nodes and models.",
    }


async def _comfyui_node_status(settings: Settings, class_types: list[str]) -> dict[str, dict[str, Any]]:
    unique = sorted({item for item in class_types if item})
    status: dict[str, dict[str, Any]] = {}
    async with httpx.AsyncClient(timeout=2.0) as client:
        for class_type in unique:
            try:
                response = await client.get(f"{settings.comfyui_base_url.rstrip('/')}/object_info/{class_type}")
                try:
                    payload = response.json()
                except ValueError:
                    payload = {}
                node_available = response.status_code == 200 and isinstance(payload, dict) and class_type in payload
                status[class_type] = {
                    "ready": node_available,
                    "status_code": response.status_code,
                    "present_in_payload": node_available,
                }
            except Exception as exc:
                status[class_type] = {
                    "ready": False,
                    "error": str(exc)[:220],
                }
    return status


def _comfyui_generation_evidence(settings: Settings) -> dict[str, Any]:
    """Sprawdza ostatni manifest renderu WAN/LTX i wykrywa fallback do motion graphics."""
    manifest = _latest_comfyui_video_manifest(settings)
    output = _latest_comfyui_backend_output(settings)
    if output is not None and (manifest is None or output.stat().st_mtime > manifest.stat().st_mtime):
        mode = "ltx" if output.name.startswith("atnos_ltx_") else "wan"
        if video_generator._is_probeable_video(output):
            return {
                "status": "verified_success",
                "last_manifest_path": str(manifest) if manifest else None,
                "last_manifest_at": _file_mtime_iso(manifest),
                "last_output_path": str(output),
                "last_output_at": _file_mtime_iso(output),
                "backend_mode": mode,
                "clip_backends": [mode],
                "last_error_path": None,
                "last_error_reason": None,
                "message": f"Latest ComfyUI output contains a probeable {mode.upper()} video.",
            }
    if manifest is None:
        return {
            "status": "unverified",
            "last_manifest_path": None,
            "last_manifest_at": None,
            "last_output_path": str(output) if output else None,
            "last_output_at": _file_mtime_iso(output),
            "backend_mode": None,
            "clip_backends": [],
            "message": "No successful WAN/LTX smoke render has been recorded.",
        }
    payload = _read_json_dict(manifest)
    backend = payload.get("backend") if isinstance(payload.get("backend"), dict) else {}
    mode = str(backend.get("mode") or "")
    clips = payload.get("clips") if isinstance(payload.get("clips"), list) else []
    clip_backends = [str(clip.get("backend") or "") for clip in clips if isinstance(clip, dict)]
    clip_statuses = [str(clip.get("status") or "") for clip in clips if isinstance(clip, dict)]
    fallback_used = bool(payload.get("fallback_used")) or any(item != mode for item in clip_backends)
    generated = bool(clips) and all(status == "generated" for status in clip_statuses)
    error_evidence = _latest_comfyui_backend_error(manifest, mode)
    if mode in video_generator.COMFYUI_VIDEO_BACKENDS and generated and not fallback_used:
        return {
            "status": "verified_success",
            "last_manifest_path": str(manifest),
            "last_manifest_at": _file_mtime_iso(manifest),
            "backend_mode": mode,
            "clip_backends": clip_backends,
            "last_error_path": error_evidence.get("path"),
            "last_error_reason": error_evidence.get("reason"),
            "message": f"Latest WAN/LTX render used real {mode} clips.",
        }
    error_reason = str(error_evidence.get("reason") or "").strip()
    message = "Latest WAN/LTX render fell back to a non-premium backend."
    if error_reason:
        message = f"{message} Last ComfyUI error: {error_reason}"
    return {
        "status": "blocked_recent_fallback",
        "last_manifest_path": str(manifest),
        "last_manifest_at": _file_mtime_iso(manifest),
        "backend_mode": mode,
        "clip_backends": clip_backends,
        "last_error_path": error_evidence.get("path"),
        "last_error_reason": error_reason or None,
        "message": message,
    }


def _latest_comfyui_video_manifest(settings: Settings) -> Path | None:
    roots = [settings.data_dir / "production", settings.data_dir / "tmp"]
    manifests: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("video_manifest.json"):
            payload = _read_json_dict(path)
            backend = payload.get("backend") if isinstance(payload.get("backend"), dict) else {}
            if str(backend.get("mode") or "") in video_generator.COMFYUI_VIDEO_BACKENDS:
                manifests.append(path)
    if not manifests:
        return None
    return max(manifests, key=lambda path: path.stat().st_mtime)


def _latest_comfyui_backend_output(settings: Settings) -> Path | None:
    install_dir = getattr(settings, "comfyui_install_dir", None)
    if not install_dir:
        return None
    output_dir = Path(install_dir) / "ComfyUI" / "output"
    if not output_dir.exists():
        return None
    candidates: list[Path] = []
    for pattern in ("atnos_ltx_*.mp4", "atnos_wan_*.mp4"):
        candidates.extend(path for path in output_dir.glob(pattern) if path.is_file())
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _read_json_dict(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _latest_comfyui_backend_error(manifest_path: Path, mode: str) -> dict[str, Any]:
    video_dir = manifest_path.parent / "video"
    if not video_dir.exists():
        return {"path": None, "reason": None}
    errors = [path for path in video_dir.glob(f"*.{mode}_errors.json") if path.is_file()]
    if not errors:
        return {"path": None, "reason": None}
    path = max(errors, key=lambda item: item.stat().st_mtime)
    payload = _read_json_dict(path)
    reason = str(payload.get("reason") or "ComfyUI generation failed.")
    details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
    body = str(details.get("body") or details.get("error") or "").strip()
    if body:
        reason = f"{reason}: {body[:500]}"
    return {"path": str(path), "reason": reason[:700]}


def visual_backend_report_to_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Atnos Visual Backend Doctor",
        "",
        f"- generated_at: `{report['generated_at']}`",
        f"- public_visual_ready: `{report['public_visual_ready']}`",
        f"- selected_video_backend: `{report['selected_video_backend']}`",
        f"- effective_video_backend: `{report['effective_video_backend']}`",
        f"- next_action: `{report['next_action']}`",
        "",
        "## Providers",
        "| provider | configured | live_checked | live_ready | status | error |",
        "|---|---:|---:|---:|---|---|",
    ]
    providers = report["providers"]
    for name, payload in providers.items():
        error = str(payload.get("error") or "").replace("\n", " ")[:260]
        lines.append(
            "| {name} | {configured} | {live_checked} | {live_ready} | {status} | {error} |".format(
                name=name,
                configured=payload.get("configured"),
                live_checked=payload.get("live_checked"),
                live_ready=payload.get("live_ready"),
                status=payload.get("status") or "-",
                error=error or "-",
            )
        )
    lines.append("")
    return "\n".join(lines)
