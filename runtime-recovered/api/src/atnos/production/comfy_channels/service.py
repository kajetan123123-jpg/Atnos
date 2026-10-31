"""Channel-aware Comfy generation adapter (validated workflows only)."""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from atnos.core.config import Settings
from atnos.production.comfy_channels.cache import ChannelGenerationCache, fingerprint_request
from atnos.production.comfy_channels.isolation import (
    ChannelIsolationError,
    assert_output_path_isolated,
    assert_reference_belongs,
    assert_request_isolated,
)
from atnos.production.comfy_channels.models import GenerationMetadata, GenerationRequest
from atnos.production.comfy_channels.profiles import find_workflow, generated_root, load_profile
from atnos.production.comfy_channels.prompts import (
    WB_PROMPT_COMPILER_VERSION,
    build_prompt,
    negative_prompt_with_request,
    resolve_aspect,
)
from atnos.production.comfy_channels.validation import WorkflowValidationError, validate_workflow_for_generation
from atnos.services.comfyui_image_generation_service import (
    ComfyUiDispatchOutcomeUnknownError,
    ComfyUiImageGenerationError,
    ComfyUiRuntimeUnavailableError,
    ComfyUiWorkflowRejectedError,
    ComfyUiImageGenerationService,
)
from atnos.services.gpu_runtime_lock_service import GpuRuntimeLockService

MAX_CONCURRENT_COMFY_GPU_JOBS = 1


class ChannelComfyGenerationError(RuntimeError):
    pass


class ChannelComfyRuntimeUnavailableError(ChannelComfyGenerationError):
    """The Comfy dependency was unavailable before image generation began."""


class ScopedLocalImageGenerationService(ComfyUiImageGenerationService):
    """Normal Comfy implementation with durable admission at its real POST.

    Kept outside the fingerprinted shared implementation: existing one-shot Flux
    authorizations stay intact. Only an explicitly registered local action uses it.
    """

    def __init__(self, settings):
        super().__init__(settings)
        from atnos.production.local_image_dispatch_budget import active_local_image_dispatch_scope
        self._dispatch_scope = active_local_image_dispatch_scope()
        if self._dispatch_scope is None:
            raise ValueError("Scoped local image service needs an active registered action")
        self._dispatch_reserved = False
        self._dispatch_unknown = False

    def _unknown(self, exc):
        from atnos.production.local_image_dispatch_budget import LocalImageDispatchBlocked, record_local_image_dispatch
        if self._dispatch_reserved and not self._dispatch_unknown:
            record_local_image_dispatch(self._dispatch_scope, status="unknown", error_kind=type(exc).__name__)
            self._dispatch_unknown = True
        # Not a ComfyUiImageGenerationError: neither inner backend retries nor
        # the channel fallback may turn this into a second POST.
        raise LocalImageDispatchBlocked(
            "Local image dispatch outcome unknown; inspect the recorded provider evidence before another POST",
            category="execution_outcome_unknown",
        ) from exc

    def _queue_prompt(self, workflow):
        from urllib.parse import urlparse
        from atnos.production.local_image_dispatch_budget import (
            LocalImageDispatchBlocked, reserve_local_image_dispatch, record_local_image_dispatch,
        )
        if self._dispatch_reserved:
            raise LocalImageDispatchBlocked("One POST per local candidate: an internal retry cannot dispatch again")
        target = urlparse(self.runtime.base_url)
        if target.scheme not in {"http", "https"} or target.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise LocalImageDispatchBlocked("Bounded local image action cannot dispatch to a remote or paid image endpoint")
        reserve_local_image_dispatch(self._dispatch_scope, workflow,
                                     reference_upload=self._reference_upload_evidence)
        self._dispatch_reserved = True
        try:
            prompt_id = super()._queue_prompt(workflow)
            record_local_image_dispatch(self._dispatch_scope, status="submitted", prompt_id=prompt_id)
            return prompt_id
        except ComfyUiWorkflowRejectedError as exc:
            record_local_image_dispatch(
                self._dispatch_scope,
                status="rejected",
                error_kind=type(exc).__name__,
                error_detail=str(exc),
            )
            raise LocalImageDispatchBlocked(
                "ComfyUI rejected the workflow; this candidate is recorded as rejected and cannot be retried unchanged",
                category="workflow_rejected",
            ) from exc
        except ComfyUiDispatchOutcomeUnknownError as exc:
            self._unknown(exc)
        except Exception as exc:
            self._unknown(exc)

    def _wait_for_image(self, *args, **kwargs):
        try:
            return super()._wait_for_image(*args, **kwargs)
        except ComfyUiImageGenerationError as exc:
            self._unknown(exc)

    def _download_image(self, *args, **kwargs):
        try:
            return super()._download_image(*args, **kwargs)
        except ComfyUiImageGenerationError as exc:
            self._unknown(exc)

    def _validate_image_bytes(self, *args, **kwargs):
        try:
            return super()._validate_image_bytes(*args, **kwargs)
        except ComfyUiImageGenerationError as exc:
            self._unknown(exc)

    def generate_background(self, **kwargs):
        from atnos.production.local_image_dispatch_budget import LocalImageDispatchBlocked, record_local_image_dispatch
        try:
            result = super().generate_background(**kwargs)
        except LocalImageDispatchBlocked:
            raise
        except Exception as exc:
            if self._dispatch_reserved:
                self._unknown(exc)
            raise  # GPU/runtime startup before POST does not consume a request.
        try:
            record_local_image_dispatch(self._dispatch_scope, status="completed", result=result)
        except Exception as exc:
            self._unknown(exc)
        return result


