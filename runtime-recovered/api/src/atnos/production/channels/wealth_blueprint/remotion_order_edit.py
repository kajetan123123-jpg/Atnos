"""Remotion branch for Wealth Blueprint Production Orders (produce_edit).

Selected when production.renderer == \"remotion\" (REMOTION_REQUIRED — fail-closed).
Editorial remains the default profile path; there is NO silent fallback from remotion.
Sound design defaults ON; disable via artifacts['remotion_options']['enable_sound']=false.
Still resolution: per-scene approved production roles only (never reference sheets).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from atnos.production.channels.wealth_blueprint.health import run_wealth_blueprint_video_engine_health
from atnos.production.channels.wealth_blueprint.manifest_adapters import (
    order_artifacts_to_project_manifest,
    scene_plan_to_project_manifest,
)
from atnos.production.channels.wealth_blueprint.scene_production_assets import (
    WAITING_FOR_ASSETS,
    manifest_uses_reference_sheet,
    resolve_scene_production_assets,
)
from atnos.production.channels.wealth_blueprint.planner import WealthBlueprintScenePlanner
from atnos.production.channels.wealth_blueprint.remotion_mode import REMOTION_REQUIRED
from atnos.production.channels.wealth_blueprint.renderer_boundary import RENDERER_BOUNDARY
from atnos.production.channels.wealth_blueprint.scene_plan_models import NarrationSegment, ScenePlannerInput
from atnos.production.channels.wealth_blueprint.video_qc import run_wealth_blueprint_qc
from atnos.production.media_tools import resolve_ffmpeg, resolve_ffprobe
from atnos.production.models import StepResult
from atnos.production.order_store import file_hash
from atnos.production.project_manifest import load_project_manifest, save_project_manifest
from atnos.production.project_profile import content_hash
from atnos.production.qa_gate import probe_media
from atnos.production.remotion_renderer import (
    RemotionRendererError,
    probe_remotion_runtime,
    render_project_manifest,
)
from atnos.production.steps.assembler import _mux_remotion_video
from atnos.production.steps.common import write_json


class RemotionRequiredError(RuntimeError):
    """Fail-closed Remotion order failure — must not fall back to editorial."""

    def __init__(self, message: str):
        super().__init__(f"{REMOTION_REQUIRED}: {message}")


class SceneAssetsNotReady(RemotionRequiredError):
    """Required scene assets are missing or still awaiting review."""

    def __init__(self, waiting: list[dict[str, Any]]):
        self.waiting = waiting
        super().__init__(f"{WAITING_FOR_ASSETS}: {json.dumps(waiting, ensure_ascii=False)[:600]}")


def renderer_source_fingerprint(package_root: Path | None = None) -> str:
    """Bind render checkpoints to all real renderer sources, captions and fonts.

    Lock/package files bind dependencies without reading node_modules. Recursive
    src discovery also covers motion, data-viz and charts imported by scenes.
    """
    from atnos.production import remotion_renderer as renderer

    root = (package_root or renderer._renderer_package_root()).resolve()
    required = ("package.json", "pnpm-lock.yaml", "src/Root.tsx", "src/contracts.ts",
                "src/design.tsx", "src/scenes.tsx", "src/WealthBlueprintShort.tsx", "src/typography.tsx")
    for name in required:
        if not (root / name).is_file():
            raise RemotionRequiredError(f"Renderer source missing for cache identity: {root / name}")
    sources = {root / name for name in required}
    sources.update(root / name for name in ("tsconfig.json", "remotion.config.ts") if (root / name).is_file())
    for folder in ("src", "scripts"):
        sources.update(path for path in (root / folder).rglob("*")
                       if path.is_file() and path.suffix.lower() in {".ts", ".tsx", ".css", ".json"}
                       and "node_modules" not in path.relative_to(root).parts)
    hashes = {path.relative_to(root).as_posix(): file_hash(path) for path in sorted(sources)}
    return content_hash({"sources": hashes,
                         "python_renderer_sha256": file_hash(Path(renderer.__file__)),
                         "fonts": renderer._renderer_font_assets(),
                         "native_opt_in": os.getenv("ATNOS_USE_NATIVE_REMOTION", "").strip().lower() in {"1", "true", "yes", "on"}})


def stabilize_manifest_revision_dates(path: Path, manifest):
    """Identical resumed input must keep its manifest and render cache identity."""
    if not path.is_file():
        return manifest
    previous = load_project_manifest(path, verify_hash=True)
    if previous.job_id != manifest.job_id:
        raise RemotionRequiredError("Existing ProjectManifest belongs to another order")
    old, new = previous.model_dump(mode="json"), manifest.model_dump(mode="json")
    for payload in (old, new):
        for key in ("content_hash", "created_at", "updated_at"):
            payload.pop(key, None)
    dates = {"created_at": previous.created_at}
    if old == new:
        dates["updated_at"] = previous.updated_at
    return manifest.model_copy(update=dates)


def _result(path: Path, payload: dict[str, Any], files=()):
    result = write_json(path, payload)
    return StepResult(result.artifact_name, path, payload, result.message, tuple(files))


def _step_payload(result: Any) -> dict[str, Any]:
    if isinstance(result, StepResult):
        return dict(result.payload or {})
    if isinstance(result, dict):
        return result
    payload = getattr(result, "payload", None)
    return dict(payload) if isinstance(payload, dict) else {}


def _resolve_still(context, settings) -> Path:
    """Legacy single-still helper — production film path must use resolve_scene_production_assets.

    Kept for unit tests that monkeypatch this symbol. Never returns channel reference sheets.
    """
    visuals = context.artifacts.get("visuals") or {}
    for asset in (visuals.get("assets") or {}).values():
        if not isinstance(asset, dict):
            continue
        role = str(asset.get("production_role") or asset.get("role") or "").casefold()
        if role in {"reference_sheet", "reference", "contact_sheet"}:
            continue
        staged = Path(str(asset.get("staged_path") or asset.get("file_path") or ""))
        norm = str(staged).replace("\\", "/").casefold()
        if "channel_references/" in norm:
            continue
        if staged.is_file() and staged.suffix.lower() in {".png", ".webp"}:
            return staged

    try:
        from atnos.production.asset_brain.models import AssetQuery
        from atnos.production.asset_brain.service import WealthBlueprintAssetBrain

        brain = WealthBlueprintAssetBrain(settings)
        for asset_type, character, role in (
            ("character", "alex", "character_cutout"),
            ("icon", None, "icon"),
            ("background", None, "background"),
        ):
            query = AssetQuery(
                type=asset_type,  # type: ignore[arg-type]
                character=character,
                production_role=role,  # type: ignore[arg-type]
                require_approved=True,
                exclude_reference_sheets=True,
            )
            result = brain.select_best(query)
            if result.status == "found" and result.match is not None:
                asset = result.match.asset
                if not asset.is_production_selectable():
                    continue
                path = brain.resolve_file(asset)
                if path.is_file():
                    return path
    except Exception:
        pass

    raise RemotionRequiredError(
        f"{WAITING_FOR_ASSETS}: no approved production still "
        "(character_cutout / icon / background) — reference sheets are not allowed"
    )


def _mix_voice_with_sound(
    *,
    voice_path: Path,
    duration: float,
    plan_dict: dict[str, Any] | None,
    job_id: str,
    artifact_dir: Path,
) -> tuple[Path, dict[str, Any]]:
    """Optional sound mix (opt-in). Not part of Phase 1 canonical Remotion proof."""
    from atnos.production.sound_design import (
        MusicBedRequest,
        SoundEventRequest,
        VoiceTrackSpec,
        WealthBlueprintSoundDesignEngine,
        render_mix_plan,
    )

    events: list[SoundEventRequest] = []
    if plan_dict:
        for scene in plan_dict.get("scenes") or []:
            for cue in scene.get("sound_events") or []:
                try:
                    events.append(
                        SoundEventRequest(
                            event=str(cue.get("event") or "soft_whoosh"),  # type: ignore[arg-type]
                            at_sec=float(cue.get("at_sec") or 0.0),
                            priority=int(cue.get("priority") or 0),
                        )
                    )
                except Exception:
                    continue
    if len(events) < 3:
        events = [
            SoundEventRequest(event="soft_whoosh", at_sec=0.1, priority=2),
            SoundEventRequest(event="pop", at_sec=max(1.0, duration * 0.35), priority=3),
            SoundEventRequest(event="rise", at_sec=max(2.0, duration * 0.7), priority=3),
        ]
    engine = WealthBlueprintSoundDesignEngine()
    plan_mix = engine.plan(
        voice=VoiceTrackSpec(file_path=str(voice_path), duration_sec=max(duration, 0.5)),
        events=events[:8],
        music=MusicBedRequest(event="bed_editorial", gain_db=-18.0),
        seed=job_id,
    )
    plan_path = artifact_dir / "sound_mix_plan.json"
    plan_path.write_text(plan_mix.model_dump_json(indent=2) + "\n", encoding="utf-8")
    notes: dict[str, Any] = {"wired": True, "plan": str(plan_path), "clips": len(plan_mix.clips)}
    if not plan_mix.clips:
        notes["executed"] = False
        return voice_path, notes
    mixed = artifact_dir / "assets" / "sound_mix.aac"
    try:
        notes["mix"] = render_mix_plan(plan_mix, mixed)
        notes["executed"] = True
        return mixed, notes
    except Exception as exc:  # noqa: BLE001
        notes["executed"] = False
        notes["error"] = str(exc)[:400]
        return voice_path, notes


def _preflight_remotion(settings) -> dict[str, Any]:
    """Node + Remotion package + browser + FFmpeg + ffprobe (+ health matrix)."""
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        raise RemotionRequiredError("FFmpeg unavailable")
    ffprobe = resolve_ffprobe(ffmpeg)
    if not ffprobe:
        raise RemotionRequiredError("ffprobe unavailable")

    runtime = probe_remotion_runtime()
    if not runtime.get("ready") and not runtime.get("browser_compatibility_ready"):
        raise RemotionRequiredError(
            f"Remotion runtime not ready: {runtime.get('reason') or runtime.get('status')}"
        )

    report = run_wealth_blueprint_video_engine_health(settings, require_comfy=False, require_whisper=False)
    remotion_check = next((c for c in report.get("checks") or [] if c.get("name") == "remotion"), None)
    ffmpeg_check = next((c for c in report.get("checks") or [] if c.get("name") == "ffmpeg"), None)
    if ffmpeg_check and ffmpeg_check.get("status") != "ok":
        raise RemotionRequiredError(f"FFmpeg health failed: {ffmpeg_check.get('detail')}")
    if remotion_check and remotion_check.get("status") == "BLOCKED_DEPENDENCY":
        raise RemotionRequiredError(f"Remotion blocked: {remotion_check.get('detail')}")

    blocked = [name for name in (report.get("blocked_dependencies") or []) if name in {"ffmpeg", "remotion"}]
    if blocked:
        raise RemotionRequiredError(f"preflight blocked: {', '.join(blocked)}")

    return {
        "status": report.get("status"),
        "mode": REMOTION_REQUIRED,
        "renderer_boundary": RENDERER_BOUNDARY,
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "remotion_runtime": runtime,
        "health": report,
    }


async def produce_edit_remotion(executor) -> dict[str, Any]:
    """Order artifacts → ProjectManifest → Remotion → mux → technical QC → edit.json.

    REMOTION_REQUIRED: any failure raises RemotionRequiredError / RuntimeError — never editorial.
    """
    context = executor.context
    settings = getattr(getattr(executor, "store", None), "settings", None) or getattr(executor, "settings", None)
    if settings is None:
        raise RemotionRequiredError("executor.settings / executor.store.settings missing")
    options = context.artifacts.get("remotion_options") if isinstance(context.artifacts.get("remotion_options"), dict) else {}
    options_path = context.artifact_dir / "remotion_options.json"
    if options_path.is_file():
        try:
            disk = json.loads(options_path.read_text(encoding="utf-8"))
            if isinstance(disk, dict):
                options = {**disk, **options}
        except Exception:
            pass
    # Phase 12+: sound mix on by default for Remotion production orders; tests may set False.
    enable_sound = True if "enable_sound" not in options else bool(options.get("enable_sound"))
    options = {**options, "enable_sound": enable_sound}
    context.artifacts["remotion_options"] = options

    try:
        health = _preflight_remotion(settings)
    except RemotionRequiredError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RemotionRequiredError(f"preflight failed: {exc}") from exc

    script = context.artifacts["script"]
    voice = context.artifacts["voice"]
    visuals = context.artifacts.get("visuals") or {}
    captions = context.artifacts.get("captions") if isinstance(context.artifacts.get("captions"), dict) else {}
    # Prefer the Production Order brief — channel_name alone hides host requirements (e.g. Alex).
    order_request = context.artifacts.get("production_order") or {}
    if not isinstance(order_request, dict):
        order_request = {}
    frozen_profile = context.artifacts.get("project_profile") or {}
    visual_style = frozen_profile.get("visual_style") if isinstance(frozen_profile, dict) else None
    if order_request.get("visual_style_id") and (
        not isinstance(visual_style, dict) or visual_style.get("id") != order_request["visual_style_id"]
    ):
        raise RemotionRequiredError("Order visual_style_id does not match its frozen profile")
    topic = str(
        order_request.get("topic")
        or script.get("topic")
        or script.get("title")
        or (context.artifacts.get("project_profile") or {}).get("channel_name")
        or "Wealth Blueprint"
    ).strip()
    order_direction = str(order_request.get("direction") or "").strip()
    order_notes = str(order_request.get("optional_notes") or "").strip()
    job_id = str(getattr(context, "job_id", None) or context.artifact_dir.name)

    segments_payload = []
    for item in voice.get("timestamps") or []:
        if not isinstance(item, dict):
            continue
        seg_id = str(item.get("segment_id") or "")
        text = str(item.get("text") or "").strip()
        start = float(item.get("start_sec") or 0.0)
        end = float(item.get("end_sec") or (start + 2.2))
        if not text or end <= start:
            continue
        source_segment = next((s for s in script.get("segments", [])
                               if isinstance(s, dict) and s.get("id") == seg_id), {})
        segments_payload.append(
            {"id": seg_id or f"s{len(segments_payload)+1:02d}", "text": text,
             "start": start, "end": end,
             "on_screen": str(source_segment.get("on_screen") or ""),
             "visual_intent": str(source_segment.get("visual_intent") or "")}
        )

    script_text = " ".join(
        str(s.get("text") or "") for s in (script.get("segments") or []) if isinstance(s, dict)
    ).strip()
    if not script_text:
        script_text = str(script.get("full_text") or script.get("text") or topic)

    plan_dict: dict[str, Any] | None = None
    prefer_order_adapter = bool(options.get("prefer_order_adapter"))
    if segments_payload and not prefer_order_adapter:
        try:
            segments = [NarrationSegment.model_validate(item) for item in segments_payload]
            script_claims = [c for c in (script.get("claims") or []) if isinstance(c, dict)]
            segment_claim_map = {
                str(seg.get("id")): [str(cid) for cid in (seg.get("claim_ids") or []) if cid]
                for seg in (script.get("segments") or [])
                if isinstance(seg, dict) and seg.get("id")
            }
            plan = WealthBlueprintScenePlanner(settings).plan(
                ScenePlannerInput(
                    script=script_text,
                    segments=segments,
                    profile={
                        "channel": "wealth_blueprint",
                        "topic": topic,
                        "direction": order_direction,
                        "optional_notes": order_notes,
                        "disable_visual_story": bool(visual_style),
                        "native_illustration_system": frozen_profile.get('native_illustration_system'),
                    },
                    design_system={"id": "wealth_blueprint_style_v1", "visual_profile_id": "wealth_blueprint_visual_v1",
                                   "visual_style_id": visual_style.get("id") if visual_style else None},
                    available_asset_types=["character", "icon", "charts", "background"],
                    mode="deterministic",
                    claims=script_claims,
                    segment_claim_map=segment_claim_map,
                )
            )
            plan_dict = plan.model_dump(mode="json")
            # Do not overwrite asset_planning's scene_plan.json — package verifies those hashes.
            (context.artifact_dir / "remotion_scene_plan.json").write_text(
                json.dumps(plan_dict, indent=2) + "\n", encoding="utf-8"
            )
        except Exception as exc:
            raise RemotionRequiredError(f"Scene planning failed; no generic fallback: {exc}") from exc

    duration = float(voice.get("duration_seconds") or voice.get("duration_sec") or 0.0)
    has_caption_evidence = bool(
        (isinstance(captions.get("groups"), list) and captions.get("groups"))
        or (isinstance(voice.get("word_timestamps"), list) and voice.get("word_timestamps"))
    )

    # Per-scene production assets (never reference sheets). Plan-driven when available.
    if plan_dict is not None:
        selection_plan = plan_dict
    else:
        fallback_scenes: list[dict[str, Any]] = []
        ts_map = {
            str(item.get("segment_id")): item
            for item in (voice.get("timestamps") or [])
            if isinstance(item, dict)
        }
        for index, seg in enumerate(script.get("segments") or []):
            if not isinstance(seg, dict):
                continue
            sid = str(seg.get("id") or f"s{index + 1:02d}")
            timing = ts_map.get(sid) or {}
            start = float(timing.get("start_sec") or index * 2.5)
            end = float(timing.get("end_sec") or (start + 2.5))
            # Alternate: one alex-explain scene mid-beat, else icon/object without Alex.
            use_alex = index == 1
            fallback_scenes.append(
                {
                    "scene_id": sid,
                    "start_sec": start,
                    "end_sec": end,
                    "layout_id": (
                        "character_left_text_right"
                        if use_alex
                        else ("icon_explainer" if index else "fullscreen_statement")
                    ),
                    "visual_focus": "character" if use_alex else ("icon" if index else "statement"),
                    "on_screen_text": str(seg.get("text") or topic)[:80],
                    "narration_excerpt": str(seg.get("text") or ""),
                    "use_alex": use_alex,
                    "alex_pose": "explaining" if use_alex else None,
                    "asset_needs": (
                        [
                            {
                                "type": "character",
                                "character": "alex",
                                "pose": "explaining",
                                "tags": ["finance", "host"],
                                "required": True,
                            }
                        ]
                        if use_alex
                        else [{"type": "icon", "tags": ["savings", "finance"], "required": True}]
                    ),
                }
            )
        selection_plan = {"scenes": fallback_scenes}

    from atnos.production.wb_image_api import load_image_lane
    image_api_lane = load_image_lane(settings, context.job_id)
    asset_plan = resolve_scene_production_assets(
        plan=selection_plan,
        settings=settings,
        artifact_dir=context.artifact_dir,
        visuals=visuals if isinstance(visuals, dict) else {},
        allow_generate=False if image_api_lane else (bool(visual_style) or bool(options.get("allow_asset_generate", False))),
        **({"visual_style": visual_style} if visual_style else {}),
    )
    (context.artifact_dir / "scene_production_assets.json").write_text(
        json.dumps(asset_plan.as_dict(), indent=2) + "\n", encoding="utf-8"
    )
    if asset_plan.status == "waiting":
        raise SceneAssetsNotReady(asset_plan.waiting)

    from atnos.production.channels.wealth_blueprint.geometric_assets import (
        is_geometric_placeholder_asset,
        looks_like_geometric_raster,
    )

    geometric_hits: list[str] = []
    for sid, row in asset_plan.assignments.items():
        if is_geometric_placeholder_asset(
            {"id": row.asset_id, "file_path": row.source_path},
            resolve_path=Path(row.source_path),
        ) or looks_like_geometric_raster(Path(row.source_path)):
            geometric_hits.append(f"{sid}:{row.asset_id}")
    if geometric_hits:
        raise RemotionRequiredError(
            "geometric_placeholder_blocked_in_film: " + ", ".join(geometric_hits)
        )

    scene_images = {sid: row.path for sid, row in asset_plan.assignments.items()}
    scene_asset_meta = {
        sid: {
            "asset_id": row.asset_id,
            "production_role": row.production_role,
            "channel": row.channel,
            "version": row.version,
            "preserve_alpha": row.preserve_alpha,
            "fit": row.fit,
            "function": row.function,
            "workflow": "production_role_selection",
            "visual_style_id": visual_style.get("id") if visual_style else None,
            "visual_style_definition_hash": visual_style.get("definition_hash") if visual_style else None,
        }
        for sid, row in asset_plan.assignments.items()
    }
    # Compatibility still path for older package checks (first assignment only).
    still = context.artifact_dir / "assets" / "selected_image.png"
    still.parent.mkdir(parents=True, exist_ok=True)
    if scene_images:
        first = next(iter(scene_images.values()))
        still.write_bytes(Path(first).read_bytes())
    else:
        # No ImageScenes — write a tiny transparent placeholder so packages that expect the file stay intact.
        from PIL import Image as _Image

        _Image.new("RGBA", (1080, 1920), (17, 17, 17, 255)).save(still)

    try:
        if plan_dict is not None:
            from atnos.production.channels.wealth_blueprint.visual_decision import (
                composition_flags,
                storyboard_blocks_render,
            )

            blocked = storyboard_blocks_render(plan_dict)
            if blocked:
                raise RemotionRequiredError(blocked)
            (context.artifact_dir / "composition_report.json").write_text(
                json.dumps({"flags": composition_flags(plan_dict)}, indent=2) + "\n",
                encoding="utf-8",
            )
            manifest = scene_plan_to_project_manifest(
                job_id=job_id,
                topic=topic,
                script=script_text,
                plan=plan_dict,
                verified_claims=script.get('claims') or [],
                selected_image=still if scene_images else None,
                scene_images=scene_images,
                scene_asset_meta=scene_asset_meta,
                duration_hint=max(duration, 1.0),
                captions=captions,
                voice=voice,
                require_caption_words=has_caption_evidence,
                visual_style=visual_style,
            )
        else:
            manifest = order_artifacts_to_project_manifest(
                job_id=job_id,
                topic_title=topic,
                script=script,
                voice=voice,
                visuals=visuals,
                selected_image=still if scene_images else None,
                scene_images=scene_images,
                scene_asset_meta=scene_asset_meta,
                captions=captions,
                require_caption_words=has_caption_evidence,
                visual_style=visual_style,
            )
    except ValueError as exc:
        if str(exc).startswith(f"{WAITING_FOR_ASSETS}:"):
            raise SceneAssetsNotReady([{
                "scene_id": "manifest",
                "acquisition": {"status": "missing"},
                "reason": str(exc),
            }]) from exc
        raise RemotionRequiredError(f"ProjectManifest adapter failed: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise RemotionRequiredError(f"ProjectManifest adapter failed: {exc}") from exc

    from atnos.production.remotion_quality_repair import apply_manifest_quality_repairs
    manifest = apply_manifest_quality_repairs(executor, manifest)
    presentation = context.artifacts['project_profile'].get('visual_presentation')
    if order_request.get('visual_presentation') != presentation:
        raise RemotionRequiredError('Order visual_presentation does not match its frozen profile')
    if presentation:
        manifest.brand = manifest.brand.model_copy(update={'presentation': presentation})
    illustration = context.artifacts['project_profile'].get('native_illustration_system')
    if order_request.get('native_illustration_system') != illustration:
        raise RemotionRequiredError('Order native illustration does not match its frozen profile')
    if illustration:
        from atnos.production.channels.wealth_blueprint.manifest_adapters import apply_native_illustration_system
        manifest = apply_native_illustration_system(manifest, illustration)

    from atnos.production.presenter_variant import apply_variant
    manifest = apply_variant(executor, manifest)
    ref_hits = manifest_uses_reference_sheet(manifest)
    if ref_hits:
        raise RemotionRequiredError(f"reference_sheet leaked into manifest scenes: {', '.join(ref_hits)}")

    manifest_path = context.artifact_dir / "project_manifest.json"
    try:
        manifest = stabilize_manifest_revision_dates(manifest_path, manifest)
        save_project_manifest(manifest_path, manifest)
        # Round-trip validation (hash + schema)
        load_project_manifest(manifest_path, verify_hash=True)
    except Exception as exc:  # noqa: BLE001
        raise RemotionRequiredError(f"ProjectManifest validation failed: {exc}") from exc

    silent = context.artifact_dir / "assets" / "renderer_silent.mp4"
    silent.parent.mkdir(parents=True, exist_ok=True)
    source_fingerprint = renderer_source_fingerprint()

    async def render():
        try:
            remotion = await asyncio.to_thread(render_project_manifest, manifest_path, silent)
        except RemotionRendererError as exc:
            raise RemotionRequiredError(f"Remotion renderer error: {exc}") from exc
        if not remotion.get("ok"):
            raise RemotionRequiredError(f"Remotion render failed: {remotion.get('reason') or remotion}")
        if renderer_source_fingerprint() != source_fingerprint:
            raise RemotionRequiredError("Renderer sources changed during rendering; the output cannot satisfy its checkpoint identity")
        evidence_paths = [silent]
        progress_log = (remotion.get("renderer") or {}).get("progress_log")
        if progress_log:
            progress_path = Path(progress_log)
            if progress_path.is_file():
                evidence_paths.append(progress_path)
        return _result(context.artifact_dir / "remotion_result.json", remotion, evidence_paths)

    remotion = await executor.run(
        "remotion_render",
        {
            "manifest_sha256": file_hash(manifest_path),
            "renderer_source_fingerprint": source_fingerprint,
            "mode": REMOTION_REQUIRED,
            "renderer_boundary": RENDERER_BOUNDARY,
        },
        render,
    )
    remotion_payload = _step_payload(remotion)

    voice_path = Path(str(voice.get("output_file") or voice.get("audio_file") or ""))
    if not voice_path.is_file():
        raise RemotionRequiredError("Voice audio missing for Remotion mux")

    sound_notes: dict[str, Any] = {"wired": False, "skipped": True, "reason": "phase1_no_sound"}
    audio_for_mux = voice_path
    if enable_sound:
        audio_for_mux, sound_notes = await asyncio.to_thread(
            _mix_voice_with_sound,
            voice_path=voice_path,
            duration=max(duration, 0.5),
            plan_dict=plan_dict,
            job_id=job_id,
            artifact_dir=context.artifact_dir,
        )
    (context.artifact_dir / "sound_mix_notes.json").write_text(
        json.dumps(sound_notes, indent=2, default=str) + "\n", encoding="utf-8"
    )

    final = context.artifact_dir / "assets" / "final_short.mp4"

    def mux():
        result = _mux_remotion_video(
            raw_video_path=silent,
            audio_path=audio_for_mux,
            output_path=final,
            target_lufs=-14.0,
        )
        if not result.get("ok"):
            raise RemotionRequiredError(f"Remotion mux/ffprobe failed: {result.get('reason')}")
        return _result(context.artifact_dir / "remotion_mux.json", result, [final, audio_for_mux])

    mux_result = await executor.run(
        "remotion_mux",
        {"silent_sha256": file_hash(silent), "audio_sha256": file_hash(audio_for_mux)},
        lambda: asyncio.to_thread(mux),
    )

    qc = run_wealth_blueprint_qc(
        artifact_dir=context.artifact_dir,
        video_path=final,
        audio_path=voice_path,
        manifest_path=manifest_path,
        scene_plan_path=(context.artifact_dir / "remotion_scene_plan.json")
        if (context.artifact_dir / "remotion_scene_plan.json").is_file()
        else ((context.artifact_dir / "scene_plan.json") if (context.artifact_dir / "scene_plan.json").is_file() else None),
    )
    (context.artifact_dir / "video_qc_report.json").write_text(json.dumps(qc, indent=2) + "\n", encoding="utf-8")
    if not qc.get("ok"):
        failed = [c["name"] for c in qc.get("checks", []) if c.get("status") == "fail"]
        raise RemotionRequiredError(f"technical QC hard-fail: {', '.join(failed)}")

    probe = probe_media(final)
    if probe.get("width") != 1080 or probe.get("height") != 1920 or probe.get("video_codec") != "h264":
        raise RemotionRequiredError(f"final format check failed: {probe}")
    if not probe.get("audio_codec"):
        raise RemotionRequiredError("final has no audio track")
    if abs(float(probe.get("fps") or 0) - 30.0) > 0.05:
        raise RemotionRequiredError(f"fps check failed: {probe.get('fps')}")

    production = (context.artifacts.get("project_profile") or {}).get("production") or {}
    # Explicit request label for provenance: E2E overrides set remotion_options.requested_renderer;
    # post-switch smoke leaves it unset → "default" (profile default, no temporary override).
    requested_renderer = str(options.get("requested_renderer") or "default").strip() or "default"
    remotion_output = str(
        remotion_payload.get("output_path")
        or remotion_payload.get("output")
        or silent
    )
    remotion_render_id = str(
        remotion_payload.get("render_id")
        or remotion_payload.get("job_id")
        or remotion_payload.get("output_sha256")
        or file_hash(silent)
    )
    provenance = {
        "requested_renderer": requested_renderer,
        "selected_renderer": "remotion",
        "executed_renderer": "remotion",
        "fallback_used": False,
        "project_manifest_path": str(manifest_path),
        "remotion_render_id": remotion_render_id,
        "remotion_output": remotion_output,
        "final_mp4_path": str(final),
        "remotion_mode": REMOTION_REQUIRED,
        "renderer_boundary": RENDERER_BOUNDARY,
        "editorial_fallback": False,
    }
    write_json(context.artifact_dir / "render_provenance.json", provenance)
    if provenance["fallback_used"] or provenance["editorial_fallback"]:
        raise RemotionRequiredError("provenance fallback_used must be false under REMOTION_REQUIRED")
    if provenance["selected_renderer"] != "remotion" or provenance["executed_renderer"] != "remotion":
        raise RemotionRequiredError("provenance renderer mismatch")
    if not manifest_path.is_file() or not final.is_file():
        raise RemotionRequiredError("provenance paths missing on disk")

    # Remotion has no per-scene editorial clip files. Semantic QA must sample the final MP4
    # using ProjectManifest scene time ranges — never treat empty clips as an audited film.
    scene_windows = [
        {
            "scene_id": scene.scene_id,
            "start_sec": float(scene.timing.start_sec),
            "end_sec": float(scene.timing.end_sec),
        }
        for scene in manifest.scenes
    ]
    if not scene_windows:
        raise RemotionRequiredError("ProjectManifest has no scenes for semantic QA windows")
    payload = {
        "output_file": str(final),
        "sha256": file_hash(final),
        "clips": [],
        "duration_seconds": duration,
        "music": "none",
        "renderer": "remotion",
        "remotion_mode": REMOTION_REQUIRED,
        "renderer_boundary": RENDERER_BOUNDARY,
        "manifest_path": str(manifest_path),
        "manifest_sha256": file_hash(manifest_path),
        "semantic_scene_source": "project_manifest_mp4",
        "semantic_scene_windows": scene_windows,
        "remotion": remotion_payload,
        "mux": _step_payload(mux_result),
        "sound": sound_notes,
        "video_qc": {"ok": True, "failed_count": 0, "role": "technical_precheck"},
        "qa": "not_started",
        "probe": probe,
        "health": health,
        "editorial_fallback": False,
        "render_provenance": provenance,
        "requested_renderer": provenance["requested_renderer"],
        "selected_renderer": provenance["selected_renderer"],
        "executed_renderer": provenance["executed_renderer"],
        "fallback_used": False,
    }
    _result(context.artifact_dir / "edit.json", payload, [silent, final, still, manifest_path])
    context.artifacts["edit"] = payload
    return payload
