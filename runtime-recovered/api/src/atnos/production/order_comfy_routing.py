"""Production Order → existing Comfy channel routing (single source of truth).

Does NOT implement a second channel router. Orders call:
  resolve_channel_id → load_profile → ChannelComfyGenerationService

WB: Asset Brain APPROVED search first; Comfy only on MISS.
WC: REFERENCE_ONLY / CONCEPT — never film renderer.
DC: Dark Chronicles profile only.
Missing/unknown channel_id: FAIL CLOSED (no silent default profile).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from atnos.core.config import Settings
from atnos.production.comfy_channels.models import ChannelId, GenerationMetadata, GenerationRequest
from atnos.production.comfy_channels.model_profiles import load_model_profile
from atnos.production.comfy_channels.profiles import find_workflow, load_profile, resolve_channel_id
from atnos.production.comfy_channels.service import (
    ChannelComfyGenerationError,
    ChannelComfyGenerationService,
)

ComfyUsageRole = Literal["FILM_STILL_ASSETS", "REFERENCE_ONLY", "CONCEPT"]
AssetPurpose = Literal["film_final", "concept", "reference"]
FulfillStatus = Literal[
    "reused_library",
    "reused_cache",
    "generated",
    "awaiting_approval",
    "blocked",
]

CHANNEL_COMFY_ROLE: dict[ChannelId, ComfyUsageRole] = {
    "wealth_blueprint": "FILM_STILL_ASSETS",
    "wonder_crew": "REFERENCE_ONLY",
    "dark_chronicles": "CONCEPT",
}

# Map Asset Brain / order asset types → registered workflow titles on each profile.
_WB_WORKFLOW_BY_TYPE: dict[str, str] = {
    "character": "WB_CHARACTER",
    "background": "WB_BACKGROUND",
    "icon": "WB_ICON_OR_SYMBOL",
    "object": "WB_OBJECT",
    "editorial_scene": "WB_EDITORIAL_SCENE",
    "money": "WB_OBJECT",
    "banking": "WB_OBJECT",
    "investing": "WB_OBJECT",
    "business": "WB_EDITORIAL_SCENE",
    "houses": "WB_OBJECT",
    "cars": "WB_OBJECT",
    "technology": "WB_OBJECT",
    "charts": "WB_ICON_OR_SYMBOL",
    "decorations": "WB_ICON_OR_SYMBOL",
    "transitions": "WB_BACKGROUND",
}

_WC_WORKFLOW_BY_TYPE: dict[str, str] = {
    "character": "CHARACTER_REFERENCE",
    "character_reference": "CHARACTER_REFERENCE",
    "expression": "CHARACTER_EXPRESSION_SHEET",
    "pose": "CHARACTER_POSE_REFERENCE",
    "costume": "COSTUME_CONCEPT",
    "environment": "ENVIRONMENT_CONCEPT",
    "storyboard": "STORYBOARD_FRAME",
    "prop": "PROP_CONCEPT",
}

_DC_WORKFLOW_BY_TYPE: dict[str, str] = {
    "location": "DC_LOCATION",
    "character": "DC_CHARACTER_REFERENCE",
    "object": "DC_OBJECT",
    "event": "DC_HORROR_EVENT",
    "storyboard": "DC_STORYBOARD_FRAME",
    "atmosphere": "DC_ATMOSPHERE",
    "thumbnail": "DC_THUMBNAIL_CONCEPT",
    "establishing": "DC_ESTABLISHING_SHOT",
    "background": "DC_LOCATION",
}


class OrderComfyRoutingError(RuntimeError):
    """Fail-closed routing / policy violation for Production Orders."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OrderAssetRequest(StrictModel):
    """One still/concept asset need from a Production Order (never a final video)."""

    channel_id: str = Field(min_length=1, max_length=80)
    asset_type: str = Field(default="icon", max_length=60)
    subject: str = Field(default="", max_length=120)
    emotion: str = Field(default="", max_length=40)
    direction: str = Field(default="", max_length=40)
    pose: str = Field(default="", max_length=80)
    text: str = Field(default="", max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=16)
    workflow: str | None = Field(default=None, max_length=80)
    purpose: AssetPurpose = "film_final"
    references: list[str] = Field(default_factory=list, max_length=8)
    width: int | None = Field(default=None, ge=256, le=2048)
    height: int | None = Field(default=None, ge=256, le=2048)
    seed: int | None = Field(default=None, ge=0, le=2_147_483_647)
    allow_generate: bool = True
    force_regenerate: bool = False
    visual_style_id: Literal["A", "B", "C", "D"] | None = None
    visual_style_prompt: str | None = Field(default=None, min_length=20, max_length=1600)
    visual_style_tags: list[str] = Field(default_factory=list, max_length=4)
    visual_style_definition_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class OrderAssetResult(StrictModel):
    status: FulfillStatus
    channel_id: ChannelId
    profile_id: ChannelId
    comfy_role: ComfyUsageRole
    workflow_id: str = ""
    workflow_title: str = ""
    model: str = ""
    output_path: str | None = None
    asset_brain_id: str | None = None
    cache_hit: bool = False
    comfy_called: bool = False
    usable_in_final_film: bool = False
    generation_time_ms: float = 0.0
    fingerprint: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