def _require_wb_quality_evidence(evidence_path: Path, checkpoint: str) -> None:
    """The rendered graph, rather than a requested tier label, proves the model."""
    try:
        evidence = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
        actual_models = [Path(str(name)).name.casefold() for name in evidence["models"]]
        checkpoint_nodes = [
            Path(str(node["inputs"]["ckpt_name"])).name.casefold()
            for node in evidence["workflow"].values()
            if node.get("class_type") == "CheckpointLoaderSimple"
        ]
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ChannelComfyGenerationError(
            "WB_STYLE_QUALITY_BLOCKED: Missing readable checkpoint generation evidence."
        ) from exc
    expected = Path(checkpoint).name.casefold()
    if actual_models != [expected] or checkpoint_nodes != [expected]:
        raise ChannelComfyGenerationError(
            "WB_STYLE_QUALITY_BLOCKED: Generated graph does not use the required quality checkpoint."
        )


class ChannelComfyGenerationService:
    """channel_id → profile → workflow → shared Comfy runtime → isolated output."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        from atnos.production.local_image_dispatch_budget import active_local_image_dispatch_scope
        self.images = (ScopedLocalImageGenerationService(settings) if active_local_image_dispatch_scope()
                       else ComfyUiImageGenerationService(settings))
        self.lock = GpuRuntimeLockService(settings)

    def generate(self, request: GenerationRequest) -> GenerationMetadata:
        if request.channel == "dark_chronicles":
            return self._generate_dc_with_visual_qc(request)
        return self._generate_single(request)

    def _generate_dc_with_visual_qc(self, request: GenerationRequest) -> GenerationMetadata:
        """Post-generation visual QC with bounded regeneration (max 3)."""
        from atnos.production.comfy_channels.dark_chronicles_visual_qc import (
            DcVisualQcPolicy,
            adjust_request_for_failures,
            resolve_gore_profile,
            run_dc_visual_qc,
        )

        policy = DcVisualQcPolicy(gore_profile=resolve_gore_profile(request))
        current = request
        last_meta: GenerationMetadata | None = None
        attempts_log: list[dict[str, Any]] = []
        for attempt in range(policy.max_attempts):
            last_meta = self._generate_single(current)
            qc = run_dc_visual_qc(
                Path(last_meta.output_path),
                request=current,
                policy=policy,
                settings=self.settings,
                expected_width=last_meta.width,
                expected_height=last_meta.height,
            )
            attempt_row = {
                "attempt": attempt + 1,
                "passed": qc.passed,
                "verdict": qc.verdict,
                "failure_reasons": list(qc.failure_reasons),
                "seed": current.seed,
                "output_path": last_meta.output_path,
            }
            attempts_log.append(attempt_row)
            settings = dict(last_meta.generation_settings or {})
            settings["dc_visual_qc"] = qc.as_dict()
            settings["dc_visual_qc_attempt"] = attempt + 1
            settings["dc_visual_qc_attempts"] = attempts_log
            settings["dc_visual_qc_passed"] = qc.passed
            if not qc.passed:
                settings["validation_verdict"] = "FAIL_VISUAL_QC"
            elif settings.get("validation_verdict") == "VALIDATED":
                settings["validation_verdict"] = "VALIDATED"
            last_meta = last_meta.model_copy(update={"generation_settings": settings})
            meta_path = Path(last_meta.output_path).with_name("metadata.json")
            meta_path.write_text(last_meta.model_dump_json(indent=2) + "\n", encoding="utf-8")
            if qc.passed:
                return last_meta
            if attempt + 1 >= policy.max_attempts:
                settings["dc_visual_qc_exhausted"] = True
                last_meta = last_meta.model_copy(update={"generation_settings": settings})
                meta_path.write_text(last_meta.model_dump_json(indent=2) + "\n", encoding="utf-8")
                return last_meta
            current = adjust_request_for_failures(current, qc, attempt=attempt)
        assert last_meta is not None
        return last_meta

    def _generate_single(self, request: GenerationRequest) -> GenerationMetadata:
        """channel_id → profile → workflow → shared Comfy runtime → isolated output (one attempt)."""
        profile = load_profile(self.settings, request.channel)
        try:
            assert_request_isolated(profile, request)
            workflow = find_workflow(profile, request.workflow)
            validation = validate_workflow_for_generation(self.settings, profile, workflow)
        except (ChannelIsolationError, ValueError, WorkflowValidationError) as exc:
            raise ChannelComfyGenerationError(str(exc)) from exc

        if workflow.require_references and not (request.references or profile.reference_files):
            raise ChannelComfyGenerationError(
                f"Workflow {workflow.workflow_id} requires reference_asset_ids / reference files"
            )

        width, height = resolve_aspect(
            request, default_w=workflow.default_width, default_h=workflow.default_height
        )
        prompt = build_prompt(request)
        neg = negative_prompt_with_request(profile.channel_id, request)
        seed = int(request.seed if request.seed is not None else random.randint(1, 2_147_483_647))

        ref_paths: list[str] = []
        for ref in request.references:
            # Allow absolute paths or filenames matched against profile refs
            candidate = Path(ref)
            if not candidate.is_file():
                matched = next((Path(p) for p in profile.reference_files if Path(p).name == ref or p.endswith(ref)), None)
                if matched is None:
                    raise ChannelComfyGenerationError(f"Unknown reference id/path: {ref}")
                candidate = matched
            try:
                assert_reference_belongs(profile, candidate)
            except ChannelIsolationError as exc:
                raise ChannelComfyGenerationError(str(exc)) from exc
            ref_paths.append(str(candidate))
        if workflow.require_references and not ref_paths and profile.reference_files:
            # Default to first approved anchors (Alex / Niko sheets)
            for p in profile.reference_files[:2]:
                ref_paths.append(p)

        from atnos.production.comfy_channels.model_profiles import load_model_profile, resolve_generation_tier

        model_prof = load_model_profile(self.settings, profile.channel_id)
        ref_mode = str(model_prof.get("reference_mode") or "core_img2img")
        ref_denoise = float(model_prof.get("reference_denoise") or 0.6)
        if request.denoise_override is not None:
            ref_denoise = float(request.denoise_override)
        tier = resolve_generation_tier(
            model_prof,
            channel=profile.channel_id,
            asset_type=workflow.asset_type or request.asset_type,
            workflow_title=workflow.title,
            visual_style_id=request.visual_style_id,
        )
        primary_model = str(tier.get("preferred_checkpoint") or model_prof.get("primary_model") or "")
        required_style_quality = (
            profile.channel_id == "wealth_blueprint" and (
                request.visual_style_id in {"A", "B", "C", "D"}
                or bool(model_prof.get("required_quality"))
            )
        )
        requested_tier = str(tier.get("tier") or "primary")
        requested_checkpoint = primary_model
        if required_style_quality and (not tier.get("quality_mode") or requested_tier != "quality" or not primary_model):
            raise ChannelComfyGenerationError(
                "WB_STYLE_QUALITY_BLOCKED: Style production requires a configured quality checkpoint."
            )
        # img2img when profile/preset says core_img2img and we have refs.
        # Character channels: subject present. DC environments: explicit refs on request.
        use_img2img = bool(ref_paths) and ref_mode == "core_img2img" and (
            workflow.require_references
            or (profile.channel_id in {"wealth_blueprint", "wonder_crew"} and bool(request.subject))
            or (profile.channel_id == "dark_chronicles" and bool(request.references))
        )
        if profile.channel_id == 'wealth_blueprint' and request.references and not use_img2img:
            raise ChannelComfyGenerationError(
                'Explicit WB composition reference requires supported pixel conditioning; text-only fallback is forbidden')

        # Always keep anchor names in the prompt for provenance + CLIP guidance.
        if ref_paths:
            prompt = (
                f"{prompt}. Stay consistent with approved reference sheet(s): "
                + ", ".join(Path(p).name for p in ref_paths)
            )

        request_id = uuid4().hex[:16]
        out_dir = generated_root(self.settings) / profile.output_subdir / workflow.asset_type / request_id
        out_dir.mkdir(parents=True, exist_ok=True)
        image_path = out_dir / "image.png"
        meta_path = out_dir / "metadata.json"

        fingerprint = fingerprint_request(
            {
                "channel": profile.channel_id,
                "workflow_id": workflow.workflow_id,
                "workflow_version": workflow.workflow_version,
                "models": [primary_model] if primary_model else profile.image_models,
                "references": ref_paths,
                "prompt": prompt,
                "negative": neg,
                "width": width,
                "height": height,
                "seed": seed,
                "backend_preference": workflow.backend_preference,
                "reference_mode": ref_mode if use_img2img else "none",
                "reference_denoise": ref_denoise if use_img2img else None,
                "illustration_checkpoint_trial": model_prof.get("illustration_trial"),
            }
        )
        cache = ChannelGenerationCache(generated_root(self.settings) / profile.output_subdir / "_cache.json")
        if not request.force_regenerate:
            hit = cache.get(fingerprint)
            if hit and Path(str(hit.get("output_path") or "")).is_file():
                meta = GenerationMetadata.model_validate({**hit, "cache_hit": True, "request_id": request_id})
                if required_style_quality:
                    actual = meta.generation_settings
                    if (actual.get("quality_mode") is not True or actual.get("fallback_used")
                            or Path(meta.model).name.casefold() != Path(primary_model).name.casefold()):
                        raise ChannelComfyGenerationError(
                            "WB_STYLE_QUALITY_BLOCKED: Cached candidate does not satisfy the required quality tier."
                        )
                    _require_wb_quality_evidence(Path(str(actual.get("evidence_path") or "")), primary_model)
                meta_path.write_text(meta.model_dump_json(indent=2) + "\n", encoding="utf-8")
                return meta

        if MAX_CONCURRENT_COMFY_GPU_JOBS != 1:
            raise ChannelComfyGenerationError("Invalid GPU concurrency policy")

        lease = None
        try:
            lease = self.lock.acquire(owner=f"comfy-channel:{profile.channel_id}:{request_id}", timeout_s=600)
        except Exception as exc:  # noqa: BLE001
            raise ChannelComfyGenerationError(f"GPU lock unavailable: {exc}") from exc

        try:
            # Production never depends on Flux2/Z-Image (OPTIONAL/BLOCKED on AMD ROCm).
            quality_mode = bool(tier.get("quality_mode"))
            fallback_model = str(tier.get("fallback_checkpoint") or model_prof.get("fallback_model") or "")
            fallback_quality_mode = bool(tier.get("fallback_quality_mode"))
            fallback_used = False
            fallback_error: str | None = None
            reference_image = Path(ref_paths[0]) if use_img2img and ref_paths else None
            try:
                result = self.images.generate_background(
                    prompt=prompt,
                    output_path=image_path,
                    width=width,
                    height=height,
                    seed=seed,
                    negative_prompt=neg,
                    quality_mode=quality_mode,
                    reference_image=reference_image,
                    reference_denoise=ref_denoise,
                    preferred_checkpoint=primary_model or None,
                )
            except ComfyUiImageGenerationError as exc:
                if isinstance(exc, ComfyUiRuntimeUnavailableError):
                    failure_path = out_dir / "runtime_failure.json"
                    failure_path.write_text(json.dumps({
                        "status": "blocked", "code": "COMFY_RUNTIME_UNAVAILABLE",
                        "image_generated": False,
                        "requested_generation_tier": requested_tier,
                        "requested_checkpoint": requested_checkpoint,
                        "visual_style_id": request.visual_style_id,
                        "fallback_used": False, "fallback_allowed": False,
                        "reason": str(exc), "timestamp": datetime.now(UTC).isoformat(),
                    }, indent=2) + "\n", encoding="utf-8")
                    raise ChannelComfyRuntimeUnavailableError(
                        f"COMFY_RUNTIME_UNAVAILABLE: ComfyUI did not become ready; see {failure_path}"
                    ) from exc
                if required_style_quality:
                    failure_path = out_dir / "generation_failure.json"
                    failure_path.write_text(json.dumps({
                        "status": "blocked", "code": "WB_STYLE_QUALITY_BLOCKED",
                        "requested_generation_tier": requested_tier,
                        "requested_checkpoint": requested_checkpoint,
                        "visual_style_id": request.visual_style_id,
                        "fallback_used": False, "fallback_allowed": False,
                        "reason": str(exc), "timestamp": datetime.now(UTC).isoformat(),
                    }, indent=2) + "\n", encoding="utf-8")
                    # A runtime failure is not permission to change the medium's
                    # generation tier. Keep the detailed cause in the receipt;
                    # this short non-transient message also prevents timeout-text
                    # retries in the order bridge from duplicating the blocked job.
                    raise ChannelComfyGenerationError(
                        "WB_STYLE_QUALITY_BLOCKED: Required quality checkpoint failed; "
                        f"faster fallback is disabled. Details: {failure_path}"
                    ) from exc
                # Explicit fallback — never silent. Record primary failure.
                fallback_error = str(exc)
                if fallback_model and primary_model and Path(fallback_model).name != Path(primary_model).name:
                    try:
                        result = self.images.generate_background(
                            prompt=prompt,
                            output_path=image_path,
                            width=width,
                            height=height,
                            seed=seed,
                            negative_prompt=neg,
                            quality_mode=fallback_quality_mode,
                            reference_image=reference_image,
                            reference_denoise=ref_denoise,
                            preferred_checkpoint=fallback_model,
                        )
                        primary_model = fallback_model
                        quality_mode = fallback_quality_mode
                        fallback_used = True
                    except ComfyUiImageGenerationError as exc2:
                        raise ChannelComfyGenerationError(
                            f"Primary checkpoint failed ({fallback_error}); fallback also failed: {exc2}"
                        ) from exc2
                else:
                    raise ChannelComfyGenerationError(str(exc)) from exc

            if required_style_quality:
                _require_wb_quality_evidence(result.evidence_path, requested_checkpoint)
            assert_output_path_isolated(profile, image_path, generated_root(self.settings))
            if not image_path.is_file() or image_path.stat().st_size < 1024:
                raise ChannelComfyGenerationError("Comfy output missing or too small")

            alpha_applied = False
            source_image_path = out_dir / "image_source.png"
            source_image_path.write_bytes(image_path.read_bytes())
            if workflow.allow_transparent_bg:
                try:
                    from atnos.services.local_cutout_service import remove_background

                    cutout_path = out_dir / "image_cutout.png"
                    remove_background(image_path, cutout_path,
                        postprocess='solid_paper' if request.asset_type == 'solid_paper_prop' else 'improved')
                    if cutout_path.is_file() and cutout_path.stat().st_size > 1024:
                        image_path.write_bytes(cutout_path.read_bytes())
                        alpha_applied = True
                except Exception:
                    alpha_applied = False

            conditioning = "core_img2img" if reference_image is not None else (
                "prompt_anchor_names" if ref_paths else "none"
            )
            meta = GenerationMetadata(
                request_id=request_id,
                channel=profile.channel_id,
                workflow_id=workflow.workflow_id,
                workflow_version=workflow.workflow_version,
                backend=result.backend,
                model=primary_model or str(getattr(result, "filename", "") or result.backend),
                seed=seed,
                prompt=prompt,
                negative_prompt=neg,
                width=width,
                height=height,
                references=list(request.references),
                reference_paths=ref_paths,
                output_path=str(image_path),
                fingerprint=fingerprint,
                cache_hit=False,
                timestamp=datetime.now(UTC).isoformat(),
                generation_settings={
                    "aspect_ratio": request.aspect_ratio,
                    "quality_mode": quality_mode,
                    "generation_tier": "fast" if fallback_used and not quality_mode else requested_tier,
                    "requested_generation_tier": requested_tier,
                    "requested_checkpoint": requested_checkpoint,
                    "required_style_quality": required_style_quality,
                    "prompt_compiler_version": WB_PROMPT_COMPILER_VERSION if profile.channel_id == 'wealth_blueprint' else None,
                    "fallback_used": fallback_used,
                    "fallback_error": fallback_error,
                    "fallback_checkpoint": fallback_model if fallback_used else None,
                    "allow_transparent_bg": workflow.allow_transparent_bg,
                    "alpha_applied": alpha_applied,
                    "source_image_path": str(source_image_path),
                    "forbid_text_in_image": workflow.forbid_text_in_image,
                    "evidence_path": str(result.evidence_path),
                    "prompt_id": result.prompt_id,
                    "shared_models": True,
                    "ipadapter": False,
                    "reference_conditioning": conditioning,
                    "reference_denoise": ref_denoise if reference_image is not None else None,
                    "preferred_checkpoint": primary_model,
                    "workflow_validation": validation,
                    "asset_brain_id": None,
                    "quality_gate": None,
                    "approved": False,
                    "visual_style_id": request.visual_style_id,
                    "visual_style_tags": list(request.visual_style_tags),
                    "visual_style_definition_hash": request.visual_style_definition_hash,
                    "no_flux_dependency": True,
                    "illustration_checkpoint_trial": model_prof.get("illustration_trial"),
                },
            )
            meta_path.write_text(meta.model_dump_json(indent=2) + "\n", encoding="utf-8")

            from atnos.production.comfy_channels.quality_gate import run_asset_quality_gate

            character_required = profile.channel_id in {"wealth_blueprint", "wonder_crew"} and (
                workflow.require_references or workflow.asset_type.startswith("character")
            )
            gate = run_asset_quality_gate(
                profile=profile,
                request=request,
                meta=meta,
                generated_root=generated_root(self.settings),
                require_alpha=bool(workflow.allow_transparent_bg and alpha_applied),
                character_consistency_required=character_required,
            )
            meta.generation_settings["quality_gate"] = gate.as_dict()
            # Never auto-approve; VALIDATED ≠ approved.
            meta.generation_settings["approved"] = False
            meta.generation_settings["validation_verdict"] = gate.verdict

            if profile.channel_id == "wealth_blueprint":
                asset_id = _register_wealth_asset_brain(
                    self.settings, request, meta, workflow, gate_passed=gate.passed
                )
                meta.generation_settings["asset_brain_id"] = asset_id
                if request.visual_style_id and not asset_id:
                    meta.generation_settings["registration_error"] = "styled_candidate_not_in_asset_brain"
                    meta_path.write_text(meta.model_dump_json(indent=2) + "\n", encoding="utf-8")
                    raise ChannelComfyGenerationError("Styled candidate could not be registered for review")
            meta_path.write_text(meta.model_dump_json(indent=2) + "\n", encoding="utf-8")
            cache.put(fingerprint, json.loads(meta.model_dump_json()))
            return meta
        finally:
            if lease is not None:
                try:
                    self.lock.release(lease)
                except Exception:
                    pass


def _register_wealth_asset_brain(
    settings: Settings,
    request: GenerationRequest,
    meta: GenerationMetadata,
    workflow: Any,
    *,
    gate_passed: bool = False,
) -> str | None:
    """Best-effort Asset Brain registration for Wealth Blueprint stills only."""
    try:
        from atnos.production.asset_brain.models import (
            AssetCatalog,
            AssetGenerationProvenance,
            AssetRecord,
        )
        from atnos.production.asset_brain.service import WealthBlueprintAssetBrain

        brain = WealthBlueprintAssetBrain(settings)
        asset_type = "character" if workflow.asset_type == "character" else "icon"
        if workflow.asset_type == "background":
            asset_type = "background"
        asset_id = f"comfy_{meta.channel}_{workflow.workflow_id}_{meta.request_id}"[:120]
        provenance = AssetGenerationProvenance(
            prompt=meta.prompt,
            workflow_id=meta.workflow_id,
            seed=meta.seed,
            model=meta.model or meta.backend,
            generated_at=meta.timestamp,
            source_workflow=meta.workflow_id,
            channel=meta.channel,
            approval_state="pending",
            fingerprint=meta.fingerprint,
            reference_files=list(meta.reference_paths),
            comfy_prompt_id=str(meta.generation_settings.get("prompt_id") or "") or None,
            backend=meta.backend,
            evidence_path=str(meta.generation_settings.get("evidence_path") or "") or None,
            negative_prompt=meta.negative_prompt,
        )
        # Store absolute path; catalog consumers resolve relative to library root when needed.
        record = AssetRecord(
            id=asset_id,
            type=asset_type,  # type: ignore[arg-type]
            file_path=meta.output_path,
            channel=meta.channel,
            character=(request.subject or None) if asset_type == "character" else None,
            pose=request.direction or None,
            emotion=request.emotion or None,
            direction=request.direction or None,
            tags=[
                "comfy_channel",
                workflow.title.casefold(),
                f"gate:{(meta.generation_settings or {}).get('validation_verdict') or 'pending'}".casefold(),
                "never_auto_approved",
                *request.visual_style_tags,
            ],
            transparent_background=bool((meta.generation_settings or {}).get("alpha_applied")),
            width=meta.width,
            height=meta.height,
            version=meta.workflow_version,
            approved=False,
            quality_status="GENERATED",
            source="comfyui_channel_profile",
            license_status="generated_pending_review",
            description=(
                f"{workflow.title} {request.subject} {request.emotion} "
                f"[gate={(meta.generation_settings or {}).get('validation_verdict')}]"
            ).strip()[:500],
            request_fingerprint=meta.fingerprint,
            generation=provenance,
        )
        from atnos.production.asset_brain.approval import AssetApprovalService

        existing = [a for a in brain.catalog.assets if a.id != record.id]
        brain.catalog = AssetCatalog(
            schema_version=brain.catalog.schema_version,
            channel=brain.catalog.channel,
            library_id=brain.catalog.library_id,
            assets=[*existing, record],
        )
        ingested = AssetApprovalService(brain).ingest_generated(record, run_pregate=True)
        return ingested.id
    except Exception:
        return None


def validate_generation_request(settings: Settings, payload: dict[str, Any]) -> GenerationRequest:
    """Reject arbitrary workflow paths — only registered workflow ids/titles."""
    request = GenerationRequest.model_validate(payload)
    profile = load_profile(settings, request.channel)
    find_workflow(profile, request.workflow)  # raises if unknown
    assert_request_isolated(profile, request)
    return request
