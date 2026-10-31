from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from atnos.model_manager.model_manager import ModelManager
from atnos.model_manager.model_manager import PIPELINE_MODELS
from atnos.model_manager.downloader import (
    HF_HUB_PACKAGE,
    get_download_progress_from_hf_cache,
    _ensure_hf_hub,
    _remove_stale_zero_byte_hf_locks,
    _replace_downloaded_file,
    _safe_cache_filename,
)
from atnos.model_manager.registry import ModelSpec, build_model_registry, resolve_comfyui_root
from atnos.model_manager.verifier import verify_model_integrity
from atnos.model_manager.vram_monitor import wait_for_vram

from .test_support import create_test_app, make_temp_dir


def _size_gb(path: Path) -> float:
    return path.stat().st_size / (1024**3)


def _write_model(path: Path, size_bytes: int = 1024 * 1024) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size_bytes)


def _spec(path: Path, *, size_gb: float | None = None, min_vram_gb: float = 0.0) -> ModelSpec:
    return ModelSpec(
        id="tiny",
        name="Tiny Test Model",
        category="test",
        hf_repo="example/tiny",
        hf_filename=path.name,
        local_path=path,
        size_gb=size_gb if size_gb is not None else 0.001,
        required=False,
        min_vram_gb=min_vram_gb,
        tags=["test"],
    )


def test_verify_model_integrity_reports_ok_for_existing_model() -> None:
    with make_temp_dir() as temp_dir:
        model_path = Path(temp_dir) / "tiny.bin"
        _write_model(model_path)
        result = asyncio.run(verify_model_integrity(_spec(model_path, size_gb=_size_gb(model_path))))

        assert result["status"] == "ok"
        assert result["exists"] is True
        assert result["size_ok"] is True


def test_verify_model_integrity_reports_missing_model() -> None:
    with make_temp_dir() as temp_dir:
        result = asyncio.run(verify_model_integrity(_spec(Path(temp_dir) / "missing.bin")))

        assert result["status"] == "missing"
        assert result["exists"] is False


def test_ensure_model_returns_existing_path_without_download() -> None:
    with make_temp_dir() as temp_dir:
        model_path = Path(temp_dir) / "tiny.bin"
        _write_model(model_path)
        manager = ModelManager(registry={"tiny": _spec(model_path, size_gb=_size_gb(model_path))})
        progress: list[tuple[str, float, str]] = []

        ok, message = asyncio.run(
            manager.ensure_model(
                "tiny",
                auto_download=False,
                ws_callback=lambda model_id, progress_value, text: progress.append(
                    (model_id, progress_value, text)
                ),
            )
        )

        assert ok is True
        assert message == str(model_path)
        assert progress == [
            ("tiny", 0.0, "checking model"),
            ("tiny", 1.0, "model already available"),
        ]


def test_ensure_model_missing_without_auto_download_is_safe_failure() -> None:
    with make_temp_dir() as temp_dir:
        model_path = Path(temp_dir) / "missing.bin"
        manager = ModelManager(registry={"tiny": _spec(model_path)})
        progress: list[tuple[str, float, str]] = []

        ok, message = asyncio.run(
            manager.ensure_model(
                "tiny",
                auto_download=False,
                ws_callback=lambda model_id, progress_value, text: progress.append(
                    (model_id, progress_value, text)
                ),
            )
        )

        assert ok is False
        assert "not available" in message
        assert not model_path.exists()
        assert progress[0] == ("tiny", 0.0, "checking model")
        assert "not available" in progress[-1][2]


def test_ensure_model_downloads_once_under_concurrent_calls() -> None:
    with make_temp_dir() as temp_dir:
        model_path = Path(temp_dir) / "tiny.bin"
        model_spec = _spec(model_path, size_gb=1024 * 1024 / (1024**3))
        manager = ModelManager(registry={"tiny": model_spec})
        calls = {"count": 0}

        async def fake_download(spec: ModelSpec, **_: object) -> Path:
            calls["count"] += 1
            _write_model(spec.local_path)
            await asyncio.sleep(0)
            return spec.local_path

        async def run_two() -> list[tuple[bool, str]]:
            return await asyncio.gather(
                manager.ensure_model("tiny", auto_download=True),
                manager.ensure_model("tiny", auto_download=True),
            )

        with patch("atnos.model_manager.model_manager.download_model", side_effect=fake_download):
            results = asyncio.run(run_two())

        assert results == [(True, str(model_path)), (True, str(model_path))]
        assert calls["count"] == 1