@dataclass
class OrderRoutingMetrics:
    """Per-order counters — Asset Brain HIT must not inflate generated/comfy counts."""

    asset_requests: int = 0
    brain_hits: int = 0
    cache_hits: int = 0
    generated: int = 0
    reused: int = 0
    comfy_calls: int = 0
    generation_time_ms_total: float = 0.0
    awaiting_approval: int = 0
    blocked: int = 0
    results: list[OrderAssetResult] = field(default_factory=list)

    @property
    def asset_cache_hit_rate(self) -> float | None:
        decided = self.brain_hits + self.cache_hits + self.generated + self.awaiting_approval
        if decided <= 0:
            return None
        return round((self.brain_hits + self.cache_hits) / decided, 4)

    @property
    def generated_assets_per_order(self) -> int:
        return self.generated + self.awaiting_approval

    @property
    def reused_assets_per_order(self) -> int:
        return self.reused

    def record(self, result: OrderAssetResult) -> None:
        self.asset_requests += 1
        self.generation_time_ms_total += result.generation_time_ms
        self.results.append(result)
        if result.comfy_called:
            self.comfy_calls += 1
        if result.status == "reused_library":
            self.brain_hits += 1
            self.reused += 1
        elif result.status == "reused_cache":
            self.cache_hits += 1
            self.reused += 1
        elif result.status == "generated":
            self.generated += 1
        elif result.status == "awaiting_approval":
            self.awaiting_approval += 1
        elif result.status == "blocked":
            self.blocked += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "asset_requests": self.asset_requests,
            "brain_hits": self.brain_hits,
            "cache_hits": self.cache_hits,
            "generated": self.generated,
            "reused": self.reused,
            "comfy_calls": self.comfy_calls,
            "awaiting_approval": self.awaiting_approval,
            "blocked": self.blocked,
            "asset_cache_hit_rate": self.asset_cache_hit_rate,
            "generated_assets_per_order": self.generated_assets_per_order,
            "reused_assets_per_order": self.reused_assets_per_order,
            "generation_time_ms_total": round(self.generation_time_ms_total, 2),
            "results": [r.model_dump(mode="json") for r in self.results],
        }


def resolve_order_channel(channel_or_project: str | None) -> ChannelId:
    """Fail closed: missing/unknown channel never picks a default profile."""
    raw = (channel_or_project or "").strip()
    if not raw:
        raise OrderComfyRoutingError("FAIL_CLOSED: missing channel_id on Production Order asset request")
    try:
        return resolve_channel_id(raw)
    except ValueError as exc:
        raise OrderComfyRoutingError(f"FAIL_CLOSED: {exc}") from exc


