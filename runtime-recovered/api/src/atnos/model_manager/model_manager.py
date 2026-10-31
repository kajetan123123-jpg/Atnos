from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Any

from atnos.model_manager.downloader import download_model
from atnos.model_manager.registry import ModelSpec, build_model_registry
from atnos.model_manager.verifier import verify_all_models, verify_model_integrity
from atnos.model_manager.vram_monitor import check_vram_sufficient, get_vram_info

if TYPE_CHECKING:
    from atnos.core.config import Settings

ProgressCallback = Callable[[str, float, str], None]


PIPELINE_MODELS: dict[str, tuple[str, ...]] = {
    "shorts_sdxl": ("sdxl_base_vae",),
    "shorts_ltx": ("ltx_video", "ltx_vae", "t5_encoder"),
    "shorts_wan": ("wan_video_1_3b",),
    "shorts_full": ("sdxl_base_vae", "ltx_video", "ltx_vae", "t5_encoder", "wan_video_1_3b"),
    "tts": ("kokoro_v1", "kokoro_voices_pl"),
    "subtitles": ("whisper_medium",),
}


class ModelManager:
    _instance: "ModelManager | None" = None
    _download_locks: dict[str, asyncio.Lock] = {}

    def __init__(
        self,
        settings: "Settings | None" = None,
        *,
        registry: dict[str, ModelSpec] | None = None,
    ) -> None:
        self.settings = settings
        self.registry = registry or build_model_registry(settings)

    @classmethod
    def get_instance(cls, settings: "Settings | None" = None) -> "ModelManager":
        if cls._instance is None:
            cls._instance = cls(settings)
        elif settings is not None and cls._instance.settings is not settings:
            cls._instance = cls(settings)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None
        cls._download_locks = {}

    async def ensure_model(
        self,
        model_id: str,
        *,
        auto_download: bool = True,
        ws_callback: ProgressCallback | None = None,
    ) -> tuple[bool, str]:
        spec = self.registry.get(model_id)
        if spec is None:
            return False, f"Unknown model: {model_id}"

        lock = self._download_locks.setdefault(model_id, asyncio.Lock())
        async with lock:
            self._emit_progress(ws_callback, model_id, 0.0, "checking model")
            status = await verify_model_integrity(spec)
            if status["status"] == "ok":
                self._emit_progress(ws_callback, model_id, 1.0, "model already available")
                return True, str(spec.local_path)

            if not auto_download:
                message = f"Model {model_id} not available: {status['status']}"
                self._emit_progress(ws_callback, model_id, 0.0, message)
                return False, message

            if spec.min_vram_gb > 0 and not await check_vram_sufficient(spec.min_vram_gb):
                self._emit_progress(
                    ws_callback,
                    model_id,
                    0.0,
                    f"VRAM below runtime target ({spec.min_vram_gb:.1f}GB); downloading anyway.",
                )

            try:
                await download_model(spec, progress_callback=ws_callback)
            except Exception as exc:
                self._emit_progress(ws_callback, model_id, 0.0, f"model download failed: {exc}")
                return False, str(exc)

            verified = await verify_model_integrity(spec)
            if verified["status"] == "ok":
                self._emit_progress(ws_callback, model_id, 1.0, "model ready")
                return True, str(spec.local_path)
            self._emit_progress(ws_callback, model_id, 0.0, f"model verification failed: {verified['status']}")
            return False, f"Download failed verification: {verified['status']}"

    async def ensure_pipeline_models(
        self,
        pipeline: str,
        *,
        ws_callback: ProgressCallback | None = None,
    ) -> dict[str, bool]:
        model_ids = PIPELINE_MODELS.get(pipeline, ())
        results: dict[str, bool] = {}
        for model_id in model_ids:
            ok, _ = await self.ensure_model(
                model_id,
                auto_download=True,
                ws_callback=ws_callback,
            )
            results[model_id] = ok
        return results

    async def get_status(self) -> dict[str, Any]:
        verifications = await verify_all_models(registry=self.registry)
        vram = await get_vram_info()
        models = {
            item["model_id"]: self._status_item(item)
            for item in verifications
        }
        pipeline_statuses = {
            pipeline: self._pipeline_status(pipeline, model_ids, models)
            for pipeline, model_ids in PIPELINE_MODELS.items()
        }
        return {
            "vram": vram,
            "models": models,
            "pipelines": {key: list(value) for key, value in PIPELINE_MODELS.items()},
            "pipeline_statuses": pipeline_statuses,
            "total_downloaded_gb": round(
                sum(float(item["size_actual_gb"]) for item in verifications if item["exists"]),
                3,
            ),
        }

    async def delete_model(self, model_id: str) -> bool:
        spec = self.registry.get(model_id)
        if spec is None or not spec.local_path.exists() or not spec.local_path.is_file():
            return False
        spec.local_path.unlink()
        return True

    def list_models_by_tag(self, tag: str) -> list[ModelSpec]:
        return [spec for spec in self.registry.values() if tag in spec.tags]

    def get_model_path(self, model_id: str) -> Path | None:
        spec = self.registry.get(model_id)
        return spec.local_path if spec is not None else None

    @staticmethod
    def _emit_progress(
        callback: ProgressCallback | None,
        model_id: str,
        progress: float,
        message: str,
    ) -> None:
        if callback is None:
            return
        callback(model_id, max(0.0, min(1.0, progress)), message)

    def _status_item(self, verification: dict[str, Any]) -> dict[str, Any]:
        model_id = str(verification["model_id"])
        spec = self.registry[model_id]
        return {
            **verification,
            "name": spec.name,
            "size_gb": spec.size_gb,
            "category": spec.category,
            "required": spec.required,
            "min_vram_gb": spec.min_vram_gb,
            "tags": list(spec.tags),
            "local_path": str(spec.local_path),
            "hf_repo": spec.hf_repo,
            "hf_filename": spec.hf_filename,
            "notes": spec.notes,
        }

    @staticmethod
    def _pipeline_status(
        pipeline: str,
        model_ids: tuple[str, ...],
        models: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        missing = [
            model_id
            for model_id in model_ids
            if not isinstance(models.get(model_id), dict) or models[model_id].get("status") != "ok"
        ]
        return {
            "pipeline": pipeline,
            "model_ids": list(model_ids),
            "ready": not missing,
            "missing": missing,
            "download_endpoint": f"POST /api/models/ensure-pipeline/{pipeline}?wait=true",
            "download_status_endpoint": f"GET /api/models/downloads/{pipeline}",
        }
