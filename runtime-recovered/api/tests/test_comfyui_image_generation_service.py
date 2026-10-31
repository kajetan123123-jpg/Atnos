from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

from PIL import Image
import httpx
import pytest

from atnos.services import comfyui_image_generation_service as comfy_generation
from atnos.services.comfyui_image_generation_service import (
    ComfyUiDispatchOutcomeUnknownError,
    ComfyUiImageGenerationError,
    ComfyUiRuntimeUnavailableError,
    ComfyUiWorkflowRejectedError,
    ComfyUiImageGenerationService,
)
from atnos.services.hardware_guard_service import HardwareGuardService

from .test_support import build_test_settings, make_temp_dir


def _png_bytes(width: int = 512, height: int = 896) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), color=(14, 22, 31)).save(buffer, format="PNG")
    return buffer.getvalue()


PNG_BYTES = _png_bytes()


def _create_portable_install(root: Path) -> None:
    install_dir = root / "ComfyUI_windows_portable"
    (install_dir / "ComfyUI" / "models" / "checkpoints").mkdir(parents=True)
    (install_dir / "python_embeded").mkdir(parents=True)
    (install_dir / "ComfyUI" / "main.py").write_text("print('comfy')", encoding="utf-8")
    (install_dir / "python_embeded" / "python.exe").write_text("", encoding="utf-8")


def test_release_memory_posts_comfyui_free_request(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, bool]]] = []

    class Response:
        def json(self) -> dict[str, object]:
            return {"system": {"pytorch_version": "2.9.1+cpu"}, "devices": []}

        def raise_for_status(self) -> None:
            return None

    class Client:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        def __enter__(self) -> "Client":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, url: str) -> Response:
            return Response()

        def post(self, url: str, *, json: dict[str, bool]) -> Response:
            calls.append((url, json))
            return Response()

    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        _create_portable_install(root)
        service = ComfyUiImageGenerationService(build_test_settings(root))
        monkeypatch.setattr(
            "atnos.services.comfyui_image_generation_service.httpx.Client",
            Client,
        )

        assert service.release_memory() is True
        assert calls == [
            (
                f"{service.runtime.base_url}/free",
                {"unload_models": True, "free_memory": True},
            )
        ]


def test_release_memory_returns_false_when_comfyui_is_offline(monkeypatch) -> None:
    class Client:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        def __enter__(self) -> "Client":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, url: str) -> None:
            raise OSError("offline")

        def post(self, url: str, *, json: dict[str, bool]) -> None:
            raise OSError("offline")

    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        _create_portable_install(root)
        service = ComfyUiImageGenerationService(build_test_settings(root))
        monkeypatch.setattr(
            "atnos.services.comfyui_image_generation_service.httpx.Client",
            Client,
        )

        assert service.release_memory() is False


def test_release_memory_stops_windows_rocm_runtime_without_native_unload(monkeypatch) -> None:
    calls: list[str] = []

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "system": {"pytorch_version": "2.9.1+rocm7.2.1"},
                "devices": [{"name": "cuda:0 AMD Radeon RX 7600 XT : native"}],
            }

    class Client:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        def __enter__(self) -> "Client":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, url: str) -> Response:
            calls.append(f"GET {url}")
            return Response()

        def post(self, url: str, *, json: dict[str, bool]) -> None:
            raise AssertionError("Windows ROCm must not call ComfyUI /free")

    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        _create_portable_install(root)
        service = ComfyUiImageGenerationService(build_test_settings(root))
        stopped: list[bool] = []
        monkeypatch.setattr("atnos.services.comfyui_image_generation_service.os.name", "nt")
        monkeypatch.setattr("atnos.services.comfyui_image_generation_service.httpx.Client", Client)
        monkeypatch.setattr(
            service.runtime,
            "stop_server",
            lambda *, require_idle: stopped.append(require_idle) or {"stopped": True},
        )

        assert service.release_memory() is True
        assert calls == [f"GET {service.runtime.base_url}/system_stats"]
        assert stopped == [True]


def test_local_checkpoint_is_made_visible_to_comfyui() -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        _create_portable_install(root)
        settings = build_test_settings(root)
        external_checkpoint = settings.data_dir / "models" / "comfyui" / "checkpoints" / "model.safetensors"
        external_checkpoint.parent.mkdir(parents=True)
        external_checkpoint.write_bytes(b"checkpoint")
        service = ComfyUiImageGenerationService(settings)

        checkpoint_name = service.resolve_checkpoint_name()

        assert checkpoint_name == "model.safetensors"
        target = root / "ComfyUI_windows_portable" / "ComfyUI" / "models" / "checkpoints" / "model.safetensors"
        assert target.exists()


