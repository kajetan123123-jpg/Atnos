from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from atnos.core.config import Settings
from atnos.production.asset_brain.alex_consistency import AlexAnchorError, AlexAnchors, load_alex_anchors, require_alex_anchors
from atnos.production.asset_brain.catalog import save_catalog
from atnos.production.asset_brain.fingerprint import fingerprint_seed, request_fingerprint
from atnos.production.asset_brain.generation_cache import GenerationCache
from atnos.production.asset_brain.models import (
    AssetCatalog,
    AssetGenerationProvenance,
    AssetGenerationRequest,
    AssetMatch,
    AssetQuery,
    AssetRecord,
    AssetSelectionResult,
    GenerationNeed,
    StrictModel,
)
from atnos.production.asset_brain.prompts import build_prompt, resolve_workflow_id
from atnos.production.asset_brain.service import WealthBlueprintAssetBrain
from atnos.production.comfy_channels.models import GenerationMetadata
from atnos.production.comfy_channels.service import ChannelComfyGenerationError, ChannelComfyGenerationService
from atnos.services.comfyui_image_generation_service import (
    ComfyUiImageGenerationError,
    ComfyUiImageGenerationService,
    ComfyUiImageResult,
)


class ImageGenerator(Protocol):
    def generate_background(
        self,
        *,
        prompt: str,
        output_path: Path,
        width: int,
        height: int,
        seed: int,
        negative_prompt: str | None = None,
        steps_override: int | None = None,
        cfg_scale_override: float | None = None,
        quality_mode: bool = False,
    ) -> ComfyUiImageResult: ...


class ChannelGenerator(Protocol):
    def generate(self, request) -> GenerationMetadata: ...


class ProvisionResult(StrictModel):
    status: Literal["reused_library", "reused_cache", "generated"]
    asset: AssetRecord
    match: AssetMatch | None = None
    request: AssetGenerationRequest
    fingerprint: str
    generation_need: GenerationNeed | None = None
    comfy_called: bool = False


class AssetProvisionError(RuntimeError):
    pass