def resolve_order_comfy_profile(settings: Settings, channel_or_project: str | None):
    channel_id = resolve_order_channel(channel_or_project)
    profile = load_profile(settings, channel_id)
    role = CHANNEL_COMFY_ROLE[channel_id]
    return channel_id, profile, role


def default_workflow_for(channel_id: ChannelId, asset_type: str) -> str:
    key = (asset_type or "icon").strip().casefold()
    if channel_id == "wealth_blueprint":
        return _WB_WORKFLOW_BY_TYPE.get(key, "WB_ICON_OR_SYMBOL")
    if channel_id == "wonder_crew":
        return _WC_WORKFLOW_BY_TYPE.get(key, "CHARACTER_REFERENCE")
    return _DC_WORKFLOW_BY_TYPE.get(key, "DC_STORYBOARD_FRAME")


def assert_purpose_allowed(channel_id: ChannelId, purpose: AssetPurpose, role: ComfyUsageRole) -> None:
    if channel_id == "wonder_crew" and purpose == "film_final":
        raise OrderComfyRoutingError(
            "Wonder Crew Comfy role is REFERENCE_ONLY/CONCEPT — cannot render final film frames via Comfy"
        )
    if channel_id == "dark_chronicles" and purpose == "film_final" and role == "CONCEPT":
        # DC Comfy is concept/storyboard; final edit uses other renderers.
        raise OrderComfyRoutingError(
            "Dark Chronicles Comfy role is CONCEPT — not the final film renderer"
        )
    if channel_id == "wonder_crew" and purpose not in {"concept", "reference"}:
        raise OrderComfyRoutingError(f"Wonder Crew rejects purpose={purpose}")


def _is_alex(subject: str | None, asset_type: str) -> bool:
    name = (subject or "").strip().casefold()
    return asset_type.strip().casefold() == "character" and name in {"alex", "wb_alex_01", "wb-alex-01"}