def test_api_checkpoint_names_reads_first_comfyui_checkpoint(monkeypatch) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "CheckpointLoaderSimple": {
                    "input": {
                        "required": {
                            "ckpt_name": [["first.safetensors", "second.safetensors"], {}],
                        }
                    }
                }
            }

    class Client:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        def __enter__(self) -> "Client":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, url: str) -> Response:
            return Response()

    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        _create_portable_install(root)
        service = ComfyUiImageGenerationService(build_test_settings(root))
        monkeypatch.setattr(
            "atnos.services.comfyui_image_generation_service.httpx.Client",
            Client,
        )

        assert service.resolve_checkpoint_name() == "first.safetensors"


def test_running_comfy_prefers_image_checkpoint_over_ltx_video_checkpoint(monkeypatch) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "CheckpointLoaderSimple": {
                    "input": {
                        "required": {
                            "ckpt_name": [
                                [
                                    "ltx-video-2b-v0.9.5.safetensors",
                                    "sd_xl_turbo_1.0_fp16.safetensors",
                                ],
                                {},
                            ],
                        }
                    }
                }
            }

    class Client:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        def __enter__(self) -> "Client":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, url: str) -> Response:
            return Response()

    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        _create_portable_install(root)
        service = ComfyUiImageGenerationService(build_test_settings(root))
        monkeypatch.setattr(
            "atnos.services.comfyui_image_generation_service.httpx.Client",
            Client,
        )

        assert service.resolve_checkpoint_name() == "sd_xl_turbo_1.0_fp16.safetensors"


def test_quality_mode_prefers_sdxl_base_over_turbo(monkeypatch) -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        _create_portable_install(root)
        service = ComfyUiImageGenerationService(build_test_settings(root))
        monkeypatch.setattr(
            service,
            "_api_checkpoint_names",
            lambda: [
                "ltx-video-2b-v0.9.5.safetensors",
                "sd_xl_turbo_1.0_fp16.safetensors",
                "sd_xl_base_1.0_0.9vae.safetensors",
            ],
        )
        monkeypatch.setattr(service, "list_checkpoint_candidates", lambda: [])

        assert service.resolve_quality_checkpoint_name() == "sd_xl_base_1.0_0.9vae.safetensors"


def test_quality_mode_honors_explicit_juggernaut_checkpoint(monkeypatch) -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        _create_portable_install(root)
        settings = build_test_settings(root).model_copy(
            update={"comfyui_default_checkpoint": "Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors"}
        )
        service = ComfyUiImageGenerationService(settings)
        monkeypatch.setattr(
            service,
            "_api_checkpoint_names",
            lambda: [
                "sd_xl_base_1.0_0.9vae.safetensors",
                "Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors",
            ],
        )
        monkeypatch.setattr(service, "list_checkpoint_candidates", lambda: [])

        assert service.resolve_quality_checkpoint_name() == "Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors"


def test_juggernaut_quality_sampling_uses_sdxl_photo_defaults() -> None:
    assert ComfyUiImageGenerationService._quality_sampling_defaults(
        "Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors"
    ) == (30, 5.5, "dpmpp_2m_sde", "karras")


def test_flux2_klein_workflow_matches_official_comfy_template() -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        _create_portable_install(root)
        service = ComfyUiImageGenerationService(build_test_settings(root))

        workflow = service._build_flux2_klein_workflow(
            prompt="editorial finance photograph",
            width=512,
            height=896,
            seed=19,
            unet_name=service.FLUX2_KLEIN_UNET,
            clip_name=service.FLUX2_KLEIN_CLIP,
            vae_name=service.FLUX2_KLEIN_VAE,
            negative_prompt="text, watermark",
            steps_override=None,
            cfg_scale_override=None,
        )

        assert workflow["1"]["class_type"] == "UNETLoader"
        assert workflow["1"]["inputs"]["unet_name"] == service.FLUX2_KLEIN_UNET
        assert workflow["2"]["inputs"]["type"] == "flux2"
        assert workflow["6"]["class_type"] == "EmptyFlux2LatentImage"
        assert workflow["10"]["inputs"] == {"steps": 20, "width": 512, "height": 896}
        assert workflow["13"]["inputs"]["images"] == ["12", 0]


