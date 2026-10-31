from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
from io import BytesIO
import json
import logging
import os
from pathlib import Path
import shutil
from time import monotonic, sleep
from typing import Any
from uuid import uuid4

import httpx
from PIL import Image, UnidentifiedImageError

from atnos.core.config import Settings
from atnos.services.comfyui_runtime_service import ComfyUiRuntimeService
from atnos.services.hardware_guard_service import HardwareFeatureUnavailableError, HardwareGuardService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ComfyUiImageResult:
    output_path: Path
    prompt_id: str
    filename: str
    subfolder: str
    image_type: str
    backend: str
    evidence_path: Path


class ComfyUiImageGenerationError(RuntimeError):
    pass


class ComfyUiDispatchOutcomeUnknownError(ComfyUiImageGenerationError):
    """The provider may have accepted a prompt, so resubmitting could duplicate work."""


class ComfyUiWorkflowRejectedError(ComfyUiImageGenerationError):
    """ComfyUI returned a definite workflow rejection that cannot be fixed by retrying."""


class ComfyUiRuntimeUnavailableError(ComfyUiImageGenerationError):
    """ComfyUI could not become ready before the bounded startup deadline."""


class ComfyUiImageGenerationService:
    Z_IMAGE_TURBO_UNET = "z_image_turbo_bf16.safetensors"
    Z_IMAGE_TURBO_CLIP = "qwen_3_4b.safetensors"
    Z_IMAGE_TURBO_VAE = "ae.safetensors"
    FLUX2_KLEIN_UNET = "flux-2-klein-base-4b.safetensors"
    FLUX2_KLEIN_CLIP = "qwen_3_4b.safetensors"
    FLUX2_KLEIN_VAE = "flux2-vae.safetensors"
    BACKEND_FAILURE_COOLDOWN_HOURS = 24
    DEFAULT_NEGATIVE_PROMPT = (
        "lowres, blurry, waxy skin, malformed hands, deformed fruit, duplicate objects, "
        "text, watermark, logo, oversaturated neon, jpeg artifacts"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.runtime = ComfyUiRuntimeService(settings)
        self._backend_health_path = self.settings.runtime_dir / "comfyui-image-backends.json"
        self._reference_upload_evidence: dict[str, Any] | None = None

    def resolve_checkpoint_name(self) -> str | None:
        # The running Comfy instance is the source of truth. A model present in a
        # different installation is not usable by the active desktop runtime.
        if self.settings.app_env == "test":
            local_name = self._resolve_local_checkpoint_name(ensure_available=True)
            if local_name:
                return local_name
        api_names = self._api_checkpoint_names()
        if api_names:
            explicit = (self.settings.comfyui_default_checkpoint or "").strip()
            if explicit in api_names and self._is_image_checkpoint_name(explicit):
                return explicit
            image_names = [name for name in api_names if self._is_image_checkpoint_name(name)]
            if image_names:
                return sorted(image_names, key=self._image_checkpoint_priority)[0]
        local_name = self._resolve_local_checkpoint_name(ensure_available=True)
        if local_name:
            return local_name
        return None

    def resolve_quality_checkpoint_name(self) -> str | None:
        """Wybierz najlepszy dostępny checkpoint obrazu, nawet kosztem szybkości."""
        api_names = [name for name in self._api_checkpoint_names() if self._is_image_checkpoint_name(name)]
        explicit = (self.settings.comfyui_default_checkpoint or "").strip()
        if explicit in api_names:
            return explicit
        local_candidates = [
            candidate
            for candidate in self.list_checkpoint_candidates()
            if self._is_image_checkpoint_name(candidate.name)
        ]
        if explicit:
            for candidate in local_candidates:
                if candidate.name == explicit:
                    return self._ensure_checkpoint_available_for_comfy(candidate)
        ranked: list[tuple[tuple[int, str], str, Path | None]] = [
            (self._quality_checkpoint_priority(name), name, None) for name in api_names
        ]
        ranked.extend(
            (self._quality_checkpoint_priority(candidate.name), candidate.name, candidate)
            for candidate in local_candidates
        )
        if not ranked:
            return None
        _, name, local_path = min(ranked, key=lambda item: item[0])
        if local_path is not None:
            return self._ensure_checkpoint_available_for_comfy(local_path)
        return name

    def resolve_local_checkpoint_name(self) -> str | None:
        return self._resolve_local_checkpoint_name(ensure_available=False)

    def _resolve_local_checkpoint_name(self, *, ensure_available: bool) -> str | None:
        explicit = (self.settings.comfyui_default_checkpoint or "").strip()
        if explicit:
            explicit_path = Path(explicit)
            if explicit_path.exists() and explicit_path.is_file():
                return (
                    self._ensure_checkpoint_available_for_comfy(explicit_path)
                    if ensure_available
                    else explicit_path.name
                )
            for candidate in self.list_checkpoint_candidates():
                if candidate.name == explicit:
                    return (
                        self._ensure_checkpoint_available_for_comfy(candidate)
                        if ensure_available
                        else candidate.name
                    )
        candidates = self.list_checkpoint_candidates()
        if candidates:
            return (
                self._ensure_checkpoint_available_for_comfy(candidates[0])
                if ensure_available
                else candidates[0].name
            )
        return None

    def can_generate(self) -> bool:
        if self._resolve_local_checkpoint_name(ensure_available=False) is not None:
            return True
        if self.runtime._server_ready():
            if self.resolve_z_image_turbo_models() is not None:
                return True
            if self.resolve_flux2_klein_models() is not None:
                return True
            if self._api_checkpoint_names():
                return True
        return self.is_configured()

    def resolve_z_image_turbo_models(self) -> tuple[str, str, str] | None:
        """Return model names only when the active Desktop runtime exposes all Z-Image inputs."""
        unets = self._api_combo_names("UNETLoader", "unet_name")
        clips = self._api_combo_names("CLIPLoader", "clip_name")
        vaes = self._api_combo_names("VAELoader", "vae_name")
        required = (
            self.Z_IMAGE_TURBO_UNET,
            self.Z_IMAGE_TURBO_CLIP,
            self.Z_IMAGE_TURBO_VAE,
        )
        if required[0] in unets and required[1] in clips and required[2] in vaes:
            return required
        return None

    def resolve_flux2_klein_models(self) -> tuple[str, str, str] | None:
        """Return the verified model names exposed by the active Comfy Desktop instance."""
        unets = self._api_combo_names("UNETLoader", "unet_name")
        clips = self._api_combo_names("CLIPLoader", "clip_name")
        vaes = self._api_combo_names("VAELoader", "vae_name")
        required = (
            self.FLUX2_KLEIN_UNET,
            self.FLUX2_KLEIN_CLIP,
            self.FLUX2_KLEIN_VAE,
        )
        if required[0] in unets and required[1] in clips and required[2] in vaes:
            return required
        return None

    def is_configured(self) -> bool:
        return self.runtime.describe_install().exists

    def generate_image(
        self,
        *,
        prompt: str,
        output_path: Path,
        width: int = 768,
        height: int = 768,
        steps: int = 20,
        seed: int | None = None,
        negative_prompt: str | None = None,
    ) -> ComfyUiImageResult:
        return self.generate_background(
            prompt=prompt,
            output_path=output_path,
            width=width,
            height=height,
            seed=seed if seed is not None else int(uuid4().int % 2_147_483_647),
            negative_prompt=negative_prompt,
            steps_override=steps,
        )

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
        reference_image: Path | None = None,
        reference_denoise: float = 0.6,
        preferred_checkpoint: str | None = None,
    ) -> ComfyUiImageResult:
        self._reference_upload_evidence = None
        try:
            HardwareGuardService.require_gpu("ComfyUI image generation")
        except HardwareFeatureUnavailableError as exc:
            raise ComfyUiImageGenerationError(str(exc)) from exc
        from atnos.services.production_gpu_residency_service import ProductionGpuResidencyService
        ProductionGpuResidencyService(self.settings).prepare_for_comfy()
        last_error: ComfyUiImageGenerationError | None = None
        lowvram = bool(self.settings.comfyui_lowvram)
        reduce_dimensions = False
        attempts = 3
        runtime_incompatible_backends = self._runtime_incompatible_backends()
        disabled_backends = (
            self._persistently_blocked_backends()
            | runtime_incompatible_backends
        )
        # An explicitly pinned checkpoint is an execution contract.  It must
        # not be replaced by an optional backend merely because it is online.
        # Only real references choose img2img; a pin without a reference
        # remains ordinary checkpoint txt2img.
        has_reference = reference_image is not None and Path(reference_image).is_file()
        force_checkpoint = bool((preferred_checkpoint or "").strip()) or has_reference
        if force_checkpoint:
            disabled_backends |= {"z_image_turbo", "flux2_klein"}
        backend = "unselected"
        for attempt in range(attempts):
            attempt_width = min(width, 512) if reduce_dimensions else width
            attempt_height = min(height, 512) if reduce_dimensions else height
            if not self.runtime.ensure_server_running(
                # A cold model/runtime boot can take several minutes. Give the
                # existing owner one bounded window instead of retrying and
                # restarting a runtime that may still be starting.
                wait_timeout_s=min(self.settings.comfyui_generation_timeout_s, 240),
                lowvram=lowvram,
            ):
                raise ComfyUiRuntimeUnavailableError(
                    "ComfyUI runtime did not become ready before the bounded startup deadline."
                )
            else:
                # The first compatibility probe can run while ComfyUI is offline.
                # Re-probe the live runtime before selecting a model backend.
                live_incompatible_backends = self._runtime_incompatible_backends()
                runtime_incompatible_backends |= live_incompatible_backends
                disabled_backends |= live_incompatible_backends
                prefer_quality_checkpoint = quality_mode and bool(
                    {"z_image_turbo", "flux2_klein"} & runtime_incompatible_backends
                )
                z_image_models = (
                    self.resolve_z_image_turbo_models()
                    if (
                        quality_mode
                        and not force_checkpoint
                        and not prefer_quality_checkpoint
                        and "z_image_turbo" not in disabled_backends
                    )
                    else None
                )
                flux_models = (
                    self.resolve_flux2_klein_models()
                    if (
                        quality_mode
                        and not force_checkpoint
                        and not prefer_quality_checkpoint
                        and z_image_models is None
                        and "flux2_klein" not in disabled_backends
                    )
                    else None
                )
                checkpoint_name = None
                if z_image_models is None and flux_models is None:
                    preferred = (preferred_checkpoint or "").strip()
                    if preferred:
                        # Accept basename or relative path segment
                        checkpoint_name = Path(preferred).name
                    elif quality_mode:
                        checkpoint_name = self.resolve_quality_checkpoint_name()
                    else:
                        checkpoint_name = self.resolve_checkpoint_name()
                    if not checkpoint_name:
                        raise ComfyUiImageGenerationError("No ComfyUI checkpoint is configured or discoverable.")
                try:
                    if z_image_models is not None:
                        backend = "z_image_turbo"
                        model_names = list(z_image_models)
                        workflow = self._build_z_image_turbo_workflow(
                            prompt=prompt,
                            width=attempt_width,
                            height=attempt_height,
                            seed=seed,
                            unet_name=z_image_models[0],
                            clip_name=z_image_models[1],
                            vae_name=z_image_models[2],
                            steps_override=steps_override,
                        )
                    elif flux_models is not None:
                        backend = "flux2_klein"
                        model_names = list(flux_models)
                        workflow = self._build_flux2_klein_workflow(
                            prompt=prompt,
                            width=attempt_width,
                            height=attempt_height,
                            seed=seed,
                            unet_name=flux_models[0],
                            clip_name=flux_models[1],
                            vae_name=flux_models[2],
                            negative_prompt=negative_prompt,
                            steps_override=steps_override,
                            cfg_scale_override=cfg_scale_override,
                        )
                    elif has_reference:
                        backend = "checkpoint_img2img"
                        model_names = [str(checkpoint_name)]
                        uploaded = self._upload_input_image(Path(reference_image))
                        workflow = self._build_checkpoint_img2img_workflow(
                            prompt=prompt,
                            width=attempt_width,
                            height=attempt_height,
                            seed=seed,
                            checkpoint_name=str(checkpoint_name),
                            negative_prompt=negative_prompt,
                            steps_override=steps_override,
                            cfg_scale_override=cfg_scale_override,
                            quality_mode=quality_mode,
                            image_name=uploaded,
                            denoise=float(reference_denoise),
                        )
                    else:
                        backend = "checkpoint"
                        model_names = [str(checkpoint_name)]
                        workflow = self._build_workflow(
                            prompt=prompt,
                            width=attempt_width,
                            height=attempt_height,
                            seed=seed,
                            checkpoint_name=checkpoint_name,
                            negative_prompt=negative_prompt,
                            steps_override=steps_override,
                            cfg_scale_override=cfg_scale_override,
                            quality_mode=quality_mode,
                        )
                    prompt_id = self._queue_prompt(workflow)
                    generation_timeout_s = (
                        max(600, self.settings.comfyui_generation_timeout_s)
                        if z_image_models is not None or flux_models is not None
                        else self.settings.comfyui_generation_timeout_s
                    )
                    image_meta = self._wait_for_image(prompt_id, timeout_s=generation_timeout_s)
                    image_bytes = self._download_image(image_meta)
                    self._validate_image_bytes(
                        image_bytes,
                        expected_width=attempt_width,
                        expected_height=attempt_height,
                    )

                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_bytes(image_bytes)
                    evidence_path = self._write_generation_evidence(
                        output_path=output_path,
                        prompt=prompt,
                        prompt_id=prompt_id,
                        backend=backend,
                        model_names=model_names,
                        seed=seed,
                        width=attempt_width,
                        height=attempt_height,
                        image_bytes=image_bytes,
                        workflow=workflow,
                        reference_upload=self._reference_upload_evidence,
                    )
                    self._record_backend_success(backend, model_names=model_names, prompt_id=prompt_id)
                    return ComfyUiImageResult(
                        output_path=output_path.resolve(),
                        prompt_id=prompt_id,
                        filename=str(image_meta["filename"]),
                        subfolder=str(image_meta.get("subfolder") or ""),
                        image_type=str(image_meta.get("type") or "output"),
                        backend=backend,
                        evidence_path=evidence_path,
                    )
                except ComfyUiImageGenerationError as exc:
                    last_error = exc

            # A lost POST response is not proof that ComfyUI did not enqueue the
            # workflow. Retrying or restarting here can create duplicate GPU work.
            # A workflow validation rejection is deterministic and also needs a
            # changed workflow, not another identical dispatch.
            if isinstance(last_error, (ComfyUiDispatchOutcomeUnknownError, ComfyUiWorkflowRejectedError)):
                break

            if attempt >= attempts - 1:
                break
            if last_error is not None and self._looks_like_runtime_exit(str(last_error)):
                self._record_backend_failure(
                    backend,
                    reason=str(last_error),
                    failure_kind="native_runtime_exit",
                )
                logger.error("ComfyUI runtime exited during generation; automatic native-crash retry is disabled")
                break
            if last_error is not None and self._looks_like_pagefile_error(str(last_error)):
                disabled_backends.add(backend)
                self._record_backend_failure(
                    backend,
                    reason=str(last_error),
                    failure_kind="windows_commit_memory",
                )
                logger.warning(
                    "ComfyUI backend %s exceeded Windows commit memory; trying the next quality backend",
                    backend,
                )
                continue
            if last_error is not None and self._looks_like_vram_error(str(last_error)):
                lowvram = True
                reduce_dimensions = True
                logger.warning("ComfyUI generation hit a VRAM-looking error; retrying at 512px with --lowvram")
            logger.warning("Retrying ComfyUI generation after failure: %s", last_error)
            self.runtime.restart_server(wait_timeout_s=180, lowvram=lowvram)

        detail = str(last_error) if last_error is not None else "Unknown ComfyUI image generation failure."
        if isinstance(last_error, ComfyUiDispatchOutcomeUnknownError):
            raise ComfyUiDispatchOutcomeUnknownError(
                "ComfyUI dispatch outcome is unknown; no automatic retry was sent. "
                "Reconcile the existing prompt in the ComfyUI queue/history before another request. "
                f"Last error: {detail}"
            ) from last_error
        if isinstance(last_error, ComfyUiWorkflowRejectedError):
            raise ComfyUiWorkflowRejectedError(
                "ComfyUI rejected the workflow; no automatic retry was sent. "
                f"Correct the workflow before retrying. Last error: {detail}"
            ) from last_error
        raise ComfyUiImageGenerationError(
            "ComfyUI image generation failed after 3 backend attempts. "
            f"Last error: {detail}\n{self.runtime.error_log_tail()}"
        )

    def release_memory(self) -> bool:
        """Unload ComfyUI models and release cached GPU memory."""
        try:
            with httpx.Client(timeout=max(5.0, float(self.settings.comfyui_health_timeout_s) * 5)) as client:
                stats = client.get(f"{self.runtime.base_url.rstrip('/')}/system_stats")
                stats.raise_for_status()
                stats_payload = stats.json()
                if self._is_windows_rocm_runtime(stats_payload):
                    # torch ROCm on Windows can access-violate while ComfyUI
                    # unpatches Flux models. Process termination releases the
                    # same VRAM without exercising that native unload path.
                    result = self.runtime.stop_server(require_idle=True)
                    return bool(result.get("stopped"))
                response = client.post(
                    f"{self.runtime.base_url.rstrip('/')}/free",
                    json={"unload_models": True, "free_memory": True},
                )
            response.raise_for_status()
            return True
        except (httpx.HTTPError, OSError, ValueError):
            return False

    def backend_health_snapshot(self) -> dict[str, Any]:
        """Zwraca zapisany, dowodowy stan backendow obrazu ComfyUI."""
        payload = self._read_backend_health()
        backends = payload.get("backends")
        return backends if isinstance(backends, dict) else {}

    def _persistently_blocked_backends(self) -> set[str]:
        now = datetime.now(UTC)
        blocked: set[str] = set()
        for backend, raw in self.backend_health_snapshot().items():
            if not isinstance(raw, dict) or raw.get("status") != "blocked":
                continue
            try:
                blocked_until = datetime.fromisoformat(str(raw.get("blocked_until") or ""))
            except ValueError:
                continue
            if blocked_until.tzinfo is None:
                blocked_until = blocked_until.replace(tzinfo=UTC)
            if blocked_until > now:
                blocked.add(str(backend))
        if blocked:
            logger.warning("Skipping temporarily blocked ComfyUI image backends: %s", sorted(blocked))
        return blocked

    def _runtime_incompatible_backends(self) -> set[str]:
        """Pomiń backendy z potwierdzonym natywnym crashem na aktywnej platformie."""
        if self.settings.app_env == "test":
            return set()
        try:
            with httpx.Client(timeout=self.settings.comfyui_health_timeout_s * 5) as client:
                response = client.get(f"{self.runtime.base_url}/system_stats")
                response.raise_for_status()
                payload = response.json()
        except Exception:
            return set()

        if self._z_image_is_unsupported_on_runtime(payload):
            logger.warning(
                "Skipping Z-Image Turbo and Flux2 Klein: Windows ROCm on AMD has confirmed native "
                "torch access-violations while loading or switching these models."
            )
            return {"z_image_turbo", "flux2_klein"}
        return set()

    @staticmethod
    def _z_image_is_unsupported_on_runtime(payload: Any) -> bool:
        return ComfyUiImageGenerationService._is_windows_rocm_runtime(payload)

    @staticmethod
    def _is_windows_rocm_runtime(payload: Any) -> bool:
        if os.name != "nt" or not isinstance(payload, dict):
            return False
        system = payload.get("system")
        devices = payload.get("devices")
        if not isinstance(system, dict) or not isinstance(devices, list):
            return False
        pytorch_version = str(system.get("pytorch_version") or "").casefold()
        device_names = " ".join(
            str(device.get("name") or "")
            for device in devices
            if isinstance(device, dict)
        ).casefold()
        return "rocm" in pytorch_version and "amd" in device_names

    def _record_backend_failure(self, backend: str, *, reason: str, failure_kind: str) -> None:
        if not backend or backend == "unselected":
            return
        now = datetime.now(UTC)
        payload = self._read_backend_health()
        backends = payload.setdefault("backends", {})
        if not isinstance(backends, dict):
            backends = {}
            payload["backends"] = backends
        backends[backend] = {
            "status": "blocked",
            "failure_kind": failure_kind,
            "reason": reason[:2_000],
            "failed_at": now.isoformat(),
            "blocked_until": (now + timedelta(hours=self.BACKEND_FAILURE_COOLDOWN_HOURS)).isoformat(),
        }
        self._write_backend_health(payload)

    def _record_backend_success(
        self,
        backend: str,
        *,
        model_names: list[str],
        prompt_id: str,
    ) -> None:
        if not backend or backend == "unselected":
            return
        payload = self._read_backend_health()
        backends = payload.setdefault("backends", {})
        if not isinstance(backends, dict):
            backends = {}
            payload["backends"] = backends
        backends[backend] = {
            "status": "verified",
            "models": model_names,
            "prompt_id": prompt_id,
            "verified_at": datetime.now(UTC).isoformat(),
        }
        self._write_backend_health(payload)

    def _read_backend_health(self) -> dict[str, Any]:
        try:
            payload = json.loads(self._backend_health_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {"version": 1, "backends": {}}
        if not isinstance(payload, dict):
            return {"version": 1, "backends": {}}
        payload.setdefault("version", 1)
        payload.setdefault("backends", {})
        return payload

    def _write_backend_health(self, payload: dict[str, Any]) -> None:
        self._backend_health_path.parent.mkdir(parents=True, exist_ok=True)
        payload["updated_at"] = datetime.now(UTC).isoformat()
        temporary_path = self._backend_health_path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary_path, self._backend_health_path)

    def _queue_prompt(self, workflow: dict[str, Any]) -> str:
        url = f"{self.runtime.base_url}/prompt"
        try:
            with httpx.Client(timeout=self.settings.comfyui_health_timeout_s * 5) as client:
                response = client.post(url, json={"prompt": workflow, "client_id": f"atnos-{uuid4().hex}"})
        except httpx.RequestError as exc:
            raise ComfyUiDispatchOutcomeUnknownError(
                f"ComfyUI prompt POST transport failed ({type(exc).__name__}); the server may have accepted it."
            ) from exc
        except Exception as exc:
            raise ComfyUiImageGenerationError(f"ComfyUI prompt client failed before a response: {exc}") from exc

        if 400 <= response.status_code < 500:
            detail = (response.text or "").strip().replace("\r", " ").replace("\n", " ")[:500]
            raise ComfyUiWorkflowRejectedError(
                f"ComfyUI rejected the workflow with HTTP {response.status_code}: {detail or 'no detail'}"
            )
        if response.status_code >= 500:
            raise ComfyUiDispatchOutcomeUnknownError(
                f"ComfyUI returned HTTP {response.status_code} to the prompt POST; enqueue outcome is unknown."
            )
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise ComfyUiDispatchOutcomeUnknownError(
                "ComfyUI returned an unreadable prompt response; enqueue outcome is unknown."
            ) from exc

        node_errors = payload.get("node_errors") if isinstance(payload, dict) else None
        if node_errors:
            detail = json.dumps(node_errors, ensure_ascii=False, separators=(",", ":"))[:800]
            raise ComfyUiWorkflowRejectedError(f"ComfyUI rejected workflow nodes: {detail}")

        prompt_id = str(payload.get("prompt_id") or "").strip() if isinstance(payload, dict) else ""
        if not prompt_id:
            raise ComfyUiDispatchOutcomeUnknownError(
                "ComfyUI returned no prompt_id; enqueue outcome is unknown and must be reconciled."
            )
        return prompt_id

    def _wait_for_image(self, prompt_id: str, *, timeout_s: int | None = None) -> dict[str, Any]:
        effective_timeout_s = max(30, timeout_s or self.settings.comfyui_generation_timeout_s)
        deadline = monotonic() + effective_timeout_s
        url = f"{self.runtime.base_url}/history/{prompt_id}"
        connection_failures = 0

        while monotonic() < deadline:
            try:
                with httpx.Client(timeout=self.settings.comfyui_health_timeout_s * 5) as client:
                    response = client.get(url)
                    response.raise_for_status()
                    payload = response.json()
            except Exception as exc:
                connection_failures += 1
                if connection_failures >= 3 and not self.runtime._server_ready():
                    raise ComfyUiDispatchOutcomeUnknownError(
                        f"ComfyUI runtime stopped while reconciling accepted prompt {prompt_id}; "
                        f"the generation may still have completed: {type(exc).__name__}"
                    ) from exc
                sleep(2.0)
                continue

            connection_failures = 0

            history = payload.get(prompt_id)
            if isinstance(history, dict):
                outputs = history.get("outputs")
                image_meta = self._extract_first_image(outputs)
                if image_meta is not None:
                    return image_meta
                status = history.get("status")
                if isinstance(status, dict):
                    completed = bool(status.get("completed"))
                    status_str = str(status.get("status_str") or "").strip().lower()
                    if completed and not image_meta:
                        raise ComfyUiImageGenerationError(
                            f"ComfyUI completed prompt {prompt_id}, but no image output was found."
                        )
                    if status_str in {"error", "failed"}:
                        messages = status.get("messages") or history.get("messages")
                        detail = self._history_error_detail(messages)
                        raise ComfyUiImageGenerationError(
                            f"ComfyUI reported failure for prompt {prompt_id}: {detail}"
                        )
            sleep(2.0)

        raise ComfyUiDispatchOutcomeUnknownError(
            f"ComfyUI generation status for accepted prompt {prompt_id} is unknown after "
            f"{effective_timeout_s} seconds; inspect its queue/history before another POST."
        )

    def _download_image(self, image_meta: dict[str, Any]) -> bytes:
        url = f"{self.runtime.base_url}/view"
        params = {
            "filename": str(image_meta["filename"]),
            "subfolder": str(image_meta.get("subfolder") or ""),
            "type": str(image_meta.get("type") or "output"),
        }
        try:
            with httpx.Client(timeout=self.settings.comfyui_health_timeout_s * 5) as client:
                response = client.get(url, params=params)
                response.raise_for_status()
                return response.content
        except Exception as exc:
            raise ComfyUiImageGenerationError(f"ComfyUI image download failed: {exc}") from exc

    @staticmethod
    def _validate_image_bytes(
        image_bytes: bytes,
        *,
        expected_width: int,
        expected_height: int,
    ) -> None:
        if len(image_bytes) < 1_024:
            raise ComfyUiImageGenerationError(
                f"ComfyUI returned an implausibly small image ({len(image_bytes)} bytes)."
            )
        try:
            with Image.open(BytesIO(image_bytes)) as image:
                image.verify()
            with Image.open(BytesIO(image_bytes)) as image:
                actual_size = image.size
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise ComfyUiImageGenerationError("ComfyUI returned an invalid image artifact.") from exc
        expected_size = (int(expected_width), int(expected_height))
        if actual_size != expected_size:
            raise ComfyUiImageGenerationError(
                f"ComfyUI image dimensions are {actual_size}, expected {expected_size}."
            )

    @staticmethod
    def _write_generation_evidence(
        *,
        output_path: Path,
        prompt: str,
        prompt_id: str,
        backend: str,
        model_names: list[str],
        seed: int,
        width: int,
        height: int,
        image_bytes: bytes,
        workflow: dict[str, Any],
        reference_upload: dict[str, Any] | None = None,
    ) -> Path:
        evidence_path = output_path.with_suffix(f"{output_path.suffix}.comfyui.json")
        payload = {
            "status": "verified",
            "generated_at": datetime.now(UTC).isoformat(),
            "backend": backend,
            "models": model_names,
            "seed": int(seed),
            "prompt_id": prompt_id,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "workflow_sha256": hashlib.sha256(
                json.dumps(workflow, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "workflow": workflow,
            "artifact": str(output_path.resolve()),
            "artifact_sha256": hashlib.sha256(image_bytes).hexdigest(),
            "bytes": len(image_bytes),
            "width": int(width),
            "height": int(height),
            "reference_upload": reference_upload,
            "generation_parameters": {
                "width": int(width),
                "height": int(height),
                "seed": int(seed),
            },
        }
        evidence_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return evidence_path.resolve()

    @staticmethod
    def _extract_first_image(outputs: Any) -> dict[str, Any] | None:
        if not isinstance(outputs, dict):
            return None
        for node_output in outputs.values():
            if not isinstance(node_output, dict):
                continue
            images = node_output.get("images")
            if not isinstance(images, list) or not images:
                continue
            first = images[0]
            if isinstance(first, dict) and first.get("filename"):
                return first
        return None

    def list_checkpoint_candidates(self) -> list[Path]:
        return self.runtime.list_checkpoint_candidates()

    def _api_checkpoint_names(self) -> list[str]:
        return self._api_combo_names("CheckpointLoaderSimple", "ckpt_name")

    def _api_combo_names(self, class_type: str, input_name: str) -> list[str]:
        url = f"{self.runtime.base_url}/object_info/{class_type}"
        try:
            with httpx.Client(timeout=self.settings.comfyui_health_timeout_s * 5) as client:
                response = client.get(url)
                response.raise_for_status()
                payload = response.json()
        except Exception:
            return []

        loader_info = payload.get(class_type) if isinstance(payload, dict) else None
        if not isinstance(loader_info, dict):
            return []
        required = ((loader_info.get("input") or {}).get("required") or {})
        ckpt_info = required.get(input_name)
        names: Any = None
        if isinstance(ckpt_info, list) and ckpt_info:
            names = ckpt_info[0]
        elif isinstance(ckpt_info, tuple) and ckpt_info:
            names = ckpt_info[0]
        if not isinstance(names, list):
            return []
        return [str(name).strip() for name in names if str(name).strip()]

    def _build_flux2_klein_workflow(
        self,
        *,
        prompt: str,
        width: int,
        height: int,
        seed: int,
        unet_name: str,
        clip_name: str,
        vae_name: str,
        negative_prompt: str | None,
        steps_override: int | None,
        cfg_scale_override: float | None,
    ) -> dict[str, Any]:
        """Build the API form of Comfy Desktop's official Flux.2 Klein Base 4B workflow."""
        file_prefix = f"atnos_flux2_{uuid4().hex}"
        return {
            "1": {
                "inputs": {"unet_name": unet_name, "weight_dtype": "default"},
                "class_type": "UNETLoader",
            },
            "2": {
                "inputs": {"clip_name": clip_name, "type": "flux2", "device": "default"},
                "class_type": "CLIPLoader",
            },
            "3": {
                "inputs": {"vae_name": vae_name},
                "class_type": "VAELoader",
            },
            "4": {
                "inputs": {"text": prompt, "clip": ["2", 0]},
                "class_type": "CLIPTextEncode",
            },
            "5": {
                "inputs": {"text": (negative_prompt or "").strip(), "clip": ["2", 0]},
                "class_type": "CLIPTextEncode",
            },
            "6": {
                "inputs": {"width": width, "height": height, "batch_size": 1},
                "class_type": "EmptyFlux2LatentImage",
            },
            "7": {
                "inputs": {"noise_seed": int(seed)},
                "class_type": "RandomNoise",
            },
            "8": {
                "inputs": {
                    "model": ["1", 0],
                    "positive": ["4", 0],
                    "negative": ["5", 0],
                    "cfg": float(cfg_scale_override if cfg_scale_override is not None else 5.0),
                },
                "class_type": "CFGGuider",
            },
            "9": {
                "inputs": {"sampler_name": "euler"},
                "class_type": "KSamplerSelect",
            },
            "10": {
                "inputs": {
                    "steps": int(steps_override if steps_override is not None else 20),
                    "width": width,
                    "height": height,
                },
                "class_type": "Flux2Scheduler",
            },
            "11": {
                "inputs": {
                    "noise": ["7", 0],
                    "guider": ["8", 0],
                    "sampler": ["9", 0],
                    "sigmas": ["10", 0],
                    "latent_image": ["6", 0],
                },
                "class_type": "SamplerCustomAdvanced",
            },
            "12": {
                "inputs": {"samples": ["11", 0], "vae": ["3", 0]},
                "class_type": "VAEDecode",
            },
            "13": {
                "inputs": {"filename_prefix": file_prefix, "images": ["12", 0]},
                "class_type": "SaveImage",
            },
        }

    def _build_z_image_turbo_workflow(
        self,
        *,
        prompt: str,
        width: int,
        height: int,
        seed: int,
        unet_name: str,
        clip_name: str,
        vae_name: str,
        steps_override: int | None,
    ) -> dict[str, Any]:
        """Build the API form of Comfy Desktop's official Z-Image Turbo template."""
        file_prefix = f"atnos_z_image_{uuid4().hex}"
        return {
            "1": {
                "inputs": {"unet_name": unet_name, "weight_dtype": "default"},
                "class_type": "UNETLoader",
            },
            "2": {
                "inputs": {"model": ["1", 0], "shift": 3.0},
                "class_type": "ModelSamplingAuraFlow",
            },
            "3": {
                "inputs": {"clip_name": clip_name, "type": "lumina2", "device": "default"},
                "class_type": "CLIPLoader",
            },
            "4": {
                "inputs": {"text": prompt, "clip": ["3", 0]},
                "class_type": "CLIPTextEncode",
            },
            "5": {
                "inputs": {"conditioning": ["4", 0]},
                "class_type": "ConditioningZeroOut",
            },
            "6": {
                "inputs": {"width": width, "height": height, "batch_size": 1},
                "class_type": "EmptySD3LatentImage",
            },
            "7": {
                "inputs": {"vae_name": vae_name},
                "class_type": "VAELoader",
            },
            "8": {
                "inputs": {
                    "model": ["2", 0],
                    "seed": int(seed),
                    "steps": int(steps_override if steps_override is not None else 8),
                    "cfg": 1.0,
                    "sampler_name": "res_multistep",
                    "scheduler": "simple",
                    "positive": ["4", 0],
                    "negative": ["5", 0],
                    "latent_image": ["6", 0],
                    "denoise": 1.0,
                },
                "class_type": "KSampler",
            },
            "9": {
                "inputs": {"samples": ["8", 0], "vae": ["7", 0]},
                "class_type": "VAEDecode",
            },
            "10": {
                "inputs": {"filename_prefix": file_prefix, "images": ["9", 0]},
                "class_type": "SaveImage",
            },
        }

    @staticmethod
    def _is_image_checkpoint_name(name: str) -> bool:
        lowered = str(name or "").casefold()
        video_markers = ("ltx", "wan", "hunyuan_video", "video")
        return bool(lowered) and not any(marker in lowered for marker in video_markers)

    @staticmethod
    def _image_checkpoint_priority(name: str) -> tuple[int, str]:
        lowered = str(name or "").casefold()
        if "sd_xl_turbo" in lowered or "sdxl_turbo" in lowered:
            return (0, lowered)
        if "sd_xl" in lowered or "sdxl" in lowered:
            return (1, lowered)
        if "flux" in lowered:
            return (2, lowered)
        return (3, lowered)

    @staticmethod
    def _quality_checkpoint_priority(name: str) -> tuple[int, str]:
        lowered = str(name or "").casefold()
        if "juggernaut" in lowered:
            return (0, lowered)
        if "flux" in lowered or "z_image" in lowered or "z-image" in lowered:
            return (1, lowered)
        if "sd_xl_base" in lowered or "sdxl_base" in lowered:
            return (2, lowered)
        if ("sd_xl" in lowered or "sdxl" in lowered) and "turbo" not in lowered:
            return (3, lowered)
        if "sd_xl_turbo" in lowered or "sdxl_turbo" in lowered:
            return (5, lowered)
        return (4, lowered)

    def _ensure_checkpoint_available_for_comfy(self, checkpoint_path: Path) -> str:
        state = self.runtime.describe_install()
        checkpoint_dir = state.checkpoint_dir
        if checkpoint_dir is None:
            return checkpoint_path.name
        source = checkpoint_path.resolve()
        target = (checkpoint_dir / source.name).resolve()
        if source == target or target.exists():
            return source.name
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source, target)
            logger.info("Linked ComfyUI checkpoint into runtime models dir: %s -> %s", source, target)
        except OSError:
            logger.warning("Hardlink failed for ComfyUI checkpoint; copying model into runtime models dir: %s", target)
            shutil.copy2(source, target)
        return source.name

    @staticmethod
    def _looks_like_vram_error(message: str) -> bool:
        lowered = message.lower()
        markers = (
            "out of memory",
            "cuda out of memory",
            "vram",
            "memory allocation",
            "not enough memory",
            "allocation failed",
        )
        return any(marker in lowered for marker in markers)

    @staticmethod
    def _looks_like_pagefile_error(message: str) -> bool:
        lowered = message.casefold()
        markers = (
            "os error 1455",
            "paging file is too small",
            "pagefile is too small",
            "plik stronicowania jest za mały",
            "plik stronicowania jest za maĹ‚y",
        )
        return any(marker.casefold() in lowered for marker in markers)

    @staticmethod
    def _history_error_detail(messages: Any) -> str:
        if isinstance(messages, list):
            for message in reversed(messages):
                if not isinstance(message, (list, tuple)) or len(message) < 2:
                    continue
                message_type, payload = message[0], message[1]
                if str(message_type) != "execution_error" or not isinstance(payload, dict):
                    continue
                exception_type = str(payload.get("exception_type") or "execution_error").strip()
                exception_message = str(payload.get("exception_message") or "").strip()
                node_type = str(payload.get("node_type") or "unknown node").strip()
                return f"{exception_type} in {node_type}: {exception_message}".strip()
        return repr(messages)

    @staticmethod
    def _looks_like_runtime_exit(message: str) -> bool:
        lowered = message.casefold()
        return "runtime stopped unexpectedly" in lowered or "native runtime exit" in lowered

    def _upload_input_image(self, source: Path) -> str:
        """Upload a local reference into the active Comfy input folder; return LoadImage name."""
        path = source.resolve()
        if not path.is_file():
            raise ComfyUiImageGenerationError(f"Reference image missing: {path}")
        url = f"{self.runtime.base_url.rstrip('/')}/upload/image"
        try:
            source_bytes = path.read_bytes()
            upload_bytes = source_bytes
            alpha_composited = False
            with Image.open(BytesIO(source_bytes)) as image:
                if "A" in image.getbands() or "transparency" in image.info:
                    # Comfy LoadImage converts RGB separately from the mask.
                    # Hidden checkerboard/paper in an RGBA file otherwise leaks
                    # into VAE conditioning.  Flatten only the upload, retaining
                    # the original source/alpha unchanged in the library.
                    rgba = image.convert("RGBA")
                    matte = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                    rgb = Image.alpha_composite(matte, rgba).convert("RGB")
                    buffer = BytesIO()
                    rgb.save(buffer, format="PNG")
                    upload_bytes = buffer.getvalue()
                    alpha_composited = True
            upload_sha = hashlib.sha256(upload_bytes).hexdigest()
            upload_name = f"atnos-ref-{upload_sha[:24]}.png" if alpha_composited else path.name
            with BytesIO(upload_bytes) as handle, httpx.Client(timeout=60.0) as client:
                response = client.post(
                    url,
                    files={"image": (upload_name, handle, "application/octet-stream")},
                    data={"overwrite": "true"},
                )
                response.raise_for_status()
                payload = response.json()
            self._reference_upload_evidence = {
                "source_path": str(path), "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
                "uploaded_sha256": upload_sha, "alpha_composited": alpha_composited,
                "matte_rgb": [255, 255, 255] if alpha_composited else None,
                "source_unchanged": True, "requested_upload_name": upload_name,
            }
        except (httpx.HTTPError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise ComfyUiImageGenerationError(f"ComfyUI reference upload failed: {exc}") from exc
        name = str(payload.get("name") or upload_name)
        subfolder = str(payload.get("subfolder") or "").strip()
        resolved_image_name = f"{subfolder}/{name}".strip("/") if subfolder else name
        self._reference_upload_evidence['resolved_image_name'] = resolved_image_name
        return resolved_image_name

    def _build_checkpoint_img2img_workflow(
        self,
        *,
        prompt: str,
        width: int,
        height: int,
        seed: int,
        checkpoint_name: str,
        negative_prompt: str | None,
        steps_override: int | None,
        cfg_scale_override: float | None,
        quality_mode: bool,
        image_name: str,
        denoise: float,
    ) -> dict[str, Any]:
        """Core-node reference conditioning (no IPAdapter): LoadImage → scale → VAEEncode → KSampler."""
        file_prefix = f"atnos_ref_{uuid4().hex}"
        quality_steps, quality_cfg, quality_sampler, quality_scheduler = self._quality_sampling_defaults(checkpoint_name)
        default_steps = quality_steps if quality_mode else max(8, int(self.settings.comfyui_steps))
        default_cfg = quality_cfg if quality_mode else self.settings.comfyui_cfg_scale
        clamped = min(0.92, max(0.35, float(denoise)))
        return {
            "1": {
                "inputs": {"ckpt_name": checkpoint_name},
                "class_type": "CheckpointLoaderSimple",
            },
            "2": {
                "inputs": {"text": prompt, "clip": ["1", 1]},
                "class_type": "CLIPTextEncode",
            },
            "3": {
                "inputs": {
                    "text": (
                        (negative_prompt or "").strip()
                        or self.settings.comfyui_negative_prompt.strip()
                        or self.DEFAULT_NEGATIVE_PROMPT
                    ),
                    "clip": ["1", 1],
                },
                "class_type": "CLIPTextEncode",
            },
            "4": {
                "inputs": {"image": image_name},
                "class_type": "LoadImage",
            },
            "5": {
                "inputs": {
                    "image": ["4", 0],
                    "upscale_method": "lanczos",
                    "width": int(width),
                    "height": int(height),
                    "crop": "center",
                },
                "class_type": "ImageScale",
            },
            "6": {
                "inputs": {"pixels": ["5", 0], "vae": ["1", 2]},
                "class_type": "VAEEncode",
            },
            "7": {
                "inputs": {
                    "seed": int(seed),
                    "steps": int(steps_override if steps_override is not None else default_steps),
                    "cfg": float(
                        cfg_scale_override if cfg_scale_override is not None else default_cfg
                    ),
                    "sampler_name": quality_sampler if quality_mode else self.settings.comfyui_sampler_name,
                    "scheduler": quality_scheduler if quality_mode else self.settings.comfyui_scheduler,
                    "denoise": clamped,
                    "model": ["1", 0],
                    "positive": ["2", 0],
                    "negative": ["3", 0],
                    "latent_image": ["6", 0],
                },
                "class_type": "KSampler",
            },
            "8": {
                "inputs": {"samples": ["7", 0], "vae": ["1", 2]},
                "class_type": "VAEDecode",
            },
            "9": {
                "inputs": {"filename_prefix": file_prefix, "images": ["8", 0]},
                "class_type": "SaveImage",
            },
        }

    def _build_workflow(
        self,
        *,
        prompt: str,
        width: int,
        height: int,
        seed: int,
        checkpoint_name: str,
        negative_prompt: str | None,
        steps_override: int | None,
        cfg_scale_override: float | None,
        quality_mode: bool = False,
    ) -> dict[str, Any]:
        file_prefix = f"atnos_{uuid4().hex}"
        quality_steps, quality_cfg, quality_sampler, quality_scheduler = self._quality_sampling_defaults(checkpoint_name)
        default_steps = quality_steps if quality_mode else self.settings.comfyui_steps
        default_cfg = quality_cfg if quality_mode else self.settings.comfyui_cfg_scale
        return {
            "1": {
                "inputs": {"ckpt_name": checkpoint_name},
                "class_type": "CheckpointLoaderSimple",
            },
            "2": {
                "inputs": {"text": prompt, "clip": ["1", 1]},
                "class_type": "CLIPTextEncode",
            },
            "3": {
                "inputs": {
                    "text": (
                        (negative_prompt or "").strip()
                        or self.settings.comfyui_negative_prompt.strip()
                        or self.DEFAULT_NEGATIVE_PROMPT
                    ),
                    "clip": ["1", 1],
                },
                "class_type": "CLIPTextEncode",
            },
            "4": {
                "inputs": {"width": width, "height": height, "batch_size": 1},
                "class_type": "EmptyLatentImage",
            },
            "5": {
                "inputs": {
                    "seed": int(seed),
                    "steps": int(steps_override if steps_override is not None else default_steps),
                    "cfg": float(
                        cfg_scale_override if cfg_scale_override is not None else default_cfg
                    ),
                    "sampler_name": quality_sampler if quality_mode else self.settings.comfyui_sampler_name,
                    "scheduler": quality_scheduler if quality_mode else self.settings.comfyui_scheduler,
                    "denoise": 1,
                    "model": ["1", 0],
                    "positive": ["2", 0],
                    "negative": ["3", 0],
                    "latent_image": ["4", 0],
                },
                "class_type": "KSampler",
            },
            "6": {
                "inputs": {"samples": ["5", 0], "vae": ["1", 2]},
                "class_type": "VAEDecode",
            },
            "7": {
                "inputs": {"filename_prefix": file_prefix, "images": ["6", 0]},
                "class_type": "SaveImage",
            },
        }

    @staticmethod
    def _quality_sampling_defaults(checkpoint_name: str) -> tuple[int, float, str, str]:
        lowered = str(checkpoint_name or "").casefold()
        if "turbo" in lowered:
            return (4, 1.5, "euler", "normal")
        if "juggernaut" in lowered:
            return (30, 5.5, "dpmpp_2m_sde", "karras")
        if "sd_xl" in lowered or "sdxl" in lowered:
            return (30, 6.5, "dpmpp_2m_sde", "karras")
        return (24, 5.0, "dpmpp_2m", "karras")