class OrderComfyAssetFulfiller:
    """Production Order entry: channel_id → profile → Asset Brain / Comfy → metadata."""

    def __init__(
        self,
        settings: Settings,
        *,
        channel_service: ChannelComfyGenerationService | None = None,
        metrics: OrderRoutingMetrics | None = None,
        asset_brain: Any | None = None,
    ) -> None:
        self.settings = settings
        self.channel_service = channel_service or ChannelComfyGenerationService(settings)
        self.metrics = metrics or OrderRoutingMetrics()
        self._asset_brain = asset_brain

    def fulfill(self, request: OrderAssetRequest) -> OrderAssetResult:
        started = time.perf_counter()
        channel_id, profile, role = resolve_order_comfy_profile(self.settings, request.channel_id)
        try:
            assert_purpose_allowed(channel_id, request.purpose, role)
        except OrderComfyRoutingError as exc:
            result = OrderAssetResult(
                status="blocked",
                channel_id=channel_id,
                profile_id=profile.channel_id,
                comfy_role=role,
                reason=str(exc),
                generation_time_ms=(time.perf_counter() - started) * 1000,
            )
            self.metrics.record(result)
            raise

        workflow_key = request.workflow or default_workflow_for(channel_id, request.asset_type)
        try:
            workflow = find_workflow(profile, workflow_key)
        except ValueError as exc:
            raise OrderComfyRoutingError(
                f"FAIL_CLOSED: workflow '{workflow_key}' not on profile {profile.channel_id}"
            ) from exc

        if channel_id == "wealth_blueprint":
            result = self._fulfill_wealth_blueprint(request, profile, role, workflow, started)
        else:
            result = self._fulfill_concept_channel(request, profile, role, workflow, started)

        self.metrics.record(result)
        return result

    def _fulfill_wealth_blueprint(
        self,
        request: OrderAssetRequest,
        profile,
        role: ComfyUsageRole,
        workflow,
        started: float,
    ) -> OrderAssetResult:
        from atnos.production.asset_brain.models import AssetQuery
        from atnos.production.asset_brain.service import WealthBlueprintAssetBrain

        brain = self._asset_brain or WealthBlueprintAssetBrain(self.settings)
        alex = _is_alex(request.subject, request.asset_type)
        # Film final always requires APPROVED (Alex and non-Alex reusable stills).
        require_approved = request.purpose == "film_final"
        query = AssetQuery(
            channel="wealth_blueprint",
            type=_wb_asset_type(request.asset_type),
            character=(request.subject or None) if request.asset_type == "character" else None,
            pose=request.pose or None,
            emotion=request.emotion or None,
            direction=request.direction or None,
            tags_any=list(request.tags),
            tags_all=list(request.visual_style_tags),
            text=request.text,
            require_approved=require_approved,
        )
        selection = brain.select_best(query)
        elapsed = (time.perf_counter() - started) * 1000
        if not request.references and selection.status == "found" and selection.match is not None:
            asset = selection.match.asset
            path = brain.resolve_file(asset)
            usable = asset.is_production_selectable() and request.purpose == "film_final"
            if alex and request.purpose == "film_final" and not asset.is_production_selectable():
                raise OrderComfyRoutingError(
                    "Character Alex requires an APPROVED Asset Brain asset for final film"
                )
            return OrderAssetResult(
                status="reused_library",
                channel_id="wealth_blueprint",
                profile_id=profile.channel_id,
                comfy_role=role,
                workflow_id=workflow.workflow_id,
                workflow_title=workflow.title,
                model="",
                output_path=str(path) if path.is_file() else asset.file_path,
                asset_brain_id=asset.id,
                cache_hit=True,
                comfy_called=False,
                usable_in_final_film=usable,
                generation_time_ms=elapsed,
                fingerprint=asset.request_fingerprint or "",
                metadata={"match_score": selection.match.score, "quality_status": asset.quality_status},
                reason="Asset Brain APPROVED hit — Comfy not launched",
            )

        if not request.allow_generate:
            raise OrderComfyRoutingError(
                "Asset Brain MISS and generation disabled"
                + (f": {selection.generation_need.reason}" if selection.generation_need else "")
            )

        # MISS → Comfy WB profile (existing ChannelComfyGenerationService).
        # A pending candidate from this style/order must be reviewed before a retry
        # can generate another one. No unapproved asset enters the final film.
        # Style tags do not bind a pending candidate to reference pixels.
        # Explicit references must go through the existing fingerprinted cache,
        # just like the approved-library branch above.
        if request.visual_style_id and not request.references:
            pending_query = query.model_copy(update={"require_approved": False})
            pending = brain.select_best(pending_query)
            if pending.status == "found" and pending.match is not None:
                asset = pending.match.asset
                if asset.quality_status in {"GENERATED", "AWAITING_REVIEW", "TECHNICALLY_VALID", "NEEDS_REPROCESS"}:
                    return OrderAssetResult(
                        status="awaiting_approval", channel_id="wealth_blueprint",
                        profile_id=profile.channel_id, comfy_role=role,
                        workflow_id=workflow.workflow_id, workflow_title=workflow.title,
                        output_path=str(brain.resolve_file(asset)), asset_brain_id=asset.id,
                        cache_hit=True, comfy_called=False, usable_in_final_film=False,
                        generation_time_ms=(time.perf_counter() - started) * 1000,
                        fingerprint=asset.request_fingerprint or "",
                        metadata={"visual_style_id": request.visual_style_id},
                        reason="Style candidate exists — awaiting approval",
                    )
        meta = self._generate_with_retry(
            GenerationRequest(
                channel="wealth_blueprint",
                workflow=workflow.title,
                subject=request.subject or request.asset_type,
                emotion=request.emotion or "neutral",
                direction=request.direction or request.pose or "front",
                asset_type=workflow.asset_type,
                composition=request.text if request.text else "",
                references=list(request.references),
                width=request.width,
                height=request.height,
                seed=request.seed,
                force_regenerate=request.force_regenerate,
                visual_style_id=request.visual_style_id,
                visual_style_prompt=request.visual_style_prompt,
                visual_style_tags=list(request.visual_style_tags),
                visual_style_definition_hash=request.visual_style_definition_hash,
            )
        )
        elapsed = (time.perf_counter() - started) * 1000
        asset_id = str((meta.generation_settings or {}).get("asset_brain_id") or "") or None
        # Newly generated never auto-enters final film (Alex especially).
        awaiting = True
        if alex and request.purpose == "film_final":
            return OrderAssetResult(
                status="awaiting_approval",
                channel_id="wealth_blueprint",
                profile_id=profile.channel_id,
                comfy_role=role,
                workflow_id=meta.workflow_id,
                workflow_title=workflow.title,
                model=meta.model,
                output_path=meta.output_path,
                asset_brain_id=asset_id,
                cache_hit=meta.cache_hit,
                comfy_called=not meta.cache_hit,
                usable_in_final_film=False,
                generation_time_ms=elapsed,
                fingerprint=meta.fingerprint,
                metadata=dict(meta.generation_settings or {}),
                reason="Alex generated — awaiting APPROVED before final film",
            )
        return OrderAssetResult(
            status="awaiting_approval" if awaiting else "generated",
            channel_id="wealth_blueprint",
            profile_id=profile.channel_id,
            comfy_role=role,
            workflow_id=meta.workflow_id,
            workflow_title=workflow.title,
            model=meta.model,
            output_path=meta.output_path,
            asset_brain_id=asset_id,
            cache_hit=meta.cache_hit,
            comfy_called=not meta.cache_hit,
            usable_in_final_film=False,
            generation_time_ms=elapsed,
            fingerprint=meta.fingerprint,
            metadata=dict(meta.generation_settings or {}),
            reason="Generated via WB Comfy profile — pending quality/approval policy",
        )

    def _fulfill_concept_channel(
        self,
        request: OrderAssetRequest,
        profile,
        role: ComfyUsageRole,
        workflow,
        started: float,
    ) -> OrderAssetResult:
        meta = self._generate_with_retry(
            GenerationRequest(
                channel=profile.channel_id,
                workflow=workflow.title,
                subject=request.subject or request.asset_type,
                emotion=request.emotion,
                direction=request.direction or "front",
                asset_type=workflow.asset_type,
                composition=request.text if request.text else "",
                lighting=request.text[:200] if profile.channel_id == "dark_chronicles" else "",
                references=list(request.references),
                width=request.width,
                height=request.height,
                seed=request.seed,
                force_regenerate=request.force_regenerate,
            )
        )
        elapsed = (time.perf_counter() - started) * 1000
        return OrderAssetResult(
            status="generated" if not meta.cache_hit else "reused_cache",
            channel_id=profile.channel_id,
            profile_id=profile.channel_id,
            comfy_role=role,
            workflow_id=meta.workflow_id,
            workflow_title=workflow.title,
            model=meta.model,
            output_path=meta.output_path,
            asset_brain_id=None,
            cache_hit=meta.cache_hit,
            comfy_called=not meta.cache_hit,
            usable_in_final_film=False,
            generation_time_ms=elapsed,
            fingerprint=meta.fingerprint,
            metadata=dict(meta.generation_settings or {}),
            reason=f"{role} output via {profile.channel_id} profile",
        )

    def _generate_with_retry(self, gen_request: GenerationRequest) -> GenerationMetadata:
        """Controlled retry: never duplicate a timed-out job without cache/job check."""
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return self.channel_service.generate(gen_request)
            except ChannelComfyGenerationError as exc:
                last_error = exc
                message = str(exc).casefold()
                timed_out = "timeout" in message or "timed out" in message
                if not timed_out or attempt >= 2:
                    raise OrderComfyRoutingError(str(exc)) from exc
                # Before retry: identical request without force may hit channel cache
                # if the first attempt actually finished after the client timed out.
                gen_request = gen_request.model_copy(update={"force_regenerate": False})
                time.sleep(0.25 * (attempt + 1))
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                raise OrderComfyRoutingError(f"Comfy channel generation failed: {exc}") from exc
        raise OrderComfyRoutingError(f"Comfy channel generation failed after retries: {last_error}")