def test_z_image_turbo_workflow_matches_official_comfy_template() -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        _create_portable_install(root)
        service = ComfyUiImageGenerationService(build_test_settings(root))

        workflow = service._build_z_image_turbo_workflow(
            prompt="documentary photograph with literal evidence",
            width=512,
            height=896,
            seed=29,
            unet_name=service.Z_IMAGE_TURBO_UNET,
            clip_name=service.Z_IMAGE_TURBO_CLIP,
            vae_name=service.Z_IMAGE_TURBO_VAE,
            steps_override=None,
        )

        assert workflow["1"]["inputs"]["unet_name"] == service.Z_IMAGE_TURBO_UNET
        assert workflow["2"] == {
            "inputs": {"model": ["1", 0], "shift": 3.0},
            "class_type": "ModelSamplingAuraFlow",
        }
        assert workflow["3"]["inputs"]["type"] == "lumina2"
        assert workflow["5"]["class_type"] == "ConditioningZeroOut"
        assert workflow["6"]["class_type"] == "EmptySD3LatentImage"
        assert workflow["8"]["inputs"]["steps"] == 8
        assert workflow["8"]["inputs"]["cfg"] == 1.0
        assert workflow["8"]["inputs"]["sampler_name"] == "res_multistep"
        assert workflow["8"]["inputs"]["scheduler"] == "simple"
        assert workflow["10"]["inputs"]["images"] == ["9", 0]


def test_quality_generation_prefers_z_image_turbo_over_flux_when_models_are_live(monkeypatch) -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        _create_portable_install(root)
        service = ComfyUiImageGenerationService(build_test_settings(root))
        monkeypatch.setattr(HardwareGuardService, "require_gpu", classmethod(lambda cls, feature_name, **kwargs: None))
        monkeypatch.setattr(service.runtime, "ensure_server_running", lambda *, wait_timeout_s, lowvram: True)
        monkeypatch.setattr(
            service,
            "resolve_z_image_turbo_models",
            lambda: (service.Z_IMAGE_TURBO_UNET, service.Z_IMAGE_TURBO_CLIP, service.Z_IMAGE_TURBO_VAE),
        )
        monkeypatch.setattr(
            service,
            "resolve_flux2_klein_models",
            lambda: (_ for _ in ()).throw(AssertionError("Flux must not be selected when Z-Image is live")),
        )
        queued: list[dict[str, object]] = []
        monkeypatch.setattr(service, "_queue_prompt", lambda workflow: queued.append(workflow) or "z-image-prompt")
        wait_timeouts: list[int | None] = []
        monkeypatch.setattr(
            service,
            "_wait_for_image",
            lambda prompt_id, *, timeout_s=None: wait_timeouts.append(timeout_s)
            or {"filename": "z-image.png", "type": "output"},
        )
        monkeypatch.setattr(service, "_download_image", lambda image_meta: PNG_BYTES)

        service.generate_background(
            prompt="literal editorial scene",
            output_path=settings_output_path(root),
            width=512,
            height=896,
            seed=31,
            quality_mode=True,
        )

        assert queued[0]["2"]["class_type"] == "ModelSamplingAuraFlow"  # type: ignore[index]
        assert wait_timeouts == [max(600, service.settings.comfyui_generation_timeout_s)]
        evidence = settings_output_path(root).with_suffix(".png.comfyui.json")
        assert evidence.exists()
        assert '"backend": "z_image_turbo"' in evidence.read_text(encoding="utf-8")


def test_quality_generation_prefers_flux2_klein_when_models_are_live(monkeypatch) -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        _create_portable_install(root)
        service = ComfyUiImageGenerationService(build_test_settings(root))
        monkeypatch.setattr(HardwareGuardService, "require_gpu", classmethod(lambda cls, feature_name, **kwargs: None))
        monkeypatch.setattr(service.runtime, "ensure_server_running", lambda *, wait_timeout_s, lowvram: True)
        monkeypatch.setattr(service, "resolve_z_image_turbo_models", lambda: None)
        monkeypatch.setattr(
            service,
            "resolve_flux2_klein_models",
            lambda: (service.FLUX2_KLEIN_UNET, service.FLUX2_KLEIN_CLIP, service.FLUX2_KLEIN_VAE),
        )
        monkeypatch.setattr(
            service,
            "resolve_quality_checkpoint_name",
            lambda: (_ for _ in ()).throw(AssertionError("SDXL fallback must not be selected")),
        )
        queued: list[dict[str, object]] = []
        monkeypatch.setattr(service, "_queue_prompt", lambda workflow: queued.append(workflow) or "flux-prompt")
        wait_timeouts: list[int | None] = []
        monkeypatch.setattr(
            service,
            "_wait_for_image",
            lambda prompt_id, *, timeout_s=None: wait_timeouts.append(timeout_s)
            or {"filename": "flux.png", "type": "output"},
        )
        monkeypatch.setattr(service, "_download_image", lambda image_meta: PNG_BYTES)

        service.generate_background(
            prompt="literal finance scene",
            output_path=settings_output_path(root),
            width=512,
            height=896,
            seed=23,
            quality_mode=True,
        )

        assert queued[0]["1"]["class_type"] == "UNETLoader"  # type: ignore[index]
        assert wait_timeouts == [max(600, service.settings.comfyui_generation_timeout_s)]