def test_ensure_model_downloads_even_when_vram_is_currently_insufficient() -> None:
    with make_temp_dir() as temp_dir:
        model_path = Path(temp_dir) / "tiny.bin"
        model_spec = _spec(model_path, size_gb=1024 * 1024 / (1024**3), min_vram_gb=8.0)
        manager = ModelManager(registry={"tiny": model_spec})
        progress: list[tuple[str, float, str]] = []

        async def fake_download(spec: ModelSpec, **_: object) -> Path:
            _write_model(spec.local_path)
            return spec.local_path

        with (
            patch("atnos.model_manager.model_manager.check_vram_sufficient", return_value=False),
            patch("atnos.model_manager.model_manager.download_model", side_effect=fake_download),
        ):
            ok, message = asyncio.run(
                manager.ensure_model(
                    "tiny",
                    auto_download=True,
                    ws_callback=lambda model_id, progress_value, text: progress.append(
                        (model_id, progress_value, text)
                    ),
                )
            )

        assert ok is True
        assert message == str(model_path)
        assert model_path.exists()
        assert any("VRAM below runtime target" in item[2] for item in progress)


def test_wait_for_vram_returns_after_capacity_becomes_available() -> None:
    calls = {"count": 0}

    async def fake_vram_info() -> dict[str, float | str]:
        calls["count"] += 1
        free = 1.0 if calls["count"] == 1 else 12.0
        return {
            "total_gb": 16.0,
            "used_gb": 16.0 - free,
            "free_gb": free,
            "utilization_pct": 0.0,
            "source": "test",
        }

    with patch("atnos.model_manager.vram_monitor.get_vram_info", side_effect=fake_vram_info):
        ok = asyncio.run(wait_for_vram(8.0, timeout_s=1.0, check_interval_s=0.01))

    assert ok is True
    assert calls["count"] == 2


def test_hf_cache_progress_reads_incomplete_download_files() -> None:
    with make_temp_dir() as temp_dir:
        model_path = Path(temp_dir) / "models" / "big.safetensors"
        cache_dir = model_path.parent / ".cache" / "huggingface" / "download"
        cache_dir.mkdir(parents=True)
        incomplete = cache_dir / "abc123.incomplete"
        incomplete.write_bytes(b"x" * 512 * 1024)

        progress = asyncio.run(
            get_download_progress_from_hf_cache(
                _spec(model_path, size_gb=(1024 * 1024) / (1024**3))
            )
        )

        assert 0.49 <= progress <= 0.51


def test_hf_cache_progress_sanitizes_nested_hf_filenames() -> None:
    with make_temp_dir() as temp_dir:
        model_path = Path(temp_dir) / "models" / "ltx_vae.safetensors"
        part_path = model_path.parent / ".vae_diffusion_pytorch_model.safetensors.part"
        part_path.parent.mkdir(parents=True)
        part_path.write_bytes(b"x" * 512 * 1024)
        spec = ModelSpec(
            id="ltx_vae",
            name="LTX VAE",
            category="vae",
            hf_repo="Lightricks/LTX-Video",
            hf_filename="vae/diffusion_pytorch_model.safetensors",
            local_path=model_path,
            size_gb=(1024 * 1024) / (1024**3),
            required=True,
            tags=["ltx"],
        )

        assert _safe_cache_filename(spec.hf_filename) == "vae_diffusion_pytorch_model.safetensors"
        progress = asyncio.run(get_download_progress_from_hf_cache(spec))

        assert 0.49 <= progress <= 0.51