def _wb_asset_type(asset_type: str):
    from atnos.production.asset_brain.models import ASSET_TYPES

    key = (asset_type or "icon").strip().casefold()
    if key in ASSET_TYPES:
        return key  # type: ignore[return-value]
    if key in {"object", "editorial_scene"}:
        return "icon"
    return "icon"


def routing_snapshot_for_order(settings: Settings, project: str | None) -> dict[str, Any]:
    """Durable artifact payload: which Comfy profile this Production Order is bound to."""
    channel_id, profile, role = resolve_order_comfy_profile(settings, project)
    effective_model_profile = load_model_profile(settings, channel_id)
    return {
        "schema_version": "comfy-order-routing-1",
        "order_project": project,
        "channel_id": channel_id,
        "profile_id": profile.channel_id,
        "comfy_role": role,
        "legacy_project_key": profile.legacy_project_key,
        "output_subdir": profile.output_subdir,
        "primary_characters": list(profile.primary_characters),
        "workflows": [
            {"workflow_id": w.workflow_id, "title": w.title, "asset_type": w.asset_type}
            for w in profile.workflows
        ],
        "image_models": list(profile.image_models),
        "effective_model_profile": effective_model_profile,
        "fail_closed": True,
        "router": "atnos.production.comfy_channels.profiles.resolve_channel_id",
    }