def test_quality_mode_uses_checkpoint_specific_sampling_defaults() -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        _create_portable_install(root)
        service = ComfyUiImageGenerationService(build_test_settings(root))

        workflow = service._build_workflow(
            prompt="real editorial photograph",
            width=512,
            height=896,
            seed=7,
            checkpoint_name="sd_xl_base_1.0_0.9vae.safetensors",
            negative_prompt=None,
            steps_override=None,
            cfg_scale_override=None,
            quality_mode=True,
        )

        sampler = workflow["5"]["inputs"]
        assert sampler["steps"] == 30
        assert sampler["cfg"] == 6.5
        assert sampler["sampler_name"] == "dpmpp_2m_sde"
        assert sampler["scheduler"] == "karras"


def test_image_validation_rejects_truncated_and_wrong_size_artifacts() -> None:
    with make_temp_dir() as temp_dir:
        service = ComfyUiImageGenerationService(build_test_settings(Path(temp_dir)))

        try:
            service._validate_image_bytes(b"not-an-image", expected_width=512, expected_height=896)
        except ComfyUiImageGenerationError as exc:
            assert "implausibly small" in str(exc)
        else:
            raise AssertionError("Truncated image must be rejected")

        try:
            service._validate_image_bytes(
                _png_bytes(600, 600),
                expected_width=512,
                expected_height=896,
            )
        except ComfyUiImageGenerationError as exc:
            assert "dimensions" in str(exc)
        else:
            raise AssertionError("Wrong image dimensions must be rejected")


def test_pagefile_failure_falls_back_from_z_image_to_flux_without_restart(monkeypatch) -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        _create_portable_install(root)
        service = ComfyUiImageGenerationService(build_test_settings(root))
        monkeypatch.setattr(HardwareGuardService, "require_gpu", classmethod(lambda cls, feature_name, **kwargs: None))
        monkeypatch.setattr(service.runtime, "ensure_server_running", lambda *, wait_timeout_s, lowvram: True)
        monkeypatch.setattr(
            service,
            "resolve_z_image_turbo_models",
            lambda: (service.Z_IMAGE_TURBO_UNET, service.Z_IMAGE_TURBO_CLIP, service.Z_IMAGE_TURBO_VAE),
        )
        monkeypatch.setattr(
            service,
            "resolve_flux2_klein_models",
            lambda: (service.FLUX2_KLEIN_UNET, service.FLUX2_KLEIN_CLIP, service.FLUX2_KLEIN_VAE),
        )
        queued: list[dict[str, object]] = []
        monkeypatch.setattr(service, "_queue_prompt", lambda workflow: queued.append(workflow) or f"prompt-{len(queued)}")
        waits = iter(
            [
                ComfyUiImageGenerationError("OSError in UNETLoader: Plik stronicowania jest za mały (os error 1455)"),
                {"filename": "flux.png", "type": "output"},
            ]
        )

        def wait_for_image(prompt_id: str, *, timeout_s: int | None = None):
            result = next(waits)
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr(service, "_wait_for_image", wait_for_image)
        monkeypatch.setattr(service, "_download_image", lambda image_meta: PNG_BYTES)
        restarts: list[bool] = []
        monkeypatch.setattr(
            service.runtime,
            "restart_server",
            lambda *, wait_timeout_s, lowvram: restarts.append(lowvram) or True,
        )

        result = service.generate_background(
            prompt="literal editorial scene",
            output_path=settings_output_path(root),
            width=512,
            height=896,
            seed=41,
            quality_mode=True,
        )

        assert queued[0]["2"]["class_type"] == "ModelSamplingAuraFlow"  # type: ignore[index]
        assert queued[1]["1"]["class_type"] == "UNETLoader"  # type: ignore[index]
        assert queued[1]["2"]["inputs"]["type"] == "flux2"  # type: ignore[index]
        assert result.backend == "flux2_klein"
        assert restarts == []
        health = service.backend_health_snapshot()
        assert health["z_image_turbo"]["status"] == "blocked"
        assert health["z_image_turbo"]["failure_kind"] == "windows_commit_memory"
        assert health["flux2_klein"]["status"] == "verified"