class WealthBlueprintAssetProvisioner:
    """Scene Planner → Asset Brain → reuse or Comfy still → register.

    Generation goes through ChannelComfyGenerationService (WB profile), not a
    second router. Never generates final video through ComfyUI.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        brain: WealthBlueprintAssetBrain | None = None,
        image_service: ImageGenerator | None = None,
        channel_service: ChannelGenerator | None = None,
        cache: GenerationCache | None = None,
        persist_catalog: bool = True,
    ) -> None:
        self.settings = settings
        self.brain = brain or WealthBlueprintAssetBrain(settings)
        self.cache = cache or GenerationCache(self.brain.library_root / "generation_cache.json")
        self.persist_catalog = persist_catalog
        self._image_service = image_service
        self._channel_service = channel_service
        if channel_service is None and settings is not None and image_service is None:
            self._channel_service = ChannelComfyGenerationService(settings)
        if image_service is None and self._channel_service is None and settings is not None:
            self._image_service = ComfyUiImageGenerationService(settings)

    def resolve_or_generate(self, query: AssetQuery, *, allow_generate: bool = True) -> ProvisionResult:
        selection = self.brain.select_best(query)
        if selection.status == "found" and selection.match is not None:
            request = AssetGenerationRequest.from_query(query)
            fingerprint = self._fingerprint_for(request)
            return ProvisionResult(
                status="reused_library",
                asset=selection.match.asset,
                match=selection.match,
                request=request,
                fingerprint=fingerprint,
                comfy_called=False,
            )

        need = selection.generation_need
        request = AssetGenerationRequest.from_query(query, generation_need=need)
        return self.fulfill_request(request, generation_need=need, allow_generate=allow_generate)

    def fulfill_request(
        self,
        request: AssetGenerationRequest,
        *,
        generation_need: GenerationNeed | None = None,
        allow_generate: bool = True,
    ) -> ProvisionResult:
        if request.media_kind != "image":
            raise AssetProvisionError("ComfyUI must produce still assets only, never final video")

        alex = self._alex_if_needed(request)
        fingerprint = self._fingerprint_for(request, alex=alex)

        cached = self._reuse_cached(fingerprint)
        if cached is not None:
            return ProvisionResult(
                status="reused_cache",
                asset=cached,
                request=request,
                fingerprint=fingerprint,
                generation_need=generation_need,
                comfy_called=False,
            )

        # Also reuse catalog rows already stamped with this fingerprint
        for asset in self.brain.catalog.assets:
            if asset.request_fingerprint == fingerprint:
                path = self.brain.resolve_file(asset)
                if path.is_file():
                    self.cache.put(
                        fingerprint=fingerprint,
                        asset_id=asset.id,
                        file_path=asset.file_path,
                        workflow_id=(asset.generation.workflow_id if asset.generation else resolve_workflow_id(request)),
                        seed=asset.generation.seed if asset.generation else None,
                    )
                    return ProvisionResult(
                        status="reused_cache",
                        asset=asset,
                        request=request,
                        fingerprint=fingerprint,
                        generation_need=generation_need,
                        comfy_called=False,
                    )

        if not allow_generate:
            raise AssetProvisionError(
                "No library/cache hit and generation disabled"
                + (f": {generation_need.reason}" if generation_need else "")
            )

        asset = self._generate_and_register(request, fingerprint=fingerprint, alex=alex)
        return ProvisionResult(
            status="generated",
            asset=asset,
            request=request,
            fingerprint=fingerprint,
            generation_need=generation_need,
            comfy_called=True,
        )

    def _alex_if_needed(self, request: AssetGenerationRequest) -> AlexAnchors | None:
        if not (request.require_alex_anchors or (request.type == "character" and _is_alex_name(request.character))):
            return None
        try:
            return require_alex_anchors(self.settings)
        except AlexAnchorError as exc:
            raise AssetProvisionError(str(exc)) from exc

    def _fingerprint_for(self, request: AssetGenerationRequest, *, alex: AlexAnchors | None = None) -> str:
        if alex is None and (request.require_alex_anchors or _is_alex_name(request.character)):
            alex = load_alex_anchors(self.settings)
        hashes = alex.reference_hashes if alex is not None else None
        return request_fingerprint(request, reference_hashes=hashes)

    def _reuse_cached(self, fingerprint: str) -> AssetRecord | None:
        entry = self.cache.get(fingerprint)
        if not entry:
            return None
        asset_id = str(entry.get("asset_id") or "")
        asset = self.brain.get(asset_id) if asset_id else None
        if asset is None:
            return None
        path = self.brain.resolve_file(asset)
        if not path.is_file():
            return None
        return asset

    def _generate_and_register(
        self,
        request: AssetGenerationRequest,
        *,
        fingerprint: str,
        alex: AlexAnchors | None,
    ) -> AssetRecord:
        # Prefer existing Comfy channel router (WB profile) — no second router.
        if self._channel_service is not None:
            return self._generate_via_channel(request, fingerprint=fingerprint, alex=alex)

        if self._image_service is None:
            raise AssetProvisionError("ComfyUI image generation service is not configured")

        workflow_id = resolve_workflow_id(request)
        prompt, negative = build_prompt(request, alex=alex)
        seed = request.seed if request.seed is not None else fingerprint_seed(fingerprint)
        asset_id = _suggested_asset_id(request, fingerprint)
        relative = Path("generated") / f"{asset_id}.png"
        output_path = (self.brain.library_root / relative).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        quality_mode = alex is not None
        try:
            result = self._image_service.generate_background(
                prompt=prompt,
                output_path=output_path,
                width=request.width,
                height=request.height,
                seed=seed,
                negative_prompt=negative,
                quality_mode=quality_mode,
            )
        except ComfyUiImageGenerationError as exc:
            raise AssetProvisionError(f"ComfyUI asset generation failed: {exc}") from exc

        if not output_path.is_file() or output_path.stat().st_size < 1024:
            raise AssetProvisionError("ComfyUI returned no usable still image for Asset Brain")

        # Explicit guard: never treat video extensions as success
        if output_path.suffix.casefold() in {".mp4", ".webm", ".mov", ".mkv"}:
            raise AssetProvisionError("ComfyUI video outputs are forbidden for Asset Brain registration")

        generated_at = datetime.now(UTC).isoformat()
        model_name = result.backend
        evidence = str(result.evidence_path) if result.evidence_path else None
        approval_state: Literal["pending", "approved", "rejected"] = "pending"
        # Icons/simple graphics can enter catalog as pending; Alex always pending until review.
        approved = False
        reference_files = [str(path) for path in (alex.reference_files if alex else ())]

        provenance = AssetGenerationProvenance(
            prompt=prompt,
            workflow_id=workflow_id,
            seed=seed,
            model=model_name,
            generated_at=generated_at,
            source_workflow=workflow_id,
            channel=request.channel,
            approval_state=approval_state,
            fingerprint=fingerprint,
            reference_files=reference_files,
            comfy_prompt_id=result.prompt_id,
            backend=result.backend,
            evidence_path=evidence,
            negative_prompt=negative,
        )
        record = AssetRecord(
            id=asset_id,
            type=request.type,
            file_path=relative.as_posix(),
            channel=request.channel,
            character=request.character,
            pose=request.pose,
            emotion=request.emotion,
            direction=request.direction,
            tags=list(request.tags),
            transparent_background=request.transparent_background,
            width=request.width,
            height=request.height,
            version="v01",
            approved=approved,
            source="comfyui_generated",
            license_status="generated_pending_review",
            description=(request.text or f"Generated {request.type} asset").strip()[:500],
            request_fingerprint=fingerprint,
            generation=provenance,
        )
        self._register(record)
        self.cache.put(
            fingerprint=fingerprint,
            asset_id=record.id,
            file_path=record.file_path,
            workflow_id=workflow_id,
            seed=seed,
        )
        return record

    def _generate_via_channel(
        self,
        request: AssetGenerationRequest,
        *,
        fingerprint: str,
        alex: AlexAnchors | None,
    ) -> AssetRecord:
        from atnos.production.order_comfy_routing import map_asset_generation_to_channel_request

        if self._channel_service is None:
            raise AssetProvisionError("Channel Comfy generation service is not configured")

        references = [str(path) for path in (alex.reference_files if alex else ())]
        gen_request = map_asset_generation_to_channel_request(
            channel=request.channel or "wealth_blueprint",
            asset_type=request.type,
            subject=request.character or request.type,
            emotion=request.emotion or "neutral",
            direction=request.direction or "front",
            pose=request.pose or "",
            text=request.text,
            width=request.width,
            height=request.height,
            seed=request.seed if request.seed is not None else fingerprint_seed(fingerprint),
            references=references,
            workflow_id=request.workflow_id,
        )
        try:
            meta = self._channel_service.generate(gen_request)
        except ChannelComfyGenerationError as exc:
            message = str(exc).casefold()
            if "timeout" in message or "timed out" in message:
                # Controlled retry: check cache/job via force_regenerate=False before second call.
                try:
                    meta = self._channel_service.generate(
                        gen_request.model_copy(update={"force_regenerate": False})
                    )
                except ChannelComfyGenerationError as exc2:
                    raise AssetProvisionError(f"Comfy channel asset generation failed: {exc2}") from exc2
            else:
                raise AssetProvisionError(f"Comfy channel asset generation failed: {exc}") from exc

        output_path = Path(meta.output_path)
        if not output_path.is_file() or output_path.stat().st_size < 1024:
            raise AssetProvisionError("Comfy channel returned no usable still image for Asset Brain")
        if output_path.suffix.casefold() in {".mp4", ".webm", ".mov", ".mkv"}:
            raise AssetProvisionError("ComfyUI video outputs are forbidden for Asset Brain registration")

        brain_id = str((meta.generation_settings or {}).get("asset_brain_id") or "")
        if brain_id:
            existing = self.brain.get(brain_id)
            if existing is not None:
                self.cache.put(
                    fingerprint=fingerprint,
                    asset_id=existing.id,
                    file_path=existing.file_path,
                    workflow_id=meta.workflow_id,
                    seed=meta.seed,
                )
                return existing

        # Channel service registration may have failed — register locally as pending.
        asset_id = _suggested_asset_id(request, fingerprint)
        prompt, negative = build_prompt(request, alex=alex)
        provenance = AssetGenerationProvenance(
            prompt=meta.prompt or prompt,
            workflow_id=meta.workflow_id,
            seed=meta.seed,
            model=meta.model or meta.backend,
            generated_at=meta.timestamp,
            source_workflow=meta.workflow_id,
            channel="wealth_blueprint",
            approval_state="pending",
            fingerprint=fingerprint,
            reference_files=list(meta.reference_paths or references),
            comfy_prompt_id=str((meta.generation_settings or {}).get("prompt_id") or "") or None,
            backend=meta.backend,
            evidence_path=str((meta.generation_settings or {}).get("evidence_path") or "") or None,
            negative_prompt=meta.negative_prompt or negative,
        )
        record = AssetRecord(
            id=asset_id,
            type=request.type,
            file_path=str(output_path),
            channel="wealth_blueprint",
            character=request.character,
            pose=request.pose,
            emotion=request.emotion,
            direction=request.direction,
            tags=[*request.tags, "comfy_channel_profile"],
            transparent_background=request.transparent_background,
            width=meta.width,
            height=meta.height,
            version=meta.workflow_version,
            approved=False,
            quality_status="GENERATED",
            source="comfyui_channel_profile",
            license_status="generated_pending_review",
            description=(request.text or f"Generated {request.type} asset").strip()[:500],
            request_fingerprint=fingerprint,
            generation=provenance,
        )
        self._register(record)
        self.cache.put(
            fingerprint=fingerprint,
            asset_id=record.id,
            file_path=record.file_path,
            workflow_id=meta.workflow_id,
            seed=meta.seed,
        )
        return record

    def _register(self, record: AssetRecord) -> None:
        existing = [asset for asset in self.brain.catalog.assets if asset.id != record.id]
        catalog = AssetCatalog(
            schema_version=self.brain.catalog.schema_version,
            channel=self.brain.catalog.channel,
            library_id=self.brain.catalog.library_id,
            assets=[*existing, record],
        )
        self.brain.catalog = catalog
        if self.persist_catalog:
            save_catalog(catalog, self.brain.catalog_file)


def _is_alex_name(value: str | None) -> bool:
    return (value or "").strip().casefold() in {"alex", "wb_alex_01", "wb-alex-01"}


def _suggested_asset_id(request: AssetGenerationRequest, fingerprint: str) -> str:
    parts = [
        request.character or request.type,
        request.pose or "",
        request.emotion or "",
        request.direction or "",
        fingerprint[:8],
    ]
    raw = "_".join(part for part in parts if part).casefold()
    cleaned = re.sub(r"[^a-z0-9_.-]+", "_", raw).strip("_.-")
    if not cleaned:
        cleaned = f"asset_{fingerprint[:12]}"
    return cleaned[:100]