def fulfill_order_asset_requests(
    settings: Settings,
    *,
    project: str,
    requests: list[OrderAssetRequest] | list[dict[str, Any]],
    channel_service: ChannelComfyGenerationService | None = None,
    asset_brain: Any | None = None,
) -> tuple[list[OrderAssetResult], OrderRoutingMetrics]:
    """Batch fulfill for a Production Order; all requests must resolve to the order channel."""
    order_channel = resolve_order_channel(project)
    metrics = OrderRoutingMetrics()
    fulfiller = OrderComfyAssetFulfiller(
        settings, channel_service=channel_service, metrics=metrics, asset_brain=asset_brain
    )
    results: list[OrderAssetResult] = []
    for raw in requests:
        req = raw if isinstance(raw, OrderAssetRequest) else OrderAssetRequest.model_validate(raw)
        req_channel = resolve_order_channel(req.channel_id)
        if req_channel != order_channel:
            raise OrderComfyRoutingError(
                f"FAIL_CLOSED: asset channel {req_channel} leaks from order channel {order_channel}"
            )
        results.append(fulfiller.fulfill(req))
    return results, metrics


def map_asset_generation_to_channel_request(
    *,
    channel: str,
    asset_type: str,
    subject: str = "",
    emotion: str = "neutral",
    direction: str = "front",
    pose: str = "",
    text: str = "",
    width: int | None = None,
    height: int | None = None,
    seed: int | None = None,
    references: list[str] | None = None,
    workflow_id: str | None = None,
) -> GenerationRequest:
    """Bridge Asset Brain / provisioner requests onto the existing channel GenerationRequest."""
    channel_id = resolve_order_channel(channel)
    workflow = workflow_id or default_workflow_for(channel_id, asset_type)
    return GenerationRequest(
        channel=channel_id,
        workflow=workflow,
        subject=subject or asset_type,
        emotion=emotion,
        direction=direction or pose or "front",
        asset_type=asset_type,
        composition=(text or "")[:500],
        references=list(references or []),
        width=width,
        height=height,
        seed=seed,
    )