def test_persisted_backend_failure_skips_z_image_in_new_service(monkeypatch) -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        _create_portable_install(root)
        settings = build_test_settings(root)
        first_service = ComfyUiImageGenerationService(settings)
        first_service._record_backend_failure(
            "z_image_turbo",
            reason="paging file is too small (os error 1455)",
            failure_kind="windows_commit_memory",
        )

        service = ComfyUiImageGenerationService(settings)
        monkeypatch.setattr(HardwareGuardService, "require_gpu", classmethod(lambda cls, feature_name, **kwargs: None))
        monkeypatch.setattr(service.runtime, "ensure_server_running", lambda *, wait_timeout_s, lowvram: True)
        monkeypatch.setattr(
            service,
            "resolve_z_image_turbo_models",
            lambda: (_ for _ in ()).throw(AssertionError("Persistently blocked Z-Image must not be probed")),
        )
        monkeypatch.setattr(
            service,
            "resolve_flux2_klein_models",
            lambda: (service.FLUX2_KLEIN_UNET, service.FLUX2_KLEIN_CLIP, service.FLUX2_KLEIN_VAE),
        )
        workflows: list[dict[str, object]] = []
        monkeypatch.setattr(service, "_queue_prompt", lambda workflow: workflows.append(workflow) or "flux-prompt")
        monkeypatch.setattr(
            service,
            "_wait_for_image",
            lambda prompt_id, *, timeout_s=None: {"filename": "flux.png", "type": "output"},
        )
        monkeypatch.setattr(service, "_download_image", lambda image_meta: PNG_BYTES)

        result = service.generate_background(
            prompt="literal editorial scene",
            output_path=settings_output_path(root),
            width=512,
            height=896,
            seed=43,
            quality_mode=True,
        )

        assert result.backend == "flux2_klein"
        assert workflows[0]["2"]["inputs"]["type"] == "flux2"  # type: ignore[index]


def test_persisted_native_crash_skips_z_image_and_uses_checkpoint(monkeypatch) -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        _create_portable_install(root)
        settings = build_test_settings(root)
        first_service = ComfyUiImageGenerationService(settings)
        first_service._record_backend_failure(
            "z_image_turbo",
            reason="Windows fatal exception: access violation",
            failure_kind="native_runtime_exit",
        )

        service = ComfyUiImageGenerationService(settings)
        monkeypatch.setattr(HardwareGuardService, "require_gpu", classmethod(lambda cls, feature_name, **kwargs: None))
        monkeypatch.setattr(service.runtime, "ensure_server_running", lambda *, wait_timeout_s, lowvram: True)
        monkeypatch.setattr(
            service,
            "resolve_z_image_turbo_models",
            lambda: (_ for _ in ()).throw(AssertionError("Crashed Z-Image backend must remain in cooldown")),
        )
        monkeypatch.setattr(service, "resolve_flux2_klein_models", lambda: None)
        monkeypatch.setattr(service, "resolve_quality_checkpoint_name", lambda: "model.safetensors")
        workflows: list[dict[str, object]] = []
        monkeypatch.setattr(service, "_queue_prompt", lambda workflow: workflows.append(workflow) or "checkpoint-prompt")
        monkeypatch.setattr(
            service,
            "_wait_for_image",
            lambda prompt_id, *, timeout_s=None: {"filename": "checkpoint.png", "type": "output"},
        )
        monkeypatch.setattr(service, "_download_image", lambda image_meta: PNG_BYTES)

        result = service.generate_background(
            prompt="literal editorial scene",
            output_path=settings_output_path(root),
            width=512,
            height=896,
            seed=47,
            quality_mode=True,
        )

        assert result.backend == "checkpoint"
        assert workflows[0]["1"]["class_type"] == "CheckpointLoaderSimple"  # type: ignore[index]


def test_windows_rocm_amd_runtime_blocks_z_image(monkeypatch) -> None:
    monkeypatch.setattr("atnos.services.comfyui_image_generation_service.os.name", "nt")

    assert ComfyUiImageGenerationService._z_image_is_unsupported_on_runtime(
        {
            "system": {"pytorch_version": "2.9.1+rocm7.2.1"},
            "devices": [{"name": "cuda:0 AMD Radeon RX 7600 XT : native"}],
        }
    )


def test_windows_cuda_runtime_does_not_block_z_image(monkeypatch) -> None:
    monkeypatch.setattr("atnos.services.comfyui_image_generation_service.os.name", "nt")

    assert not ComfyUiImageGenerationService._z_image_is_unsupported_on_runtime(
        {
            "system": {"pytorch_version": "2.9.1+cu128"},
            "devices": [{"name": "cuda:0 NVIDIA GeForce RTX 4080"}],
        }
    )