def test_replace_downloaded_file_overwrites_stale_corrupt_target() -> None:
    with make_temp_dir() as temp_dir:
        root = Path(temp_dir)
        source = root / "hf-cache" / "model.safetensors"
        target = root / "models" / "model.safetensors"
        source.parent.mkdir(parents=True)
        target.parent.mkdir(parents=True)
        source.write_bytes(b"downloaded-model")
        target.write_bytes(b"stale-corrupt")

        _replace_downloaded_file(source, target)

        assert target.read_bytes() == b"downloaded-model"
        assert not source.exists()


def test_stale_hf_lock_cleanup_keeps_partial_download_data() -> None:
    with make_temp_dir() as temp_dir:
        model_path = Path(temp_dir) / "models" / "big.safetensors"
        cache_dir = model_path.parent / ".cache" / "huggingface" / "download"
        cache_dir.mkdir(parents=True)
        stale_lock = cache_dir / "big.safetensors.lock"
        stale_lock.write_bytes(b"")
        partial = cache_dir / "etag.incomplete"
        partial.write_bytes(b"x" * 128)

        _remove_stale_zero_byte_hf_locks(_spec(model_path))

        assert not stale_lock.exists()
        assert partial.exists()
        assert partial.stat().st_size == 128


def test_hf_hub_dependency_auto_install_when_missing() -> None:
    calls: list[list[str]] = []

    def fake_find_spec(name: str):
        assert name == "huggingface_hub"
        return None

    def fake_check_call(cmd: list[str], **_: object) -> int:
        calls.append(cmd)
        return 0

    with (
        patch("atnos.model_manager.downloader.importlib.util.find_spec", side_effect=fake_find_spec),
        patch("atnos.model_manager.downloader.subprocess.check_call", side_effect=fake_check_call),
    ):
        _ensure_hf_hub()

    assert calls
    assert calls[0][0].endswith("python.exe") or "python" in calls[0][0].lower()
    assert calls[0][1:4] == ["-m", "pip", "install"]
    assert HF_HUB_PACKAGE in calls[0]
    assert "--break-system-packages" in calls[0]


def test_models_status_api_reports_registry_without_downloads() -> None:
    ModelManager.reset_instance()
    with make_temp_dir() as temp_dir:
        app = create_test_app(Path(temp_dir))
        with patch(
            "atnos.model_manager.model_manager.get_vram_info",
            return_value={
                "total_gb": 16.0,
                "used_gb": 0.0,
                "free_gb": 16.0,
                "utilization_pct": 0.0,
                "source": "test",
            },
        ):
            with TestClient(app) as client:
                response = client.get("/api/models/status")

        assert response.status_code == 200
        payload = response.json()
        assert "sdxl_base_vae" in payload["models"]
        assert payload["models"]["sdxl_base_vae"]["status"] == "missing"
        assert "shorts_full" in payload["pipelines"]
        assert payload["pipeline_statuses"]["shorts_full"]["ready"] is False
        assert "sdxl_base_vae" in payload["pipeline_statuses"]["shorts_full"]["missing"]
        assert "ltx_video" in payload["pipeline_statuses"]["shorts_full"]["missing"]
        assert "wan_video_1_3b" in payload["pipeline_statuses"]["shorts_full"]["missing"]
        assert payload["pipeline_statuses"]["shorts_full"]["download_endpoint"] == (
            "POST /api/models/ensure-pipeline/shorts_full?wait=true"
        )
        assert payload["pipeline_statuses"]["shorts_full"]["download_status_endpoint"] == (
            "GET /api/models/downloads/shorts_full"
        )


def test_ensure_model_wait_returns_ready_path() -> None:
    ModelManager.reset_instance()
    with make_temp_dir() as temp_dir:
        app = create_test_app(Path(temp_dir))

        async def fake_ensure_model(model_id: str, **_: object):
            assert model_id == "sdxl_base_vae"
            return True, "C:\\models\\sdxl.safetensors"

        with patch.object(ModelManager, "ensure_model", side_effect=fake_ensure_model):
            with TestClient(app) as client:
                response = client.post("/api/models/ensure/sdxl_base_vae?wait=true")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["model_id"] == "sdxl_base_vae"
    assert payload["path"] == "C:\\models\\sdxl.safetensors"
    assert payload["message"] is None


