"""Frozen legacy VideoJob ScenePlan→ProjectManifest converter.

Equivalence-reference only — not used by production paths.
Delete after shared adapter equivalence tests stay green.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from atnos.production.project_manifest import (
    AssetProvenance,
    ProjectManifest,
    QAAction,
    QAResult,
    SceneAsset,
    SceneManifest,
    SceneType,
    TimeRange,
    deterministic_hash,
    file_sha256,
)
from atnos.production.remotion_renderer import REMOTION_RENDERER_VERSION


class _LegacyAdapterError(ValueError):
    """Stand-in for WealthBlueprintVideoJobError used by the frozen converter."""


def _quantitative_chart_payload(chart: dict[str, Any] | None) -> tuple[list[float], list[str], list[str], str]:
    if not isinstance(chart, dict):
        return [], [], [], ""
    values = [float(v) for v in (chart.get("values") or []) if isinstance(v, (int, float))]
    labels = [str(label).strip() for label in (chart.get("labels") or []) if str(label).strip()]
    claim_ids = [str(cid) for cid in (chart.get("claim_ids") or []) if str(cid).strip()]
    unit = str(chart.get("unit") or "").strip()
    if len(values) < 2 or not claim_ids:
        return [], [], [], ""
    if labels and len(labels) != len(values):
        labels = []
    return values, labels[: len(values)], claim_ids, unit


def legacy_scene_plan_to_manifest(
    *,
    job_id: str,
    topic: str,
    script: str,
    plan: dict[str, Any],
    selected_image: Path,
    duration_hint: float,
) -> ProjectManifest:
    now = datetime.now(UTC).isoformat()
    image_hash = file_sha256(selected_image)
    image_asset = SceneAsset(
        asset_id="selected-image-main",
        role="selected_image",
        path=str(selected_image),
        exists=True,
        provenance=AssetProvenance(
            provider="wealth_blueprint_asset_brain",
            model="none",
            workflow="channel_reference_or_fixture",
            prompt="Wealth Blueprint selected still",
            seed=0,
            generation_parameters={"width": 1080, "height": 1920},
            input_hash=deterministic_hash({"job": job_id, "image": image_hash}),
            artifact_hash=image_hash,
        ),
        qa=QAResult(status="passed", action=QAAction.PASS, score=1.0),
    )

    scenes: list[SceneManifest] = []
    plan_scenes = plan.get("scenes") or []
    # Scale plan timings to audio duration if needed
    plan_end = float(plan_scenes[-1]["end_sec"]) if plan_scenes else duration_hint
    scale = (duration_hint / plan_end) if plan_end > 0 else 1.0

    for index, scene in enumerate(plan_scenes):
        start = float(scene["start_sec"]) * scale
        end = float(scene["end_sec"]) * scale
        focus = scene.get("visual_focus") or "statement"
        chart = scene.get("chart") if isinstance(scene.get("chart"), dict) else {}
        chart_values, chart_labels, claim_ids, chart_unit = _quantitative_chart_payload(chart)
        number_value = scene.get("number_value")
        if focus == "number" or number_value:
            scene_type = SceneType.NUMBER
        elif len(chart_values) >= 2 and claim_ids:
            scene_type = SceneType.CHART
        elif index == len(plan_scenes) - 1:
            scene_type = SceneType.CTA
        elif index == 0:
            scene_type = SceneType.HOOK
        elif focus in {"icon", "character"}:
            scene_type = SceneType.IMAGE
        else:
            scene_type = SceneType.HOOK if index == 0 else SceneType.IMAGE

        if scene_type != SceneType.CHART:
            chart_values = []
            chart_labels = []
            claim_ids = []
            chart_unit = ""

        headline = str(scene.get("on_screen_text") or "Wealth Blueprint")[:80]
        # Preserve narration — never invent spoken values to match chart image.
        narration = str(scene.get("narration_excerpt") or headline)

        assets = [image_asset] if scene_type == SceneType.IMAGE else []
        material = {
            "scene_id": scene["scene_id"],
            "scene_type": scene_type.value,
            "timing": [start, end],
            "caption": headline,
            "headline": headline,
            "number_value": number_value,
            "chart_values": chart_values or [],
            "claim_ids": claim_ids,
            "chart_unit": chart_unit,
        }
        scenes.append(
            SceneManifest(
                scene_id=scene["scene_id"],
                scene_type=scene_type,
                timing=TimeRange(start_sec=round(start, 3), end_sec=round(end, 3)),
                narration=narration,
                caption=headline,
                headline=headline,
                overlay_text=headline,
                number_value=number_value,
                chart_values=chart_values or [],
                chart_labels=chart_labels or [],
                chart_unit=chart_unit,
                claim_ids=claim_ids,
                comparison_left=(chart_labels[0] if scene_type == SceneType.CHART and focus == "comparison" and len(chart_labels) >= 2 else ""),
                comparison_right=(chart_labels[1] if scene_type == SceneType.CHART and focus == "comparison" and len(chart_labels) >= 2 else ""),
                input_hash=deterministic_hash(material),
                assets=assets,
            )
        )

    # Ensure at least hook/number/chart/cta coverage for a solid short
    if len(scenes) < 3:
        raise _LegacyAdapterError("Scene plan too short for video job")

    return ProjectManifest(
        manifest_id=f"manifest-{job_id}",
        job_id=job_id,
        project_id="wealth-blueprint",
        account_id="wealth-blueprint",
        topic_id="wb-pipeline",
        topic_title=topic,
        channel="wealth_blueprint",
        language_code="en",
        brand_profile_version="wealth-blueprint-pipeline-v1",
        renderer_version=REMOTION_RENDERER_VERSION,
        workflow_version="wealth_blueprint_video_job_v1",
        voice_version="audio_pipeline",
        status="pipeline_preview",
        current_step="remotion_renderer",
        scenes=scenes,
        created_at=now,
        updated_at=now,
    )