def test_blocked_z_image_prefers_quality_checkpoint_before_flux(monkeypatch) -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        _create_portable_install(root)
        service = ComfyUiImageGenerationService(build_test_settings(root))
        monkeypatch.setattr(
            HardwareGuardService,
            "require_gpu",
            classmethod(lambda cls, feature_name, **kwargs: None),
        )
        monkeypatch.setattr(
            service,
            "_persistently_blocked_backends",
            lambda: set(),
        )
        monkeypatch.setattr(
            service,
            "_runtime_incompatible_backends",
            lambda: {"z_image_turbo"},
        )
        monkeypatch.setattr(
            service.runtime,
            "ensure_server_running",
            lambda *, wait_timeout_s, lowvram: True,
        )
        monkeypatch.setattr(
            service,
            "resolve_flux2_klein_models",
            lambda: (_ for _ in ()).throw(
                AssertionError("Flux must not preempt the safe quality checkpoint")
            ),
        )
        monkeypatch.setattr(
            service,
            "resolve_quality_checkpoint_name",
            lambda: "Juggernaut-XL.safetensors",
        )
        workflows: list[dict[str, object]] = []
        monkeypatch.setattr(
            service,
            "_queue_prompt",
            lambda workflow: workflows.append(workflow) or "checkpoint-prompt",
        )
        monkeypatch.setattr(
            service,
            "_wait_for_image",
            lambda prompt_id, *, timeout_s=None: {
                "filename": "checkpoint.png",
                "type": "output",
            },
        )
        monkeypatch.setattr(service, "_download_image", lambda image_meta: PNG_BYTES)

        result = service.generate_background(
            prompt="mature finance editorial",
            output_path=settings_output_path(root),
            width=512,
            height=896,
            seed=51,
            quality_mode=True,
        )

        assert result.backend == "checkpoint"
        assert workflows[0]["1"]["inputs"]["ckpt_name"] == "Juggernaut-XL.safetensors"  # type: ignore[index]


def test_quality_generation_rechecks_runtime_compatibility_after_server_start(monkeypatch) -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        _create_portable_install(root)
        service = ComfyUiImageGenerationService(build_test_settings(root))
        monkeypatch.setattr(HardwareGuardService, "require_gpu", classmethod(lambda cls, feature_name, **kwargs: None))
        monkeypatch.setattr(service, "_persistently_blocked_backends", lambda: set())
        compatibility_results = iter([set(), {"z_image_turbo", "flux2_klein"}])
        monkeypatch.setattr(service, "_runtime_incompatible_backends", lambda: next(compatibility_results))
        monkeypatch.setattr(service.runtime, "ensure_server_running", lambda *, wait_timeout_s, lowvram: True)
        monkeypatch.setattr(
            service,
            "resolve_z_image_turbo_models",
            lambda: (_ for _ in ()).throw(AssertionError("Z-Image must be blocked after the live runtime probe")),
        )
        monkeypatch.setattr(
            service,
            "resolve_flux2_klein_models",
            lambda: (_ for _ in ()).throw(AssertionError("Flux2 must be blocked after the live runtime probe")),
        )
        monkeypatch.setattr(service, "resolve_quality_checkpoint_name", lambda: "Juggernaut-XL.safetensors")
        workflows: list[dict[str, object]] = []
        monkeypatch.setattr(service, "_queue_prompt", lambda workflow: workflows.append(workflow) or "checkpoint-prompt")
        monkeypatch.setattr(
            service,
            "_wait_for_image",
            lambda prompt_id, *, timeout_s=None: {"filename": "checkpoint.png", "type": "output"},
        )
        monkeypatch.setattr(service, "_download_image", lambda image_meta: PNG_BYTES)

        result = service.generate_background(
            prompt="mature finance editorial",
            output_path=settings_output_path(root),
            width=512,
            height=896,
            seed=61,
            quality_mode=True,
        )

        assert result.backend == "checkpoint"
        assert workflows[0]["1"]["inputs"]["ckpt_name"] == "Juggernaut-XL.safetensors"  # type: ignore[index]


def test_history_error_detail_extracts_execution_error() -> None:
    messages = [
        ["execution_start", {"prompt_id": "p1"}],
        [
            "execution_error",
            {
                "node_type": "UNETLoader",
                "exception_type": "OSError",
                "exception_message": "paging file is too small (os error 1455)",
            },
        ],
    ]

    detail = ComfyUiImageGenerationService._history_error_detail(messages)

    assert detail == "OSError in UNETLoader: paging file is too small (os error 1455)"
    assert ComfyUiImageGenerationService._looks_like_pagefile_error(detail) is True


