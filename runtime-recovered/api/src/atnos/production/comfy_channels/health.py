"""Per-channel Comfy health checks — required vs optional capabilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from atnos.core.config import Settings
from atnos.production.comfy_channels.model_profiles import load_custom_node_policy, load_model_profile
from atnos.production.comfy_channels.models import ChannelId
from atnos.production.comfy_channels.profiles import (
    CHANNEL_IDS,
    load_profile,
    shared_models_root,
)
from atnos.services.comfyui_runtime_service import ComfyUiRuntimeService
from atnos.services.gpu_runtime_lock_service import GpuRuntimeLockService

HealthStatus = Literal["HEALTHY", "DEGRADED", "BLOCKED_DEPENDENCY"]

# Channel production requirements (not a laundry list of all tech).
CHANNEL_REQUIREMENTS: dict[ChannelId, dict[str, Any]] = {
    "wealth_blueprint": {
        "requires_reference_conditioning": True,
        "requires_character_consistency": True,
        "optional": ["flux2_klein", "z_image_turbo", "ipadapter"],
    },
    "wonder_crew": {
        "requires_reference_conditioning": True,
        "requires_character_consistency": True,
        "optional": ["flux2_klein", "z_image_turbo", "ipadapter"],
    },
    "dark_chronicles": {
        "requires_reference_conditioning": False,
        "requires_character_consistency": False,
        "optional": ["flux2_klein", "z_image_turbo", "ipadapter"],
    },
}


def _item(name: str, status: HealthStatus, detail: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "status": status, "detail": detail, **extra}


def check_channel_comfy(settings: Settings, channel: ChannelId) -> dict[str, Any]:
    profile = load_profile(settings, channel)
    reqs = CHANNEL_REQUIREMENTS[channel]
    model_prof = load_model_profile(settings, channel)
    policy = load_custom_node_policy(settings)
    checks: list[dict[str, Any]] = []
    blocked: list[str] = []

    runtime = ComfyUiRuntimeService(settings)
    try:
        ready = bool(runtime._server_ready())  # noqa: SLF001
        api_error = None
    except Exception as exc:  # noqa: BLE001
        ready = False
        api_error = str(exc)
    if ready:
        checks.append(_item("api", "HEALTHY", f"reachable {settings.comfyui_base_url}"))
    else:
        detail = api_error or f"API not up at {settings.comfyui_base_url} (managed launch may start on demand)"
        checks.append(_item("api", "DEGRADED", detail))

    project = Path(profile.comfy_project_dir)
    if project.is_dir() and (project / "workflows").is_dir():
        checks.append(_item("workflow_root", "HEALTHY", str(project / "workflows")))
    else:
        checks.append(_item("workflow_root", "BLOCKED_DEPENDENCY", f"missing {project}"))
        blocked.append("workflow_root")

    if profile.workflows:
        checks.append(
            _item(
                "workflows",
                "HEALTHY",
                f"{len(profile.workflows)} registered",
            )
        )
    else:
        checks.append(_item("workflows", "BLOCKED_DEPENDENCY", "no workflows"))
        blocked.append("workflows")

    models_root = shared_models_root(settings)
    primary = str(model_prof.get("primary_model") or "")
    fallback = str(model_prof.get("fallback_model") or profile.image_fallback or "")
    primary_ok = bool(primary) and (models_root / primary).is_file()
    fallback_ok = bool(fallback) and (models_root / fallback).is_file()
    if primary_ok:
        checks.append(_item("models", "HEALTHY", f"primary {primary}"))
    elif fallback_ok:
        checks.append(_item("models", "DEGRADED", f"primary missing; fallback ok {fallback}"))
    else:
        checks.append(_item("models", "BLOCKED_DEPENDENCY", f"missing primary/fallback under {models_root}"))
        blocked.append("models")

    # Optional Flux/Z-Image — informational only, never blocks/degrades production health.
    checks.append(
        _item(
            "optional_flux_z_image",
            "HEALTHY",
            "OPTIONAL/BLOCKED on AMD ROCm — not a production dependency",
            production_required=False,
        )
    )

    # Custom node policy: CORE_ONLY with disable-all is HEALTHY when allowlist is empty by design.
    if policy.get("disable_all_custom_nodes") and not (policy.get("allowlist") or []):
        checks.append(
            _item(
                "custom_nodes",
                "HEALTHY",
                f"policy={policy.get('policy', 'CORE_ONLY')}: --disable-all-custom-nodes; allowlist empty",
            )
        )
    elif policy.get("allowlist"):
        checks.append(
            _item(
                "custom_nodes",
                "DEGRADED",
                f"allowlist configured but runtime still uses disable-all: {policy.get('allowlist')}",
            )
        )
    else:
        checks.append(_item("custom_nodes", "HEALTHY", "CORE_ONLY"))

    # IPAdapter is optional inventory; production reference path is core img2img.
    ipadapter_dir = models_root / "ipadapter"
    ipadapter_present = ipadapter_dir.is_dir() and any(ipadapter_dir.glob("*"))
    checks.append(
        _item(
            "ipadapter",
            "HEALTHY",
            "ABSENT — not production path; core img2img used instead"
            if not ipadapter_present
            else "models present but nodes disabled by CORE_ONLY policy",
            production_required=False,
        )
    )

    ref_mode = str(model_prof.get("reference_mode") or "core_img2img")
    if reqs["requires_reference_conditioning"]:
        if ref_mode == "core_img2img":
            # Core nodes always available when API is up; mark DEGRADED only if API down and no managed launch hope
            status: HealthStatus = "HEALTHY" if ready else "DEGRADED"
            checks.append(
                _item(
                    "reference_conditioning",
                    status,
                    "core LoadImage+VAEEncode+KSampler img2img",
                )
            )
        elif ref_mode == "ipadapter" and not ipadapter_present:
            if channel == "wonder_crew":
                checks.append(
                    _item(
                        "reference_conditioning",
                        "BLOCKED_DEPENDENCY",
                        "IPAdapter required by profile but unavailable",
                    )
                )
                blocked.append("reference_conditioning")
            else:
                checks.append(
                    _item(
                        "reference_conditioning",
                        "DEGRADED",
                        "IPAdapter unavailable; falling back needed",
                    )
                )
        else:
            checks.append(_item("reference_conditioning", "HEALTHY", ref_mode))
    else:
        checks.append(
            _item(
                "reference_conditioning",
                "HEALTHY",
                "not required for Dark Chronicles production workflows",
            )
        )

    missing_refs = [p for p in profile.reference_files if not Path(p).is_file()]
    if reqs["requires_character_consistency"] and profile.primary_characters:
        if not profile.reference_files or len(missing_refs) == len(profile.reference_files):
            checks.append(_item("references", "BLOCKED_DEPENDENCY", "approved anchors missing"))
            blocked.append("references")
        elif missing_refs:
            checks.append(_item("references", "DEGRADED", f"missing {len(missing_refs)} refs"))
        else:
            checks.append(_item("references", "HEALTHY", f"{len(profile.reference_files)} anchors"))
    else:
        checks.append(
            _item(
                "references",
                "HEALTHY",
                f"{len(profile.reference_files)} anchors" if profile.reference_files else "no mandatory anchors",
            )
        )

    out_root = Path(profile.comfy_project_dir) / "output"
    try:
        out_root.mkdir(parents=True, exist_ok=True)
        probe = out_root / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        checks.append(_item("output_writable", "HEALTHY", str(out_root)))
    except OSError as exc:
        checks.append(_item("output_writable", "BLOCKED_DEPENDENCY", str(exc)))
        blocked.append("output_writable")

    try:
        lock = GpuRuntimeLockService(settings)
        checks.append(_item("gpu_lock", "HEALTHY", f"lock dir {lock.lock_dir}"))
    except Exception as exc:  # noqa: BLE001
        checks.append(_item("gpu_lock", "DEGRADED", str(exc)))

    # Aggregate: only production-required checks affect status.
    # optional_* and ipadapter informational HEALTHY never degrade.
    material = [
        c
        for c in checks
        if c["name"]
        not in {"optional_flux_z_image", "ipadapter"}
        and c.get("production_required", True) is not False
    ]
    if blocked or any(c["status"] == "BLOCKED_DEPENDENCY" for c in material):
        status = "BLOCKED_DEPENDENCY"
    elif any(c["status"] == "DEGRADED" for c in material):
        status = "DEGRADED"
    else:
        status = "HEALTHY"

    return {
        "channel": channel,
        "display_name": profile.display_name,
        "engine": f"{channel.upper()}_COMFY",
        "status": status,
        "blocked_dependencies": blocked,
        "requirements": reqs,
        "model_profile": {
            "primary_model": primary,
            "fallback_model": fallback,
            "reference_mode": ref_mode,
        },
        "custom_node_policy": policy.get("policy"),
        "checks": checks,
        "shared_models_root": str(models_root),
        "comfy_project_dir": profile.comfy_project_dir,
    }


def run_all_channel_comfy_health(settings: Settings) -> dict[str, Any]:
    profiles = {cid: check_channel_comfy(settings, cid) for cid in CHANNEL_IDS}
    statuses = [item["status"] for item in profiles.values()]
    if any(s == "BLOCKED_DEPENDENCY" for s in statuses):
        overall = "BLOCKED_DEPENDENCY"
    elif any(s == "DEGRADED" for s in statuses):
        overall = "DEGRADED"
    else:
        overall = "HEALTHY"
    return {"overall": overall, "channels": profiles}
