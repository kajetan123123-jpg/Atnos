"""Validate channel workflows before sending work to Comfy."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from atnos.core.config import Settings
from atnos.production.comfy_channels.models import ChannelComfyProfile, WorkflowSpec
from atnos.production.comfy_channels.profiles import shared_models_root


class WorkflowValidationError(ValueError):
    pass


def validate_workflow_for_generation(
    settings: Settings,
    profile: ChannelComfyProfile,
    workflow: WorkflowSpec,
) -> dict[str, Any]:
    """Fail closed if a known-broken workflow would be sent to Comfy."""
    issues: list[str] = []

    if not workflow.workflow_id or not workflow.workflow_version:
        issues.append("missing workflow_id/version")
    if not workflow.backend_preference:
        issues.append("no backend_preference")
    if workflow.default_width < 256 or workflow.default_height < 256:
        issues.append("invalid default dimensions")

    # Logical workflows compile to programmatic Comfy API graphs — require at least one model path.
    models_root = shared_models_root(settings)
    model_ok = False
    for rel in profile.image_models:
        if (models_root / rel).is_file():
            model_ok = True
            break
    if not model_ok and profile.image_fallback and (models_root / profile.image_fallback).is_file():
        model_ok = True
    if not model_ok:
        issues.append(f"no usable model under shared root {models_root}")

    # Input/output bindings for logical workflows are fixed by ChannelComfyGenerationService
    if not workflow.asset_type:
        issues.append("missing asset_type (output binding)")

    project = Path(profile.comfy_project_dir)
    if not project.is_dir():
        issues.append(f"comfy project dir missing: {project}")

    # Spec JSON on disk is optional but preferred for provenance
    config_wf = (
        Path(settings.app_root) / "config" / "comfy_channels" / profile.channel_id / "workflows" / f"{workflow.workflow_id}.json"
        if settings.app_root
        else None
    )
    spec_on_disk = bool(config_wf and config_wf.is_file())

    if issues:
        raise WorkflowValidationError("; ".join(issues))

    return {
        "ok": True,
        "workflow_id": workflow.workflow_id,
        "workflow_version": workflow.workflow_version,
        "backend_preference": list(workflow.backend_preference),
        "spec_on_disk": spec_on_disk,
        "shared_models_root": str(models_root),
        "custom_nodes_required": [],
        "output_node": "SaveImage (via ComfyUiImageGenerationService)",
        "input_bindings": ["prompt", "negative", "seed", "width", "height", "references"],
    }