@pytest.mark.parametrize(
    "queue_error",
    [
        ComfyUiDispatchOutcomeUnknownError("connection reset after POST"),
        ComfyUiWorkflowRejectedError("invalid workflow"),
    ],
)
def test_generation_does_not_retry_or_restart_after_uncertain_or_rejected_dispatch(monkeypatch, queue_error) -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        _create_portable_install(root)
        service = ComfyUiImageGenerationService(build_test_settings(root))
        monkeypatch.setattr(HardwareGuardService, "require_gpu", classmethod(lambda cls, feature_name, **kwargs: None))
        monkeypatch.setattr(service, "resolve_checkpoint_name", lambda: "model.safetensors")
        monkeypatch.setattr(service.runtime, "ensure_server_running", lambda *, wait_timeout_s, lowvram: True)
        restarts: list[bool] = []
        monkeypatch.setattr(
            service.runtime,
            "restart_server",
            lambda *, wait_timeout_s, lowvram: restarts.append(lowvram) or True,
        )
        queue_attempts = {"count": 0}

        def queue_prompt(workflow: dict[str, object]) -> str:
            queue_attempts["count"] += 1
            raise queue_error

        monkeypatch.setattr(service, "_queue_prompt", queue_prompt)
        output_path = settings_output_path(root)

        with pytest.raises(type(queue_error)):
            service.generate_background(
                prompt="atnos thumbnail",
                output_path=output_path,
                width=768,
                height=768,
                seed=7,
                steps_override=20,
            )

        assert queue_attempts["count"] == 1
        assert restarts == []
        assert not output_path.exists()


@pytest.mark.parametrize(
    ("response_status", "payload", "expected_error"),
    [
        (400, {"error": "bad graph"}, ComfyUiWorkflowRejectedError),
        (200, {"node_errors": {"7": {"errors": [{"message": "missing model"}]}}}, ComfyUiWorkflowRejectedError),
        (503, {"error": "server error"}, ComfyUiDispatchOutcomeUnknownError),
        (200, {"status": "accepted-without-id"}, ComfyUiDispatchOutcomeUnknownError),
    ],
)
def test_queue_prompt_classifies_rejection_vs_unknown_dispatch(
    monkeypatch, response_status, payload, expected_error
) -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        _create_portable_install(root)
        service = ComfyUiImageGenerationService(build_test_settings(root))
        original_client = httpx.Client
        transport = httpx.MockTransport(
            lambda request: httpx.Response(response_status, json=payload)
        )
        monkeypatch.setattr(
            comfy_generation.httpx,
            "Client",
            lambda **kwargs: original_client(transport=transport, **kwargs),
        )

        with pytest.raises(expected_error):
            service._queue_prompt({"1": {"class_type": "SaveImage", "inputs": {}}})


