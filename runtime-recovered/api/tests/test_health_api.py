from __future__ import annotations

from pathlib import Path
import time
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from atnos.api.main import _check_optional_runtime_capabilities
from .test_support import create_test_app, make_temp_dir


class TestHealthApi:
    def test_live_and_ready_endpoints_report_runtime_state(self) -> None:
        with make_temp_dir() as temp_dir:
            app = create_test_app(Path(temp_dir))
            with TestClient(app) as client:
                live_response = client.get("/health/live")
                assert live_response.status_code == 200
                assert live_response.json()["status"] == "ok"

                ready_response = client.get("/health/ready")
                assert ready_response.status_code == 200
                ready_payload = ready_response.json()
                assert ready_payload["ready"] is True
                assert ready_payload["startup_phase"] == "ready"
                assert ready_payload["status"] == "ok"
                assert ready_payload["startup_error"] is None
                assert ready_payload["startup_last_checkpoint"] == "ready:ok"
                assert ready_payload["startup_elapsed_ms"] >= 0

                deep_health_response = client.get("/health")
                assert deep_health_response.status_code == 200
                assert deep_health_response.json()["database"] == "ok"

    def test_deep_health_returns_cached_capabilities_and_refreshes_in_background(self) -> None:
        with make_temp_dir() as temp_dir:
            app = create_test_app(Path(temp_dir))
            with TestClient(app) as client:
                async def refresh() -> None:
                    import asyncio

                    await asyncio.sleep(0.05)
                    app.state.runtime_capability_flags["image_generation"] = {
                        "enabled": True,
                        "status": "ready",
                        "checkpoint": "verified.safetensors",
                    }

                refresh_mock = AsyncMock(side_effect=refresh)
                app.state.refresh_optional_runtime_capabilities = refresh_mock

                response = client.get("/health")

                assert response.status_code == 200
                image = response.json()["runtime_capabilities"]["image_generation"]
                assert image.get("checkpoint") is None
                assert response.json()["runtime_capabilities_source"] == "background_probe_cache"
                assert response.json()["runtime_capabilities_refreshing"] is True

                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline:
                    if app.state.runtime_capability_flags["image_generation"].get("checkpoint"):
                        break
                    time.sleep(0.01)

                refresh_mock.assert_awaited()
                assert app.state.runtime_capability_flags["image_generation"]["status"] == "ready"
                assert (
                    app.state.runtime_capability_flags["image_generation"]["checkpoint"]
                    == "verified.safetensors"
                )

    def test_deep_health_does_not_block_on_slow_capability_probe(self) -> None:
        with make_temp_dir() as temp_dir:
            app = create_test_app(Path(temp_dir))
            with TestClient(app) as client:
                async def slow_refresh() -> None:
                    import asyncio

                    await asyncio.sleep(1.0)

                app.state.refresh_optional_runtime_capabilities = slow_refresh

                started = time.monotonic()
                response = client.get("/health")
                elapsed = time.monotonic() - started

                assert response.status_code == 200
                assert elapsed < 0.5
                assert response.json()["runtime_capabilities_refreshing"] is True

    def test_ready_endpoint_returns_503_when_runtime_not_ready(self) -> None:
        with make_temp_dir() as temp_dir:
            app = create_test_app(Path(temp_dir))
            with TestClient(app) as client:
                app.state.startup_ready = False
                app.state.startup_phase = "bootstrapping"
                app.state.startup_last_checkpoint = "database_migrate:start"
                app.state.startup_elapsed_ms = 321
                app.state.startup_error = "SQLite database lock detected."

                response = client.get("/health/ready")

                assert response.status_code == 503
                payload = response.json()
                assert payload["ready"] is False
                assert payload["startup_phase"] == "bootstrapping"
                assert payload["startup_last_checkpoint"] == "database_migrate:start"
                assert payload["startup_elapsed_ms"] == 321
                assert payload["startup_error"] == "SQLite database lock detected."
                assert payload["status"] == "failed"

    def test_optional_runtime_capabilities_use_production_video_readiness(self) -> None:
        with make_temp_dir() as temp_dir:
            app = create_test_app(Path(temp_dir))
            settings = app.state.settings
            settings.shorts_video_backend = "ltx"
            comfy_root = settings.comfyui_install_dir / "ComfyUI"
            for relative in (
                "models/checkpoints/ltx-video-2b-v0.9.5.safetensors",
                "models/vae/ltx-video-2b-v0.9.5_vae.safetensors",
                "models/clip/t5xxl_fp8_e4m3fn.safetensors",
            ):
                path = comfy_root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"x" * (1024 * 1024 + 1))

            with patch(
                "atnos.services.comfyui_runtime_service.ComfyUiRuntimeService.probe_live_api",
                return_value={
                    "ready": True,
                    "api_online": True,
                    "base_url": "http://127.0.0.1:8188",
                    "missing_class_types": [],
                    "missing_models": {},
                    "reason": None,
                },
            ):
                flags = _check_optional_runtime_capabilities(settings)

            video = flags["video_generation"]
            assert video["status"] == "ready"
            assert video["enabled"] is True
            assert video["backend"] == "production-ltx"
            assert video["production_short_video"]["enabled"] is True
            assert video["production_short_video"]["technical_ready"] is True
            assert video["production_short_video"]["public_quality_ready"] is False
            assert video["production_short_video"]["quality_evidence"]["status"] == "missing"
            assert video["production_short_video"]["backends"]["ltx"]["workflow_valid"] is True
            assert video["production_short_video"]["backends"]["ltx"]["public_quality_ready"] is False

    def test_optional_capability_probe_never_starts_comfyui(self) -> None:
        with make_temp_dir() as temp_dir:
            app = create_test_app(Path(temp_dir))
            settings = app.state.settings

            with (
                patch(
                    "atnos.services.comfyui_runtime_service.ComfyUiRuntimeService.ensure_startup_ready"
                ) as start_mock,
                patch(
                    "atnos.services.comfyui_runtime_service.ComfyUiRuntimeService.probe_live_api",
                    return_value={
                        "ready": False,
                        "api_online": False,
                        "base_url": None,
                        "reason": "ComfyUI API is offline.",
                    },
                ),
            ):
                flags = _check_optional_runtime_capabilities(settings)

            start_mock.assert_not_called()
            image = flags["image_generation"]
            assert image["enabled"] is False
            assert image["api_online"] is False
            assert image["started"] is False
            assert "on-demand" in image["detail"]

    def test_model_files_do_not_claim_video_ready_when_comfyui_is_offline(self) -> None:
        with make_temp_dir() as temp_dir:
            app = create_test_app(Path(temp_dir))
            settings = app.state.settings
            settings.shorts_video_backend = "ltx"
            comfy_root = settings.comfyui_install_dir / "ComfyUI"
            for relative in (
                "models/checkpoints/ltx-video-2b-v0.9.5.safetensors",
                "models/vae/ltx-video-2b-v0.9.5_vae.safetensors",
                "models/clip/t5xxl_fp8_e4m3fn.safetensors",
            ):
                path = comfy_root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"x" * (1024 * 1024 + 1))

            with patch(
                "atnos.services.comfyui_runtime_service.ComfyUiRuntimeService.probe_live_api",
                return_value={
                    "ready": False,
                    "api_online": False,
                    "base_url": None,
                    "missing_class_types": ["LTXVideoSampler"],
                    "missing_models": {},
                    "reason": "ComfyUI API is offline.",
                },
            ):
                flags = _check_optional_runtime_capabilities(settings)

            assert flags["video_generation"]["status"] != "ready"
            assert flags["video_generation"]["production_short_video"]["enabled"] is False

    def test_hidden_model_files_do_not_claim_video_ready(self) -> None:
        with make_temp_dir() as temp_dir:
            app = create_test_app(Path(temp_dir))
            settings = app.state.settings
            settings.shorts_video_backend = "ltx"
            comfy_root = settings.comfyui_install_dir / "ComfyUI"
            for relative in (
                "models/checkpoints/ltx-video-2b-v0.9.5.safetensors",
                "models/vae/ltx-video-2b-v0.9.5_vae.safetensors",
                "models/clip/t5xxl_fp8_e4m3fn.safetensors",
            ):
                path = comfy_root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"x" * (1024 * 1024 + 1))

            with patch(
                "atnos.services.comfyui_runtime_service.ComfyUiRuntimeService.probe_live_api",
                return_value={
                    "ready": False,
                    "api_online": True,
                    "base_url": "http://127.0.0.1:8188",
                    "missing_class_types": [],
                    "missing_models": {
                        "checkpoints": ["ltx-video-2b-v0.9.5.safetensors"],
                        "text_encoders": ["t5xxl_fp8_e4m3fn.safetensors"],
                    },
                    "reason": "Required model files are not visible in the active ComfyUI model catalog.",
                },
            ):
                flags = _check_optional_runtime_capabilities(settings)

            backend = flags["video_generation"]["production_short_video"]["backends"]["ltx"]
            assert backend["comfyui_live"] is True
            assert backend["runtime_ready"] is False
            assert backend["missing_runtime_models"]["checkpoints"] == [
                "ltx-video-2b-v0.9.5.safetensors"
            ]
            assert flags["video_generation"]["status"] != "ready"