def test_ensure_model_wait_returns_failed_message() -> None:
    ModelManager.reset_instance()
    with make_temp_dir() as temp_dir:
        app = create_test_app(Path(temp_dir))

        async def fake_ensure_model(model_id: str, **_: object):
            assert model_id == "sdxl_base_vae"
            return False, "download failed"

        with patch.object(ModelManager, "ensure_model", side_effect=fake_ensure_model):
            with TestClient(app) as client:
                response = client.post("/api/models/ensure/sdxl_base_vae?wait=true")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["model_id"] == "sdxl_base_vae"
    assert payload["path"] is None
    assert payload["message"] == "download failed"


def test_ensure_model_background_keeps_download_started_contract() -> None:
    ModelManager.reset_instance()
    with make_temp_dir() as temp_dir:
        app = create_test_app(Path(temp_dir))

        async def fake_ensure_model(model_id: str, **_: object):
            assert model_id == "sdxl_base_vae"
            return True, "ready"

        with patch.object(ModelManager, "ensure_model", side_effect=fake_ensure_model):
            with TestClient(app) as client:
                response = client.post("/api/models/ensure/sdxl_base_vae")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "download_started"
    assert payload["model_id"] == "sdxl_base_vae"


def test_ensure_pipeline_wait_returns_model_results() -> None:
    ModelManager.reset_instance()
    with make_temp_dir() as temp_dir:
        app = create_test_app(Path(temp_dir))

        async def fake_ensure_pipeline_models(pipeline: str, **_: object):
            assert pipeline == "shorts_full"
            return {
                "sdxl_base_vae": True,
                "ltx_video": True,
                "ltx_vae": True,
                "t5_encoder": False,
                "wan_video_1_3b": True,
            }

        with patch.object(ModelManager, "ensure_pipeline_models", side_effect=fake_ensure_pipeline_models):
            with TestClient(app) as client:
                response = client.post("/api/models/ensure-pipeline/shorts_full?wait=true")

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "partial"
        assert payload["pipeline"] == "shorts_full"
        assert payload["results"]["t5_encoder"] is False

        with TestClient(app) as client:
            status_response = client.get("/api/models/downloads/shorts_full")

        assert status_response.status_code == 200
        status_payload = status_response.json()
        assert status_payload["status"] == "partial"
        assert status_payload["results"]["sdxl_base_vae"] is True
        assert status_payload["results"]["t5_encoder"] is False
        assert set(status_payload["progress"]) == set(PIPELINE_MODELS["shorts_full"])
        assert status_payload["progress"]["sdxl_base_vae"] == 1.0
        assert status_payload["progress"]["t5_encoder"] == 0.0
        assert status_payload["messages"]["sdxl_base_vae"] == "ready"
        assert status_payload["messages"]["t5_encoder"] == "failed"


def test_model_download_status_is_idle_before_pipeline_starts() -> None:
    from atnos.api.routes.models import _PIPELINE_DOWNLOAD_STATUS

    _PIPELINE_DOWNLOAD_STATUS.clear()
    ModelManager.reset_instance()
    with make_temp_dir() as temp_dir:
        app = create_test_app(Path(temp_dir))
        with TestClient(app) as client:
            response = client.get("/api/models/downloads/shorts_full")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "idle"
    assert payload["pipeline"] == "shorts_full"
    assert payload["progress"] == {}


def test_background_pipeline_download_records_error_status() -> None:
    from atnos.api.routes.models import _PIPELINE_DOWNLOAD_STATUS

    _PIPELINE_DOWNLOAD_STATUS.clear()
    ModelManager.reset_instance()
    with make_temp_dir() as temp_dir:
        app = create_test_app(Path(temp_dir))

        async def fake_ensure_pipeline_models(pipeline: str, **_: object):
            raise RuntimeError("hf download failed")

        with patch.object(ModelManager, "ensure_pipeline_models", side_effect=fake_ensure_pipeline_models):
            with TestClient(app) as client:
                response = client.post("/api/models/ensure-pipeline/shorts_full")

        assert response.status_code == 200
        assert response.json()["status"] == "download_started"
        status = _PIPELINE_DOWNLOAD_STATUS["shorts_full"]
        assert status["status"] == "error"
        assert status["completed_at"]
        assert status["messages"]["_error"] == "hf download failed"
        assert set(status["progress"]) == set(PIPELINE_MODELS["shorts_full"])
        assert all(value == 0.0 for value in status["progress"].values())
        assert status["messages"]["sdxl_base_vae"] == "queued"


