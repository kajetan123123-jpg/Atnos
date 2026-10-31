"""Deterministic adapters → Remotion ProjectManifest (no AI).

Wealth Blueprint Remotion accepts ONLY ProjectManifest.
Internal plans (WB ScenePlan, order visuals) adapt into that contract.

Headline/overlay_text summarize the scene.
caption + caption_words carry spoken narration timings from the captions stage.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from atnos.production.caption_timing import (
    flatten_caption_words,
    subtitle_text_from_words,
    validate_manifest_captions,
    words_for_scene,
)
from atnos.production.channels.wealth_blueprint.layout_registry import layout_to_scene_type
from atnos.production.project_manifest import (
    AssetProvenance,
    BrandSpec,
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
from atnos.production.remotion_renderer import REMOTION_RENDERER_VERSION as RENDERER_VERSION


def _quantitative_chart_payload(chart: Any) -> tuple[list[float], list[str], list[str], str]:
    """Return values/labels/claim_ids/unit only when quantitative chart is source-backed.

    Missing claim/calculation ids blocks a quantitative chart — never invent series or labels.
    """
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


def _forbid_reference_sheet_path(path: Path) -> None:
    norm = str(path).replace("\\", "/").casefold()
    if "channel_references/" in norm:
        raise ValueError(f"reference_sheet path forbidden in production manifest: {path}")


def _build_scene_image_asset(
    *,
    job_id: str,
    scene_id: str,
    image_path: Path,
    meta: dict[str, Any] | None = None,
    provider: str = "wealth_blueprint_asset_brain",
    workflow: str = "production_role_selection",
) -> SceneAsset:
    _forbid_reference_sheet_path(image_path)
    meta = meta or {}
    role_name = str(meta.get("production_role") or "selected_image")
    if role_name == "reference_sheet":
        raise ValueError(f"reference_sheet role forbidden for scene {scene_id}")
    image_hash = file_sha256(image_path)
    fit = str(meta.get("fit") or "contain")
    preserve_alpha = bool(meta.get("preserve_alpha", image_path.suffix.lower() == ".png"))
    return SceneAsset(
        asset_id=str(meta.get("asset_id") or f"selected-image-{scene_id}"),
        role="selected_image",
        path=str(image_path),
        exists=True,
        provenance=AssetProvenance(
            provider=provider,
            model="none",
            workflow=str(meta.get("workflow") or workflow),
            prompt=str(meta.get("function") or "Wealth Blueprint scene still"),
            seed=0,
            generation_parameters={
                "width": 1080,
                "height": 1920,
                "fit": fit,
                "preserve_alpha": preserve_alpha,
                "production_role": role_name,
                "channel": str(meta.get("channel") or "wealth_blueprint"),
                "version": str(meta.get("version") or ""),
                "source_asset_id": str(meta.get("asset_id") or ""),
                "visual_style_id": meta.get("visual_style_id"),
                "visual_style_definition_hash": meta.get("visual_style_definition_hash"),
            },
            input_hash=deterministic_hash({"job": job_id, "scene": scene_id, "image": image_hash}),
            artifact_hash=image_hash,
        ),
        qa=QAResult(status="passed", action=QAAction.PASS, score=1.0),
    )


def scene_plan_to_project_manifest(
    *,
    job_id: str,
    topic: str,
    script: str,
    plan: dict[str, Any],
    selected_image: Path | None = None,
    scene_images: dict[str, Path] | None = None,
    scene_asset_meta: dict[str, dict[str, Any]] | None = None,
    duration_hint: float,
    captions: dict[str, Any] | None = None,
    voice: dict[str, Any] | None = None,
    require_caption_words: bool = False,
    candidate_preview: bool = False,
    illustration_pack_path: Path | None = None,
    visual_style: dict[str, Any] | None = None,
    verified_claims: list[dict] | None = None,
) -> ProjectManifest:
    """WealthBlueprintScenePlan JSON → ProjectManifest."""
    now = datetime.now(UTC).isoformat()
    scene_images = scene_images or {}
    scene_asset_meta = scene_asset_meta or {}
    if selected_image is not None:
        _forbid_reference_sheet_path(Path(selected_image))

    scenes: list[SceneManifest] = []
    plan_scenes = plan.get("scenes") or []
    plan_end = float(plan_scenes[-1]["end_sec"]) if plan_scenes else duration_hint
    scale = (duration_hint / plan_end) if plan_end > 0 else 1.0
    timed_words = flatten_caption_words(captions=captions, voice=voice)

    for index, scene in enumerate(plan_scenes):
        start = float(scene["start_sec"]) * scale
        end = float(scene["end_sec"]) * scale
        layout_id = str(scene.get("layout_id") or "fullscreen_statement")
        focus = scene.get("visual_focus") or "statement"
        chart = scene.get("chart") if isinstance(scene.get("chart"), dict) else {}
        chart_values, chart_labels, claim_ids, chart_unit = _quantitative_chart_payload(chart)
        derived = chart.get('verified_series')
        if derived is not None:
            from atnos.production.chart_series import validate_compound_checkpoints
            validate_compound_checkpoints(derived, verified_claims or [])
        if not claim_ids:
            claim_ids = [str(cid) for cid in (scene.get("claim_ids") or []) if str(cid).strip()]
        number_value = scene.get("number_value")
        scene_type = layout_to_scene_type(
            layout_id,
            visual_focus=str(focus),
            index=int(scene.get("source_scene_index", index)),
            total=int(scene.get("source_scene_count", len(plan_scenes))),
            has_number=bool(number_value),
            has_chart=len(chart_values) >= 2 and bool(claim_ids),
        )
        if scene_type != SceneType.CHART:
            chart_values = []
            chart_labels = []
            claim_ids = []
            chart_unit = ""

        headline = str(scene.get("on_screen_text") or "Wealth Blueprint")
        # Preserve approved narration verbatim — never invent spoken claims to match the chart.
        narration = str(scene.get("narration_excerpt") or headline)

        scene_id = str(scene["scene_id"])
        scene_words = words_for_scene(
            timed_words,
            scene_id=scene_id,
            start_sec=start,
            end_sec=end,
            narration_segment_ids=[str(sid) for sid in (scene.get("narration_segment_ids") or [])],
        )
        caption = subtitle_text_from_words(scene_words) if scene_words else narration

        image_asset = None
        if scene_type == SceneType.IMAGE and not scene.get("visual_story"):
            path = scene_images.get(scene_id) or selected_image
            if path is None or not Path(path).is_file():
                raise ValueError(f"ImageScene {scene_id} missing approved production still")
            image_asset = _build_scene_image_asset(
                job_id=job_id,
                scene_id=scene_id,
                image_path=Path(path),
                meta=scene_asset_meta.get(scene_id),
            )
        assets = [image_asset] if image_asset is not None else []
        story = None
        if scene.get("visual_story"):
            from atnos.production.channels.wealth_blueprint.visual_story import bind_story_audio
            from atnos.production.channels.wealth_blueprint.illustration_pack import load_layers
            story = bind_story_audio(scene["visual_story"], scene_words, start, end)
            assets = load_layers(candidate_preview=candidate_preview, pack_path=illustration_pack_path)
        material = {
            "scene_id": scene_id,
            "scene_type": scene_type.value,
            "layout_id": layout_id,
            "timing": [start, end],
            "caption": caption,
            "headline": headline,
            "caption_words": [word.model_dump(mode="json") for word in scene_words],
            "number_value": number_value,
            "chart_values": chart_values or [],
            "verified_series": derived,
            "claim_ids": claim_ids,
            "chart_unit": chart_unit,
        }
        comparison_left = ""
        comparison_right = ""
        if scene_type == SceneType.CHART and focus == "comparison" and chart_labels and len(chart_labels) >= 2:
            comparison_left = chart_labels[0]
            comparison_right = chart_labels[1]
        from atnos.production.project_manifest import infer_semantic_overlay_kind
        from atnos.production.channels.wealth_blueprint.visual_concepts import (
            infer_visual_concept,
            semantic_overlay_for_concept,
        )

        intent = scene.get("visual_intent") if isinstance(scene.get("visual_intent"), dict) else {}
        concept = str(scene.get("visual_concept") or "") or infer_visual_concept(
            narration,
            headline,
            scene.get("rationale"),
            " ".join(str(t) for t in (scene.get("icon_tags") or [])),
        )
        # Explicit editorial intent wins over keyword inference. Otherwise a closing
        # sentence mentioning a raise silently becomes another mechanism diagram.
        # Relation-backed mechanism beats still keep their diagram.
        relation = intent.get("relation") if isinstance(intent.get("relation"), dict) else {}
        from atnos.production.channels.wealth_blueprint.scene_relation import overlay_kind_for_relation

        relation_overlay = overlay_kind_for_relation(relation)
        explicit_concept = bool(scene.get("visual_concept"))
        editorial = intent.get("scene_mode") in {"statement", "illustration", "character"} and not relation_overlay
        semantic_overlay_kind = None if editorial else (relation_overlay or semantic_overlay_for_concept(concept))  # type: ignore[arg-type]
        if not editorial and not explicit_concept and not semantic_overlay_kind:
            semantic_overlay_kind = infer_semantic_overlay_kind(
                narration,
                headline,
                scene.get("rationale"),
                " ".join(str(t) for t in (scene.get("icon_tags") or [])),
                intent.get("viewer_should_understand"),
                intent.get("visible_change"),
            )
        visual_prompt = ""
        if intent:
            visual_prompt = (
                f"Understand: {intent.get('viewer_should_understand') or ''}. "
                f"Objects: {', '.join(str(x) for x in (intent.get('visible_objects') or []))}. "
                f"Initial: {intent.get('initial_state') or ''}. "
                f"Action: {intent.get('action') or ''} {intent.get('direction') or ''}. "
                f"Final: {intent.get('final_state') or ''}. "
                f"Compliance: {intent.get('compliance_criterion') or ''}. "
                f"Why: {intent.get('why_this_image') or ''}."
            ).strip()[:500]
        animation_presets = [
            str(cue.get("preset_id"))
            for cue in (scene.get("animations") or [])
            if isinstance(cue, dict) and cue.get("preset_id")
        ][:4]
        scenes.append(
            SceneManifest(
                scene_id=scene_id,
                scene_type=scene_type,
                timing=TimeRange(start_sec=round(start, 3), end_sec=round(end, 3)),
                narration=narration,
                caption=caption,
                headline=headline,
                overlay_text=headline,
                caption_words=scene_words,
                number_value=number_value,
                chart_values=chart_values or [],
                verified_series=derived,
                chart_labels=chart_labels or [],
                chart_unit=chart_unit,
                claim_ids=claim_ids if scene_type == SceneType.CHART else [],
                comparison_left=comparison_left,
                comparison_right=comparison_right,
                semantic_overlay_kind=semantic_overlay_kind,
                layout_id=layout_id,
                animation_presets=animation_presets,
                visual_prompt=visual_prompt,
                visual_story=story,
                visual_relation=relation or None,
                input_hash=deterministic_hash(material),
                assets=assets,
            )
        )

    if len(scenes) < (1 if candidate_preview else 3):
        raise ValueError("Scene plan too short for Remotion ProjectManifest")

    brand = BrandSpec(captionStyle="plain_phrase")
    try:
        from atnos.production.channels.wealth_blueprint.design_system import brand_tokens_for_manifest

        brand = BrandSpec(**brand_tokens_for_manifest())
    except Exception:
        pass
    if visual_style:
        brand = BrandSpec(**{**brand.model_dump(mode="json"), **visual_style["brand"]})

    manifest = ProjectManifest(
        review_mode="candidate_preview" if candidate_preview else "production",
        manifest_id=f"manifest-{job_id}",
        job_id=job_id,
        project_id="wealth-blueprint",
        account_id="wealth-blueprint",
        topic_id="wb-pipeline",
        topic_title=topic,
        channel="wealth_blueprint",
        language_code="en",
        brand_profile_version="wealth_blueprint_visual_v1",
        visual_style_id=visual_style.get("id") if visual_style else None,
        visual_style_version=visual_style.get("version") if visual_style else None,
        visual_style_definition_hash=visual_style.get("definition_hash") if visual_style else None,
        renderer_version=RENDERER_VERSION,
        workflow_version="wealth_blueprint_remotion_order_v1",
        voice_version="order_or_f5",
        brand=brand,
        status="pipeline_preview",
        current_step="remotion_renderer",
        scenes=scenes,
        calculation_claims=verified_claims or [],
        created_at=now,
        updated_at=now,
    )
    validate_manifest_captions(manifest, require_words=require_caption_words)
    return manifest


def order_artifacts_to_project_manifest(
    *,
    job_id: str,
    topic_title: str,
    script: dict[str, Any],
    voice: dict[str, Any],
    visuals: dict[str, Any],
    selected_image: Path | None = None,
    scene_images: dict[str, Path] | None = None,
    scene_asset_meta: dict[str, dict[str, Any]] | None = None,
    captions: dict[str, Any] | None = None,
    require_caption_words: bool = False,
    visual_style: dict[str, Any] | None = None,
) -> ProjectManifest:
    """Order-system artifacts (script/voice/visuals/captions) → ProjectManifest.

    Uses narration segments + captions timings; does not invent financial numbers.
    Headline may summarize a scene; it must not replace subtitle text.
    """
    segments = [s for s in (script.get("segments") or []) if isinstance(s, dict)]
    timestamps = {
        str(item.get("segment_id")): item
        for item in (voice.get("timestamps") or [])
        if isinstance(item, dict) and item.get("segment_id") is not None
    }
    visual_scenes = [s for s in (visuals.get("scenes") or []) if isinstance(s, dict)]
    timed_words = flatten_caption_words(captions=captions, voice=voice)
    now = datetime.now(UTC).isoformat()
    scene_images = scene_images or {}
    scene_asset_meta = scene_asset_meta or {}
    if selected_image is not None:
        _forbid_reference_sheet_path(Path(selected_image))

    scenes: list[SceneManifest] = []
    cursor = 0.0
    for index, segment in enumerate(segments):
        scene_id = str(segment.get("id") or f"s{index + 1:02d}")
        timing = timestamps.get(scene_id) or {}
        visual = visual_scenes[index] if index < len(visual_scenes) else {}
        start = float(timing.get("start_sec") or cursor)
        end = float(timing.get("end_sec") or (start + float(timing.get("duration_sec") or 2.5)))
        if end <= start:
            end = start + 2.5
        cursor = end
        narration = str(segment.get("text") or "").strip() or "Wealth Blueprint"
        headline = str(visual.get("headline") or visual.get("overlay_text") or narration.split(".")[0])[:80]
        overlay_text = str(visual.get("overlay_text") or headline)[:80]
        scene_words = words_for_scene(
            timed_words,
            scene_id=scene_id,
            start_sec=start,
            end_sec=end,
        )
        caption = subtitle_text_from_words(scene_words) if scene_words else narration
        layout_hint = str(visual.get("layout_id") or visual.get("layout") or "")
        chart_values = [float(v) for v in (visual.get("chart_values") or []) if isinstance(v, (int, float))]
        chart_labels = [str(x).strip() for x in (visual.get("chart_labels") or []) if str(x).strip()]
        claim_ids = [
            str(cid) for cid in (visual.get("claim_ids") or segment.get("claim_ids") or []) if str(cid).strip()
        ]
        chart_unit = str(visual.get("chart_unit") or visual.get("unit") or "").strip()
        if len(chart_values) < 2 or not claim_ids:
            chart_values = []
            chart_labels = []
        elif chart_labels and len(chart_labels) != len(chart_values):
            chart_labels = []
        scene_type = layout_to_scene_type(
            layout_hint or "fullscreen_statement",
            visual_focus=str(visual.get("visual_focus") or "statement"),
            index=index,
            total=len(segments),
            has_number=bool(visual.get("number_value")),
            has_chart=len(chart_values) >= 2 and bool(claim_ids),
        )
        path = scene_images.get(scene_id) or selected_image
        image_asset = None
        if path is not None and Path(path).is_file():
            image_asset = _build_scene_image_asset(
                job_id=job_id,
                scene_id=scene_id,
                image_path=Path(path),
                meta=scene_asset_meta.get(scene_id),
                provider="order_assets",
                workflow="order_selected_still",
            )
        if scene_type == SceneType.CHART and (len(chart_values) < 2 or not claim_ids):
            # Fall back if chart data is not source-backed
            scene_type = SceneType.IMAGE if image_asset else SceneType.HOOK
            chart_values = []
            chart_labels = []
            claim_ids = []
            chart_unit = ""
        if scene_type != SceneType.CHART:
            chart_values = []
            chart_labels = []
            claim_ids = []
            chart_unit = ""
        assets = [image_asset] if image_asset and scene_type == SceneType.IMAGE else []
        material = {
            "scene_id": scene_id,
            "scene_type": scene_type.value,
            "timing": [start, end],
            "caption": caption,
            "headline": headline,
            "narration": narration,
            "caption_words": [word.model_dump(mode="json") for word in scene_words],
            "claim_ids": claim_ids,
            "chart_unit": chart_unit,
        }
        scenes.append(
            SceneManifest(
                scene_id=scene_id,
                scene_type=scene_type,
                timing=TimeRange(start_sec=round(start, 3), end_sec=round(end, 3)),
                narration=narration,
                caption=caption,
                headline=headline,
                overlay_text=overlay_text,
                caption_words=scene_words,
                number_value=str(visual["number_value"]) if visual.get("number_value") is not None else None,
                chart_values=chart_values,
                chart_labels=chart_labels[: len(chart_values)] if chart_values else [],
                chart_unit=chart_unit,
                claim_ids=claim_ids,
                comparison_left=str(visual.get("comparison_left") or ""),
                comparison_right=str(visual.get("comparison_right") or ""),
                input_hash=deterministic_hash(material),
                assets=assets,
            )
        )

    if len(scenes) < 1:
        raise ValueError("Order artifacts produced no Remotion scenes")

    brand = BrandSpec(captionStyle="plain_phrase")
    try:
        from atnos.production.channels.wealth_blueprint.design_system import brand_tokens_for_manifest

        brand = BrandSpec(**brand_tokens_for_manifest())
    except Exception:
        pass
    if visual_style:
        brand = BrandSpec(**{**brand.model_dump(mode="json"), **visual_style["brand"]})

    manifest = ProjectManifest(
        manifest_id=f"manifest-{job_id}",
        job_id=job_id,
        project_id="wealth-blueprint",
        account_id="wealth-blueprint",
        topic_id="wb-order",
        topic_title=topic_title,
        channel="wealth_blueprint",
        language_code=str(script.get("language_code") or "en"),
        brand_profile_version="wealth_blueprint_visual_v1",
        visual_style_id=visual_style.get("id") if visual_style else None,
        visual_style_version=visual_style.get("version") if visual_style else None,
        visual_style_definition_hash=visual_style.get("definition_hash") if visual_style else None,
        renderer_version=RENDERER_VERSION,
        workflow_version="wealth_blueprint_remotion_order_v1",
        voice_version=str(voice.get("provider") or "order_voice"),
        brand=brand,
        status="order_remotion",
        current_step="remotion_renderer",
        scenes=scenes,
        created_at=now,
        updated_at=now,
    )
    validate_manifest_captions(manifest, require_words=require_caption_words)
    return manifest
def apply_native_illustration_system(manifest, system):
    """Opt into a real scene family only when every non-CTA scene is supported."""
    if system != 'wb_planning_desk_painted_v1':
        raise ValueError('Unknown native WB illustration system')
    from atnos.production.project_manifest import BrandSpec, SceneType
    from atnos.production.channels.wealth_blueprint.planning_desk_contract import planning_desk_action
    for scene in manifest.scenes:
        if scene.scene_type == SceneType.CTA:
            continue
        relation = scene.visual_relation or {}
        if (relation.get('kind') != 'reserve_readiness'
                or relation.get('stage') not in {'identify', 'separate', 'review'}
                or any(o.get('role') == 'goal' and o.get('label') == 'PLANNED BILL FUND'
                       for o in relation.get('objects', []))):
            raise ValueError('Unsupported painted planning scene: '+scene.scene_id)
        if relation.get('planning_desk_action') != planning_desk_action(relation['stage'], scene.narration):
            raise ValueError('Painted planning action does not match narration: '+scene.scene_id)
    manifest.brand = BrandSpec.model_validate({**manifest.brand.model_dump(mode='json'),
                                             'illustrationSystem':system})
    return manifest

