"""Per-scene production asset selection for Wealth Blueprint Remotion orders.

Rules:
- reference_sheet never enters final-film manifests
- selection requires matching channel + production_role + version + APPROVED
- missing required character → acquisition path or WAITING_FOR_ASSETS (no ref catalog fallback)
- staging preserves alpha and aspect ratio (no RGB flatten / stretch)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from PIL import Image

from atnos.production.asset_brain.models import (
    FILM_PRODUCTION_ROLES,
    AssetQuery,
    AssetRecord,
    AssetType,
    ProductionRole,
    infer_production_role,
)
from atnos.production.asset_brain.service import WealthBlueprintAssetBrain

WAITING_FOR_ASSETS = "WAITING_FOR_ASSETS"

_TYPE_TO_ROLE: dict[str, ProductionRole] = {
    "character": "character_cutout",
    "background": "background",
    "icon": "icon",
    "charts": "diagram",
    "money": "object",
    "banking": "object",
    "investing": "object",
    "business": "object",
    "houses": "object",
    "cars": "object",
    "technology": "object",
}


@dataclass
class SceneStillAssignment:
    scene_id: str
    path: Path
    asset_id: str
    production_role: ProductionRole
    channel: str
    version: str
    preserve_alpha: bool
    fit: Literal["contain", "cover"]
    source_path: str
    function: str


@dataclass
class SceneAssetPlanResult:
    status: Literal["ok", "waiting"]
    assignments: dict[str, SceneStillAssignment] = field(default_factory=dict)
    waiting: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "waiting_code": WAITING_FOR_ASSETS if self.status == "waiting" else None,
            "assignments": {
                scene_id: {
                    "scene_id": row.scene_id,
                    "path": str(row.path),
                    "asset_id": row.asset_id,
                    "production_role": row.production_role,
                    "channel": row.channel,
                    "version": row.version,
                    "preserve_alpha": row.preserve_alpha,
                    "fit": row.fit,
                    "source_path": row.source_path,
                    "function": row.function,
                }
                for scene_id, row in self.assignments.items()
            },
            "waiting": self.waiting,
            "notes": self.notes,
        }


def assert_production_asset(asset: AssetRecord, *, channel: str = "wealth_blueprint") -> None:
    """Fail closed: wrong channel / role / approval cannot enter production."""
    from atnos.production.channels.wealth_blueprint.geometric_assets import (
        geometric_reject_reason,
        is_geometric_placeholder_asset,
    )

    if (asset.channel or "").casefold() != channel.casefold():
        raise ValueError(f"asset {asset.id} channel mismatch: {asset.channel!r}")
    role = asset.production_role or infer_production_role(
        asset_type=asset.type,
        source=asset.source,
        file_path=asset.file_path,
        transparent_background=asset.transparent_background,
        tags=list(asset.tags),
    )
    if role == "reference_sheet" or role not in FILM_PRODUCTION_ROLES:
        raise ValueError(f"asset {asset.id} production_role={role!r} is not a film production asset")
    if not asset.is_production_selectable():
        raise ValueError(f"asset {asset.id} is not APPROVED for production (status={asset.quality_status})")
    if is_geometric_placeholder_asset(asset):
        raise ValueError(geometric_reject_reason(asset))


def stage_production_still(
    source: Path,
    dest: Path,
    *,
    preserve_alpha: bool = True,
    fit: Literal["contain", "cover"] = "contain",
    canvas: tuple[int, int] = (1080, 1920),
    canvas_rgba: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    """Stage still for Remotion without stretching or flattening alpha."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if canvas_rgba is None:
        # Transparent canvas when preserving alpha so Remotion can composite over brand BG.
        canvas_rgba = (0, 0, 0, 0) if preserve_alpha else (17, 17, 17, 255)
    with Image.open(source) as raw:
        img = raw.convert("RGBA") if preserve_alpha or raw.mode in {"RGBA", "LA", "P"} else raw.convert("RGB")
        if img.mode == "P":
            img = img.convert("RGBA")
        src_w, src_h = img.size
        if src_w <= 0 or src_h <= 0:
            raise ValueError(f"invalid still dimensions: {source}")
        cw, ch = canvas
        scale = min(cw / src_w, ch / src_h) if fit == "contain" else max(cw / src_w, ch / src_h)
        new_w = max(1, int(round(src_w * scale)))
        new_h = max(1, int(round(src_h * scale)))
        resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        if preserve_alpha or img.mode == "RGBA":
            canvas_img = Image.new("RGBA", canvas, canvas_rgba)
            ox = (cw - new_w) // 2
            oy = (ch - new_h) // 2
            if fit == "cover":
                left = max(0, (new_w - cw) // 2)
                top = max(0, (new_h - ch) // 2)
                cropped = resized.crop((left, top, left + cw, top + ch))
                canvas_img = cropped if cropped.mode == "RGBA" else cropped.convert("RGBA")
            else:
                canvas_img.paste(resized, (ox, oy), resized if resized.mode == "RGBA" else None)
            canvas_img.save(dest, format="PNG")
            mode_out = "RGBA"
        else:
            canvas_img = Image.new("RGB", canvas, canvas_rgba[:3])
            ox = (cw - new_w) // 2
            oy = (ch - new_h) // 2
            if fit == "cover":
                left = max(0, (new_w - cw) // 2)
                top = max(0, (new_h - ch) // 2)
                canvas_img = resized.crop((left, top, left + cw, top + ch)).convert("RGB")
            else:
                canvas_img.paste(resized.convert("RGB"), (ox, oy))
            canvas_img.save(dest, format="PNG")
            mode_out = "RGB"
    return {
        "source": str(source),
        "dest": str(dest),
        "mode": mode_out,
        "source_size": [src_w, src_h],
        "fit": fit,
        "preserve_alpha": preserve_alpha,
        "canvas": list(canvas),
    }


def _role_for_need(need_type: str) -> ProductionRole:
    return _TYPE_TO_ROLE.get(need_type, "object")


def _wb_type(need_type: str) -> AssetType:
    allowed = {
        "character",
        "background",
        "icon",
        "money",
        "banking",
        "investing",
        "business",
        "houses",
        "cars",
        "technology",
        "charts",
        "decorations",
        "transitions",
    }
    return need_type if need_type in allowed else "icon"  # type: ignore[return-value]


def _scene_function(scene: dict[str, Any]) -> str:
    if scene.get("use_alex"):
        pose = str(scene.get("alex_pose") or "explaining")
        return f"alex_{pose}"
    focus = str(scene.get("visual_focus") or "statement")
    return focus


def _needs_for_scene(scene: dict[str, Any]) -> list[dict[str, Any]]:
    needs = [n for n in (scene.get("asset_needs") or []) if isinstance(n, dict)]
    intent = scene.get("visual_intent") if isinstance(scene.get("visual_intent"), dict) else {}
    contract = scene.get("meaning_contract") if isinstance(scene.get("meaning_contract"), dict) else {}
    from atnos.production.channels.wealth_blueprint.scene_meaning import required_interaction_for_contract
    from atnos.production.channels.wealth_blueprint.visual_concepts import (
        icon_tags_for_concept,
        infer_visual_concept,
    )

    concept = str(scene.get("visual_concept") or "") or infer_visual_concept(
        scene.get("narration_excerpt"),
        scene.get("on_screen_text"),
        " ".join(str(t) for t in (scene.get("icon_tags") or [])),
    )
    tags = icon_tags_for_concept(concept)  # type: ignore[arg-type]
    interaction = required_interaction_for_contract(contract)
    dominant = str(intent.get("dominant_intent") or "")
    source_segment_ids = [str(value) for value in (scene.get("narration_segment_ids") or []) if str(value)]
    source_visual_intents = [str(value) for value in (scene.get("source_visual_intents") or [])]
    if needs:
        # Prefer concept-aligned tags so habit/notebook do not inherit a raise still.
        enriched = []
        for need in needs:
            row = dict(need)
            if str(row.get("type") or "") == "icon" and tags:
                row["tags"] = list(dict.fromkeys([*(tags), *[str(t) for t in (row.get("tags") or [])]]))
            row["visual_concept"] = concept
            row["visual_intent"] = dominant
            row["required_interaction"] = interaction
            row["object_roles"] = contract.get("object_roles") or {}
            if source_segment_ids:
                row["source_segment_ids"] = source_segment_ids
            if source_visual_intents:
                row["source_visual_intents"] = source_visual_intents
            if contract.get("native_scene_role"):
                row["native_scene_role"] = str(contract["native_scene_role"])
            if contract.get("asset_subject"):
                row["asset_subject"] = str(contract["asset_subject"])
                row["asset_composition"] = str(contract.get("asset_composition") or "")
                row["illustration_function"] = str(contract.get("illustration_function") or "")
            enriched.append(row)
        return enriched
    # Derive from scene function when planner omitted asset_needs.
    if scene.get("use_alex"):
        return [
            {
                "type": "character",
                "character": "alex",
                "pose": scene.get("alex_pose") or "explaining",
                "tags": ["finance", "host"],
                "text": str(scene.get("on_screen_text") or ""),
                "required": True,
                "visual_concept": concept,
                "visual_intent": dominant,
                "required_interaction": interaction,
                "object_roles": contract.get("object_roles") or {},
                "source_segment_ids": source_segment_ids,
                "source_visual_intents": source_visual_intents,
            }
        ]
    focus = str(scene.get("visual_focus") or "")
    if focus == "icon":
        return [
            {
                "type": "icon",
                "tags": tags or list(scene.get("icon_tags") or ["finance"]),
                "text": str(scene.get("on_screen_text") or ""),
                "required": True,
                "visual_concept": concept,
                "visual_intent": dominant,
                "required_interaction": interaction,
                "object_roles": contract.get("object_roles") or {},
                "source_segment_ids": source_segment_ids,
                "source_visual_intents": source_visual_intents,
            }
        ]
    if focus in {"chart", "comparison"}:
        row = {"type": "charts", "tags": ["investing"], "text": str(scene.get("on_screen_text") or ""), "required": False,
               "source_segment_ids": source_segment_ids, "source_visual_intents": source_visual_intents}
        if contract.get("native_scene_role"):
            row["native_scene_role"] = str(contract["native_scene_role"])
        return [row]
    return [
        {
            "type": "background",
            "tags": ["editorial"],
            "text": str(scene.get("on_screen_text") or ""),
            "required": False,
            "visual_concept": concept,
            "visual_intent": dominant,
            "required_interaction": interaction,
            "object_roles": contract.get("object_roles") or {},
            "source_segment_ids": source_segment_ids,
            "source_visual_intents": source_visual_intents,
            **({"native_scene_role": str(contract["native_scene_role"])} if contract.get("native_scene_role") else {}),
        }
    ]


_CONCEPT_ASSET_HINTS: dict[str, tuple[str, ...]] = {
    "raise_absorption": ("raise", "split", "income", "absorb"),
    "notebook_log": ("notebook", "ledger", "track", "write", "log"),
    "habit": ("habit", "repeat", "week", "cushion"),
    "weekly_transfer": ("transfer", "weekly", "auto", "friday"),
    "auto_transfer": ("transfer", "automatic", "auto"),
    "money_movement": ("transfer", "move", "banking"),
    "savings_account": ("savings", "account", "pocket"),
}


_FORBIDDEN_ASSET_FOR_CONCEPT: dict[str, tuple[str, ...]] = {
    # Habit/notebook must never stage the raise-split still via loose tag overlap.
    "notebook_log": ("raise_absorption", "illust_raise", "weekly_auto_transfer", "illust_weekly"),
    "habit": ("raise_absorption", "illust_raise", "weekly_auto_transfer", "illust_weekly", "savings_account_choice"),
    "weekly_transfer": ("raise_absorption", "illust_raise", "notebook"),
    "money_movement": ("raise_absorption", "illust_raise", "notebook"),
}


def _asset_intent_score(asset: AssetRecord, need: dict[str, Any]) -> float:
    """Score library hits by visual intent + object role + required interaction — not loose tags alone."""
    concept = str(need.get("visual_concept") or "").casefold()
    intent = str(need.get("visual_intent") or "").casefold()
    interaction = str(need.get("required_interaction") or "").casefold()
    hay = " ".join(
        [
            str(asset.id or ""),
            str(asset.file_path or ""),
            " ".join(str(t) for t in (asset.tags or [])),
            str(getattr(asset, "text", "") or ""),
        ]
    ).casefold()
    for banned in _FORBIDDEN_ASSET_FOR_CONCEPT.get(concept, ()):
        if banned.casefold() in hay:
            return -1.0
    score = 0.0
    for hint in _CONCEPT_ASSET_HINTS.get(concept, ()):
        if hint in hay:
            score += 0.35
    if intent == "note_track" and any(token in hay for token in ("notebook", "ledger", "log")):
        score += 0.5
    if intent == "split" and any(token in hay for token in ("raise", "split", "absorb")):
        score += 0.5
    if intent == "transfer" and "transfer" in hay:
        score += 0.4
    if intent == "repeat" and any(token in hay for token in ("habit", "week", "repeat")):
        score += 0.4
    if interaction == "notebook_entry_write" and not any(token in hay for token in ("notebook", "ledger", "log", "track")):
        score -= 0.8
    if interaction == "branching_split" and "raise" not in hay and "split" not in hay:
        score -= 0.5
    roles = need.get("object_roles") if isinstance(need.get("object_roles"), dict) else {}
    for role_label in roles.values():
        token = str(role_label or "").casefold()
        if token and token in hay:
            score += 0.15
    return score


def _select_library_asset(
    brain: WealthBlueprintAssetBrain,
    need: dict[str, Any],
    *,
    channel: str,
    style_tags: list[str] | None = None,
) -> AssetRecord | None:
    from atnos.production.channels.wealth_blueprint.geometric_assets import is_geometric_placeholder_asset

    need_type = _wb_type(str(need.get("type") or "icon"))
    role = _role_for_need(need_type)
    query = AssetQuery(
        channel=channel,
        type=need_type,
        production_role=role,
        character=str(need.get("character") or "") or None,
        pose=str(need.get("pose") or "") or None,
        tags_any=[] if style_tags else list(need.get("tags") or []),
        tags_all=list(style_tags or []),
        text=str(need.get("text") or ""),
        require_approved=True,
        exclude_reference_sheets=True,
        require_transparent=True if role == "character_cutout" else None,
        version=str(need.get("version") or "") or None,
        min_semantic_score=0.15,
        limit=8,
    )
    # Prefer ranked candidates; skip geometric placeholders even if still APPROVED in catalog.
    hits = brain.search(query) if hasattr(brain, "search") else []
    if not hits:
        result = brain.select_best(query)
        if result.status == "found" and result.match is not None:
            hits = [result.match]
        elif query.pose:
            loose = query.model_copy(update={"pose": None})
            result = brain.select_best(loose)
            if result.status == "found" and result.match is not None:
                hits = [result.match]
    ranked: list[tuple[float, AssetRecord]] = []
    for hit in hits:
        asset = hit.asset if hasattr(hit, "asset") else hit
        if is_geometric_placeholder_asset(asset):
            continue
        try:
            assert_production_asset(asset, channel=channel)
        except ValueError:
            continue
        path = brain.resolve_file(asset)
        if not path.is_file() or is_geometric_placeholder_asset(asset, resolve_path=path):
            continue
        score = _asset_intent_score(asset, need)
        if score < 0:
            continue
        ranked.append((score, asset))
    if not ranked:
        return None
    ranked.sort(key=lambda row: row[0], reverse=True)
    # Require a positive intent match when the beat declares a concrete interaction.
    best_score, best = ranked[0]
    if need.get("required_interaction") and best_score < 0.2 and str(need.get("visual_concept") or "") not in {
        "",
        "generic_finance",
    }:
        # No sufficiently aligned still — leave None so programmatic overlay / waiting path can run.
        return None
    return best


def _try_acquisition(
    settings: Any,
    need: dict[str, Any],
    *,
    allow_generate: bool,
    visual_style: dict[str, Any] | None = None,
    style_tags: list[str] | None = None,
    reference_files: list[str] | None = None,
) -> tuple[AssetRecord | None, dict[str, Any] | None]:
    """Use existing OrderComfyAssetFulfiller path; never returns reference sheets."""
    try:
        from atnos.production.order_comfy_routing import OrderAssetRequest, OrderComfyAssetFulfiller
    except Exception as exc:  # noqa: BLE001
        return None, {"reason": f"acquisition_unavailable:{exc}"}

    fulfiller = OrderComfyAssetFulfiller(settings)
    subject = str(need.get("character") or "")
    description = str(need.get("text") or "")
    if visual_style and not subject:
        subject = str(need.get("asset_subject") or "").strip()
        physical_subject = bool(subject)
        if not subject:
            subject = str(need.get("visual_concept") or "").replace("_", " ").strip()
        if not subject or subject == "generic finance":
            subject = str(next(iter(need.get("tags") or []), need.get("type") or "editorial object"))
        roles = need.get("object_roles") if isinstance(need.get("object_roles"), dict) else {}
        objects = ", ".join(str(value) for value in roles.values() if value)
        interaction = str(need.get("required_interaction") or "")
        if physical_subject:
            illustration_function = str(need.get("illustration_function") or "").casefold()
            if illustration_function == 'car_repair_garage' and str(visual_style.get('prompt', '')).startswith('2D '):
                # Match the accepted compact illustration's drawing language;
                # tall SDXL canvases produced a cropped car and a multi-panel sheet.
                # Keep this a single still scene, never a generated diagram.
                subject = 'one small stationary mint compact car inside a quiet garage'
                description = (
                    'Single frame. Rounded flat 2D cartoon forms, two-tone graphic shadows. '
                    'One complete car in side view, space around both bumpers and all wheels. '
                    'Cream garage, indigo shadows, warm amber light, coral accents. '
                    'Quiet space above and below. '
                    'Avoid: collage, panels, contact sheet, multiple views, extra cars, vintage car, '
                    'people, printed license plate, letters, numbers, logos, realistic photography.'
                )
            elif illustration_function == "bill_and_bag":
                # Keep this hook as a compact two-prop still life. The planner's
                # verbose caption-space / office setting used to push the actual
                # objects past SDXL's useful prompt window, producing people,
                # rooms, and landscapes instead of the requested bill and bag.
                subject = "one unprinted thin horizontal teal paper card beside one small plain shopping bag"
                description = (
                    "Two props only, side by side with a clear gap; both fully visible and large in frame. "
                    "Simple cream tabletop, hand-drawn navy ink outlines, soft paper texture, restrained gold accent."
                )
                need_text = str(need.get("asset_composition") or "")
                _, sep, excluded = need_text.partition(" Avoid:")
                if sep and excluded.strip():
                    description += f" Avoid: {excluded.strip()}"
            else:
                # First tokens name the real requested object. Abstract finance
                # words or the style's title must never become the picture's subject.
                framing = (
                    ("A complete vertical illustrated scene in the requested setting, requested objects large, clear layered color planes. "
                     if str(visual_style.get('prompt', '')).startswith('2D ') else
                     "A complete vertical illustrated scene, requested objects large on a home-office desk, quiet city dusk through a window. "
                     if str(visual_style.get('id')) == 'D' else
                     "A complete vertical editorial illustration, requested objects large in the central area, restrained painted paper backdrop. ")
                    + "Quiet upper and lower text areas."
                    if str(need.get("type") or "") == "background"
                    else "A single isolated foreground object group, fully visible on a plain white background with clean silhouette."
                )
                composition = str(need.get('asset_composition') or '')
                positive, sep, excluded = composition.partition(' Avoid:')
                positive = f"{positive} {framing} No letters, numbers, logos, currency symbols, diagrams or arrows."
                avoid_tail = f" Avoid: {excluded.strip()}" if sep else ''
                # Reserve the prompt budget for every authored exclusion. The WB
                # compiler reads the tail into negative conditioning; truncating it
                # here silently turns those exclusions into positive-only guidance.
                positive_budget = max(0, 500 - len(avoid_tail))
                description = positive[:positive_budget].rstrip() + avoid_tail
        else:
            description = (
                f"Depict {subject}. Objects: {objects}. Action: {interaction}. Meaning: {description}"
            )[:500]
    req = OrderAssetRequest(
        channel_id="wealth_blueprint",
        references=list(reference_files or []),
        seed=need.get("seed"),
        asset_type=str(need.get("type") or "icon"),
        subject=subject[:120],
        pose=str(need.get("pose") or ""),
        tags=[] if style_tags else list(need.get("tags") or []),
        text=description,
        purpose="film_final",
        allow_generate=allow_generate,
        visual_style_id=str(visual_style["id"]) if visual_style else None,
        visual_style_prompt=str(visual_style["prompt"]) if visual_style else None,
        visual_style_tags=list(style_tags or []),
        visual_style_definition_hash=str(visual_style["definition_hash"]) if visual_style else None,
        width=768 if str(need.get("type") or "") == "background" and visual_style else None,
        height=(1024 if str(need.get('illustration_function')) == 'car_repair_garage'
                       and str((visual_style or {}).get('prompt', '')).startswith('2D ') else 1344)
               if str(need.get("type") or "") == "background" and visual_style else None,
    )
    try:
        result = fulfiller.fulfill(req)
    except Exception as exc:  # noqa: BLE001
        return None, {"reason": str(exc)[:300], "status": "error"}

    brain = WealthBlueprintAssetBrain(settings) if result.asset_brain_id else None
    candidate = brain.get(str(result.asset_brain_id)) if brain is not None else None
    if result.status == "awaiting_approval" or not result.usable_in_final_film:
        acquisition: dict[str, Any] = {
            "reason": result.reason or "awaiting_approval",
            "status": result.status,
            "asset_id": result.asset_brain_id,
            "output_path": result.output_path,
        }
        if candidate is not None:
            acquisition["quality_status"] = candidate.quality_status
            if candidate.quality_status in {"REJECTED", "NEEDS_REPROCESS", "GENERATED"}:
                acquisition["status"] = candidate.quality_status.casefold()
                acquisition["reason"] = (
                    f"Asset Brain candidate {candidate.quality_status}"
                    + (f": {candidate.reject_reason}" if candidate.reject_reason else "")
                )
                acquisition["failed_checks"] = list(candidate.pre_gate.get("failed") or [])
                if candidate.reject_reason:
                    acquisition["reject_reason"] = candidate.reject_reason
        return None, acquisition
    if result.status not in {"reused_library", "reused_cache"} or not result.asset_brain_id:
        return None, {"reason": result.reason or result.status, "status": result.status}

    asset = candidate
    if asset is None:
        return None, {"reason": "asset_id_missing_from_catalog", "asset_id": result.asset_brain_id}
    try:
        assert_production_asset(asset)
    except ValueError as exc:
        return None, {"reason": str(exc), "asset_id": asset.id}
    if not brain.resolve_file(asset).is_file():
        return None, {"reason": "file_missing", "asset_id": asset.id}
    return asset, None


def _staged_visual_candidate(
    visuals: dict[str, Any],
    *,
    scene_id: str,
    role: ProductionRole,
) -> Path | None:
    """Accept only staged order assets that declare a non-reference production role."""
    assets = visuals.get("assets") or {}
    if not isinstance(assets, dict):
        return None
    for key, asset in assets.items():
        if not isinstance(asset, dict):
            continue
        declared = str(asset.get("production_role") or asset.get("role") or "").casefold()
        if declared in {"reference_sheet", "reference", "contact_sheet"}:
            continue
        if role == "character_cutout" and declared and declared not in {"character_cutout", "character", "selected_image"}:
            continue
        scene_ids = asset.get("scene_ids") or asset.get("scenes") or []
        if scene_ids and scene_id not in {str(x) for x in scene_ids}:
            continue
        staged = Path(str(asset.get("staged_path") or asset.get("file_path") or ""))
        if staged.is_file() and staged.suffix.lower() in {".png", ".webp"}:
            # Reject obvious reference catalog paths even if staged.
            norm = str(staged).replace("\\", "/").casefold()
            if "channel_references/" in norm:
                continue
            return staged
        _ = key
    return None


def resolve_scene_production_assets(
    *,
    plan: dict[str, Any],
    settings: Any,
    artifact_dir: Path,
    visuals: dict[str, Any] | None = None,
    channel: str = "wealth_blueprint",
    allow_generate: bool | None = None,
    visual_style: dict[str, Any] | None = None,
) -> SceneAssetPlanResult:
    """Select and stage per-scene production stills for IMAGE-class Remotion scenes."""
    from atnos.production.channels.wealth_blueprint.layout_registry import layout_to_scene_type
    from atnos.production.project_manifest import SceneType

    visuals = visuals or {}
    if allow_generate is None:
        allow_generate = bool(getattr(settings, "wealth_blueprint_require_comfy_for_missing_assets", False))

    brain = WealthBlueprintAssetBrain(settings)
    assignments: dict[str, SceneStillAssignment] = {}
    waiting: list[dict[str, Any]] = []
    notes: list[str] = []
    scenes = [s for s in (plan.get("scenes") or []) if isinstance(s, dict)]
    assets_dir = artifact_dir / "assets" / "scene_stills"
    assets_dir.mkdir(parents=True, exist_ok=True)

    for index, scene in enumerate(scenes):
        scene_id = str(scene.get("scene_id") or f"s{index + 1:02d}")
        if scene.get("visual_story"):
            # Hash-reviewed layers are resolved by the manifest adapter, never generic still selection.
            if visual_style:
                waiting.append({"scene_id": scene_id, "code": WAITING_FOR_ASSETS,
                                "reason": "visual_story_layers_do_not_match_order_style"})
            continue
        layout_id = str(scene.get("layout_id") or "fullscreen_statement")
        focus = str(scene.get("visual_focus") or "statement")
        scene_type = layout_to_scene_type(
            layout_id,
            visual_focus=focus,
            index=index,
            total=len(scenes),
            has_number=bool(scene.get("number_value")),
            has_chart=bool(scene.get("chart")),
        )
        if scene_type != SceneType.IMAGE:
            continue

        style_tags: list[str] = []
        if visual_style:
            from atnos.production.channels.wealth_blueprint.visual_styles import style_asset_tags

            style_tags = style_asset_tags(visual_style, artifact_dir.name, scene_id)

        function = _scene_function(scene)
        needs = _needs_for_scene(scene)
        primary = needs[0] if needs else {"type": "background", "required": False}
        role = _role_for_need(str(primary.get("type") or "background"))
        required = bool(primary.get("required", True)) if scene.get("use_alex") or focus in {"character", "icon"} else bool(
            primary.get("required", False)
        )

        asset: AssetRecord | None = None
        acquisition_meta: dict[str, Any] | None = None

        staged = None if visual_style else _staged_visual_candidate(visuals, scene_id=scene_id, role=role)
        if staged is not None and role != "character_cutout":
            # Staged non-character stills OK when role matches scene function.
            dest = assets_dir / f"{scene_id}_{role}.png"
            meta = stage_production_still(
                staged,
                dest,
                preserve_alpha=False,
                fit="cover" if role == "background" else "contain",
            )
            assignments[scene_id] = SceneStillAssignment(
                scene_id=scene_id,
                path=dest,
                asset_id=f"staged:{staged.name}",
                production_role=role,
                channel=channel,
                version="staged",
                preserve_alpha=False,
                fit="cover" if role == "background" else "contain",
                source_path=str(staged),
                function=function,
            )
            notes.append(f"{scene_id}: staged {meta['fit']}")
            continue

        asset = _select_library_asset(brain, primary, channel=channel, style_tags=style_tags)
        if asset is None and (required or allow_generate):
            asset, acquisition_meta = _try_acquisition(
                settings, primary, allow_generate=allow_generate,
                visual_style=visual_style, style_tags=style_tags,
            )

        if asset is None:
            # Character required → prefer meaning diagram demotion over geometric stick-figure.
            # Waiting only when no illustrative icon/object can carry the narration either.
            if scene.get("use_alex") or role == "character_cutout":
                demote_tags = list(scene.get("icon_tags") or [])
                if not demote_tags:
                    from atnos.production.channels.wealth_blueprint.visual_concepts import (
                        icon_tags_for_concept,
                        infer_visual_concept,
                    )

                    concept = infer_visual_concept(
                        scene.get("narration_excerpt"),
                        scene.get("on_screen_text"),
                    )
                    demote_tags = icon_tags_for_concept(concept)
                demoted = _select_library_asset(
                    brain,
                    {
                        "type": "icon",
                        "tags": demote_tags or ["finance", "savings", "money"],
                        "text": str(scene.get("on_screen_text") or ""),
                        "required": False,
                    },
                    channel=channel,
                    style_tags=style_tags,
                )
                if demoted is not None:
                    asset = demoted
                    role = demoted.production_role or "icon"
                    notes.append(
                        f"{scene_id}: demoted geometric/missing alex → meaning icon {demoted.id}"
                    )
                else:
                    waiting.append(
                        {
                            "scene_id": scene_id,
                            "code": WAITING_FOR_ASSETS,
                            "need": primary,
                            "acquisition": acquisition_meta,
                            "reason": "missing_approved_character_cutout",
                        }
                    )
                    continue
            else:
                # Progressive library fallback: scene icon_tags → catalog-stable finance icons →
                # any approved icon → editorial background. Empty tags_any = role/type match only.
                intent_meta = {
                    "visual_concept": primary.get("visual_concept"),
                    "visual_intent": primary.get("visual_intent"),
                    "required_interaction": primary.get("required_interaction"),
                    "object_roles": primary.get("object_roles") or {},
                }
                fallback_candidates = [
                    {
                        "type": "icon",
                        "tags": list(scene.get("icon_tags") or []),
                        "text": str(scene.get("on_screen_text") or ""),
                        "required": False,
                        **intent_meta,
                    },
                    {
                        "type": "icon",
                        "tags": ["finance", "savings", "money", "habit", "transfer", "account"],
                        "text": str(scene.get("on_screen_text") or ""),
                        "required": False,
                        **intent_meta,
                    },
                    {
                        "type": "icon",
                        "tags": [],
                        "text": "",
                        "required": False,
                        **intent_meta,
                    },
                    {"type": "background", "tags": ["editorial"], "text": "", "required": False, **intent_meta},
                ]
                for candidate in fallback_candidates:
                    asset = _select_library_asset(brain, candidate, channel=channel, style_tags=style_tags)
                    if asset is not None:
                        role = asset.production_role or _role_for_need(asset.type)
                        notes.append(
                            f"{scene_id}: library_fallback tags={candidate.get('tags') or ['*']} "
                            f"after miss primary={primary.get('tags')}"
                        )
                        break
                if asset is None:
                    waiting.append(
                        {
                            "scene_id": scene_id,
                            "code": WAITING_FOR_ASSETS,
                            "need": primary,
                            "acquisition": acquisition_meta,
                            "reason": "missing_approved_scene_asset",
                        }
                    )
                    continue

        source = brain.resolve_file(asset)
        assert_production_asset(asset, channel=channel)
        if style_tags and not all(tag in asset.tags for tag in style_tags):
            raise ValueError(f"asset {asset.id} does not match this order's visual style and scene")
        preserve_alpha = bool(asset.transparent_background or (asset.production_role == "character_cutout"))
        fit: Literal["contain", "cover"] = "cover" if asset.production_role == "background" else "contain"
        dest = assets_dir / f"{scene_id}_{asset.id}.png"
        stage_production_still(source, dest, preserve_alpha=preserve_alpha, fit=fit)
        assignments[scene_id] = SceneStillAssignment(
            scene_id=scene_id,
            path=dest,
            asset_id=asset.id,
            production_role=asset.production_role or role,  # type: ignore[arg-type]
            channel=asset.channel,
            version=asset.version,
            preserve_alpha=preserve_alpha,
            fit=fit,
            source_path=str(source),
            function=function,
        )
        notes.append(f"{scene_id}: {asset.id} role={asset.production_role}")

    status: Literal["ok", "waiting"] = "waiting" if waiting else "ok"
    return SceneAssetPlanResult(status=status, assignments=assignments, waiting=waiting, notes=notes)


def manifest_uses_reference_sheet(manifest: dict[str, Any] | Any) -> list[str]:
    """Return scene_ids whose selected_image path is a channel reference sheet."""
    hits: list[str] = []
    scenes = manifest.get("scenes") if isinstance(manifest, dict) else getattr(manifest, "scenes", [])
    for scene in scenes or []:
        scene_id = scene.get("scene_id") if isinstance(scene, dict) else getattr(scene, "scene_id", "")
        assets = scene.get("assets") if isinstance(scene, dict) else getattr(scene, "assets", [])
        for asset in assets or []:
            if isinstance(asset, dict):
                role = asset.get("role")
                path = str(asset.get("path") or "")
                provenance = asset.get("provenance") or {}
                workflow = str(provenance.get("workflow") or "")
            else:
                role = getattr(asset, "role", None)
                path = str(getattr(asset, "path", "") or "")
                provenance = getattr(asset, "provenance", None)
                workflow = str(getattr(provenance, "workflow", "") or "") if provenance else ""
            if role != "selected_image":
                continue
            norm = path.replace("\\", "/").casefold()
            if "channel_references/" in norm or "reference_sheet" in workflow.casefold():
                hits.append(str(scene_id))
    return hits