def test_shorts_full_pipeline_includes_ltx_wan_and_sdxl_models() -> None:
    required = set(PIPELINE_MODELS["shorts_full"])

    assert {"sdxl_base_vae", "ltx_video", "ltx_vae", "t5_encoder", "wan_video_1_3b"}.issubset(required)


def test_registry_treats_raw_sdxl_base_as_optional_variant() -> None:
    registry = build_model_registry(None)

    assert registry["sdxl_base_vae"].required is True
    assert registry["sdxl_base"].required is False
    assert "optional" in registry["sdxl_base"].notes.lower()


def test_shorts_full_registry_points_to_known_public_hf_artifacts() -> None:
    registry = build_model_registry(None)

    assert registry["ltx_vae"].hf_repo == "Lightricks/LTX-Video"
    assert registry["ltx_vae"].hf_filename == "vae/diffusion_pytorch_model.safetensors"
    assert registry["ltx_vae"].size_gb >= 1.5
    assert registry["t5_encoder"].hf_repo == "fmoraes2k/t5xxl_fp8_e4m3fn.safetensors"
    assert registry["t5_encoder"].hf_filename == "t5xxl_fp8_e4m3fn.safetensors"


def test_resolve_comfyui_root_supports_windows_portable_layout() -> None:
    with make_temp_dir() as temp_dir:
        app = create_test_app(Path(temp_dir))
        portable_root = Path(temp_dir) / "ComfyUI_windows_portable"
        actual_root = portable_root / "ComfyUI"
        actual_root.mkdir(parents=True)
        (actual_root / "main.py").write_text("# comfyui", encoding="utf-8")
        app.state.settings.comfyui_install_dir = portable_root

        assert resolve_comfyui_root(app.state.settings) == actual_root.resolve()
        assert build_model_registry(app.state.settings)["sdxl_base_vae"].local_path.is_relative_to(
            actual_root.resolve()
        )


def test_resolve_comfyui_root_supports_direct_source_checkout() -> None:
    with make_temp_dir() as temp_dir:
        app = create_test_app(Path(temp_dir))
        direct_root = Path(temp_dir) / "ComfyUI"
        direct_root.mkdir(parents=True)
        (direct_root / "main.py").write_text("# comfyui", encoding="utf-8")
        app.state.settings.comfyui_install_dir = direct_root

        assert resolve_comfyui_root(app.state.settings) == direct_root.resolve()
        assert build_model_registry(app.state.settings)["ltx_video"].local_path.is_relative_to(
            direct_root.resolve()
        )


def test_registry_honors_explicit_shared_comfyui_models_dir() -> None:
    with make_temp_dir() as temp_dir:
        app = create_test_app(Path(temp_dir))
        shared_models = Path(temp_dir) / "shared-models"
        shared_models.mkdir(parents=True)
        app.state.settings.comfyui_models_dir = shared_models

        registry = build_model_registry(app.state.settings)

        assert registry["sdxl_base_vae"].local_path == (
            shared_models / "checkpoints" / "sd_xl_base_1.0_0.9vae.safetensors"
        ).resolve()


def test_registry_uses_existing_cold_ltx_models_without_redownload() -> None:
    with make_temp_dir() as temp_dir:
        app = create_test_app(Path(temp_dir))
        shared_models = Path(temp_dir) / "shared-models"
        cold_models = Path(temp_dir) / "cold-models"
        cold_ltx = cold_models / "checkpoints" / "ltx-video-2b-v0.9.5.safetensors"
        cold_ltx.parent.mkdir(parents=True)
        cold_ltx.write_bytes(b"cold-ltx")
        app.state.settings.comfyui_models_dir = shared_models
        app.state.settings.comfyui_cold_models_dir = cold_models

        registry = build_model_registry(app.state.settings)

        assert registry["ltx_video"].local_path == cold_ltx.resolve()