def test_queue_prompt_transport_failure_is_an_unknown_dispatch(monkeypatch) -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        _create_portable_install(root)
        service = ComfyUiImageGenerationService(build_test_settings(root))
        original_client = httpx.Client

        def fail_after_request(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("response lost after request")

        transport = httpx.MockTransport(fail_after_request)
        monkeypatch.setattr(
            comfy_generation.httpx,
            "Client",
            lambda **kwargs: original_client(transport=transport, **kwargs),
        )

        with pytest.raises(ComfyUiDispatchOutcomeUnknownError, match="server may have accepted"):
            service._queue_prompt({"1": {"class_type": "SaveImage", "inputs": {}}})


def test_generation_retries_vram_failures_with_lowvram_and_512px(monkeypatch) -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        _create_portable_install(root)
        service = ComfyUiImageGenerationService(build_test_settings(root))
        monkeypatch.setattr(HardwareGuardService, "require_gpu", classmethod(lambda cls, feature_name, **kwargs: None))
        monkeypatch.setattr(service, "resolve_checkpoint_name", lambda: "model.safetensors")
        monkeypatch.setattr(service.runtime, "ensure_server_running", lambda *, wait_timeout_s, lowvram: True)
        restarts: list[bool] = []
        workflows: list[dict[str, object]] = []
        monkeypatch.setattr(
            service.runtime,
            "restart_server",
            lambda *, wait_timeout_s, lowvram: restarts.append(lowvram) or True,
        )

        def queue_prompt(workflow: dict[str, object]) -> str:
            workflows.append(workflow)
            if len(workflows) == 1:
                raise ComfyUiImageGenerationError("CUDA out of memory")
            return "prompt-2"

        monkeypatch.setattr(service, "_queue_prompt", queue_prompt)
        monkeypatch.setattr(
            service,
            "_wait_for_image",
            lambda prompt_id, *, timeout_s=None: {"filename": "image.png", "type": "output"},
        )
        monkeypatch.setattr(service, "_download_image", lambda image_meta: _png_bytes(512, 512))
        output_path = settings_output_path(root)

        service.generate_background(
            prompt="atnos poster",
            output_path=output_path,
            width=768,
            height=768,
            seed=11,
            steps_override=20,
        )

        retry_latent = workflows[1]["4"]["inputs"]  # type: ignore[index]
        assert retry_latent["width"] == 512
        assert retry_latent["height"] == 512
        assert restarts == [True]
        assert output_path.exists()


def test_configured_lowvram_keeps_requested_initial_dimensions(monkeypatch) -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        _create_portable_install(root)
        settings = build_test_settings(root)
        settings.comfyui_lowvram = True
        service = ComfyUiImageGenerationService(settings)
        monkeypatch.setattr(HardwareGuardService, "require_gpu", classmethod(lambda cls, feature_name, **kwargs: None))
        monkeypatch.setattr(service, "resolve_checkpoint_name", lambda: "model.safetensors")
        starts, workflows = [], []
        monkeypatch.setattr(service.runtime, "ensure_server_running", lambda **kwargs: starts.append(kwargs) or True)
        monkeypatch.setattr(service, "_queue_prompt", lambda workflow: workflows.append(workflow) or "p1")
        monkeypatch.setattr(service, "_wait_for_image", lambda *a, **k: {"filename":"image.png", "type":"output"})
        monkeypatch.setattr(service, "_download_image", lambda meta: _png_bytes(768, 1024))
        service.generate_background(prompt="test", output_path=settings_output_path(root), width=768, height=1024, seed=1)
        assert starts[0]["lowvram"] is True
        assert workflows[0]["4"]["inputs"]["width"] == 768
        assert workflows[0]["4"]["inputs"]["height"] == 1024


def test_generation_does_not_retry_or_restart_after_accepted_prompt_becomes_unknown(monkeypatch) -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        _create_portable_install(root)
        service = ComfyUiImageGenerationService(build_test_settings(root))
        monkeypatch.setattr(HardwareGuardService, "require_gpu", classmethod(lambda cls, feature_name, **kwargs: None))
        monkeypatch.setattr(service, "resolve_checkpoint_name", lambda: "model.safetensors")
        monkeypatch.setattr(service.runtime, "ensure_server_running", lambda *, wait_timeout_s, lowvram: True)
        restarts: list[bool] = []
        monkeypatch.setattr(
            service.runtime,
            "restart_server",
            lambda *, wait_timeout_s, lowvram: restarts.append(lowvram) or True,
        )
        queue_calls: list[dict[str, object]] = []
        monkeypatch.setattr(service, "_queue_prompt", lambda workflow: queue_calls.append(workflow) or "crash-prompt")
        monkeypatch.setattr(
            service,
            "_wait_for_image",
            lambda prompt_id, *, timeout_s=None: (_ for _ in ()).throw(
                ComfyUiDispatchOutcomeUnknownError(
                    f"ComfyUI generation status for accepted prompt {prompt_id} is unknown"
                )
            ),
        )

        with pytest.raises(ComfyUiDispatchOutcomeUnknownError, match="accepted prompt crash-prompt"):
            service.generate_background(
                prompt="safe crash probe",
                output_path=settings_output_path(root),
                width=512,
                height=512,
                seed=37,
            )

        assert len(queue_calls) == 1
        assert restarts == []


@pytest.mark.parametrize(("configured_timeout", "expected_timeout"), [(900, 240), (90, 90)])
def test_cold_start_unavailable_uses_one_bounded_start_without_restart(
    monkeypatch, configured_timeout: int, expected_timeout: int,
) -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        _create_portable_install(root)
        settings = build_test_settings(root)
        settings.comfyui_generation_timeout_s = configured_timeout
        service = ComfyUiImageGenerationService(settings)
        monkeypatch.setattr(HardwareGuardService, "require_gpu", classmethod(lambda cls, feature_name, **kwargs: None))
        monkeypatch.setattr(
            "atnos.services.production_gpu_residency_service.ProductionGpuResidencyService.prepare_for_comfy",
            lambda self: None,
        )
        starts: list[dict[str, object]] = []
        monkeypatch.setattr(service.runtime, "ensure_server_running", lambda **kwargs: starts.append(kwargs) or False)
        monkeypatch.setattr(
            service.runtime,
            "restart_server",
            lambda **kwargs: (_ for _ in ()).throw(AssertionError("cold-start timeout must not restart ComfyUI")),
        )
        queue_calls: list[object] = []
        monkeypatch.setattr(service, "_queue_prompt", lambda workflow: queue_calls.append(workflow))

        with pytest.raises(ComfyUiRuntimeUnavailableError, match="bounded startup deadline"):
            service.generate_background(
                prompt="startup probe", output_path=settings_output_path(root),
                width=512, height=512, seed=3,
            )

        assert starts == [{"wait_timeout_s": expected_timeout, "lowvram": bool(settings.comfyui_lowvram)}]
        assert queue_calls == []
        assert not settings_output_path(root).exists()


def settings_output_path(root: Path) -> Path:
    return root / "data" / "atnos-outputs" / "session-1" / "image.png"
