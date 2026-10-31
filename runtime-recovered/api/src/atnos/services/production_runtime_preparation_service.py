from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from typing import Any

from atnos.core.config import Settings
from atnos.model_manager.model_manager import ModelManager, PIPELINE_MODELS
from atnos.production.readiness import ShortProductionReadinessService
from atnos.services.comfyui_runtime_service import ComfyUiRuntimeReadiness, ComfyUiRuntimeService
from atnos.services.ollama_model_roles_service import OllamaModelRoleSnapshot, OllamaModelRolesService


@dataclass(frozen=True, slots=True)
class ProductionRuntimePreparationResult:
    status: str
    required_model_pipeline: str
    local_runtime_ready: bool
    publish_ready: bool
    full_pipeline_ready: bool
    ollama: dict[str, Any]
    ollama_models: dict[str, Any]
    comfyui: dict[str, Any]
    models: dict[str, Any]
    readiness: dict[str, Any]
    next_actions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "required_model_pipeline": self.required_model_pipeline,
            "local_runtime_ready": self.local_runtime_ready,
            "publish_ready": self.publish_ready,
            "full_pipeline_ready": self.full_pipeline_ready,
            "ollama": self.ollama,
            "ollama_models": self.ollama_models,
            "comfyui": self.comfyui,
            "models": self.models,
            "readiness": self.readiness,
            "next_actions": list(self.next_actions),
        }


class ProductionRuntimePreparationService:
    """Prepare local short-production runtimes without starting a production job."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def prepare(
        self,
        *,
        start_comfyui: bool = True,
        comfyui_wait_timeout_s: int = 180,
        ensure_models: bool = False,
        ensure_ollama_models: bool = False,
        model_pipeline: str = "shorts_sdxl",
    ) -> ProductionRuntimePreparationResult:
        if model_pipeline not in PIPELINE_MODELS:
            raise ValueError(f"Unknown production model pipeline: {model_pipeline}")
        ollama_service = OllamaModelRolesService(self.settings)
        ollama_snapshot = await asyncio.to_thread(ollama_service.probe)
        ollama_ensure = self._ollama_auto_pull_status(None)
        if ensure_ollama_models:
            ollama_ensure = await asyncio.to_thread(ollama_service.pull_missing_models, ollama_snapshot)
            ollama_snapshot = await asyncio.to_thread(ollama_service.probe)
        comfyui_readiness = await asyncio.to_thread(
            self._prepare_comfyui,
            start_comfyui=start_comfyui,
            wait_timeout_s=comfyui_wait_timeout_s,
        )
        model_ensure_results: dict[str, bool] | None = None
        if ensure_models:
            model_ensure_results = await ModelManager.get_instance(self.settings).ensure_pipeline_models(model_pipeline)
        models_status = await self._models_status(required_pipeline=model_pipeline)
        models_status["auto_download"] = self._auto_download_status(
            model_ensure_results,
            pipeline=model_pipeline,
        )
        readiness = await ShortProductionReadinessService(self.settings).check(include_live_checks=True)
        next_actions = tuple(
            self._next_actions(
                ollama_snapshot,
                comfyui_readiness,
                models_status,
                readiness,
                required_pipeline=model_pipeline,
            )
        )
        models_ready = bool(models_status.get("pipelines", {}).get(model_pipeline, {}).get("ready"))
        local_runtime_ready = bool(ollama_snapshot.ready and comfyui_readiness.image_generation and models_ready)
        publish_ready = self._readiness_item_ready(readiness, "youtube_oauth") and self._readiness_item_ready(readiness, "scheduler")
        full_pipeline_ready = bool(local_runtime_ready and publish_ready and readiness.get("ready"))
        status = "ready" if full_pipeline_ready else ("local_runtime_ready" if local_runtime_ready else "needs_action")
        return ProductionRuntimePreparationResult(
            status=status,
            required_model_pipeline=model_pipeline,
            local_runtime_ready=local_runtime_ready,
            publish_ready=publish_ready,
            full_pipeline_ready=full_pipeline_ready,
            ollama=ollama_snapshot.to_dict(),
            ollama_models=ollama_ensure,
            comfyui=asdict(comfyui_readiness),
            models=models_status,
            readiness=readiness,
            next_actions=next_actions,
        )

    @staticmethod
    def _readiness_item_ready(readiness: dict[str, Any], key: str) -> bool:
        items = readiness.get("items") if isinstance(readiness, dict) else None
        if not isinstance(items, list):
            return False
        for item in items:
            if isinstance(item, dict) and item.get("key") == key:
                return bool(item.get("ready"))
        return False

    @staticmethod
    def _ollama_auto_pull_status(results: dict[str, Any] | None) -> dict[str, Any]:
        if results is None:
            return {
                "requested": False,
                "status": "not_requested",
                "results": None,
            }
        return dict(results)

    @staticmethod
    def _auto_download_status(results: dict[str, bool] | None, *, pipeline: str) -> dict[str, Any]:
        if results is None:
            return {
                "requested": False,
                "pipeline": pipeline,
                "status": "not_requested",
                "results": None,
            }
        return {
            "requested": True,
            "pipeline": pipeline,
            "status": "ready" if all(results.values()) else "partial",
            "results": dict(results),
        }

    def _prepare_comfyui(
        self,
        *,
        start_comfyui: bool,
        wait_timeout_s: int,
    ) -> ComfyUiRuntimeReadiness:
        service = ComfyUiRuntimeService(self.settings)
        if start_comfyui:
            return service.ensure_startup_ready(wait_timeout_s=wait_timeout_s)
        if service._server_ready():  # noqa: SLF001 - intentionally reuses the runtime probe without launching.
            return ComfyUiRuntimeReadiness(image_generation=True, detail="ComfyUI already ready")
        install = service.describe_install()
        paths = ComfyUiRuntimeService._readiness_paths(install)  # noqa: SLF001 - serializes install diagnostics.
        return ComfyUiRuntimeReadiness(
            image_generation=False,
            reason="ComfyUI not running",
            detail=(
                "ComfyUI live check failed and auto-start was disabled for this request."
                if install.exists
                else "ComfyUI install path was not found."
            ),
            **paths,
            log_tail=service.error_log_tail(),
        )

    async def _models_status(self, *, required_pipeline: str = "shorts_sdxl") -> dict[str, Any]:
        status = await ModelManager.get_instance(self.settings).get_status()
        models = status.get("models", {}) if isinstance(status, dict) else {}
        pipelines = status.get("pipelines", {}) if isinstance(status, dict) else {}
        raw_pipeline_statuses = status.get("pipeline_statuses", {}) if isinstance(status, dict) else {}
        pipeline_statuses = self._normalize_pipeline_statuses(
            raw_pipeline_statuses=raw_pipeline_statuses if isinstance(raw_pipeline_statuses, dict) else {},
            pipelines=pipelines if isinstance(pipelines, dict) else {},
            models=models if isinstance(models, dict) else {},
        )
        return {
            "ready": bool(pipeline_statuses[required_pipeline]["ready"]),
            "required_pipeline": required_pipeline,
            "pipelines": pipeline_statuses,
            "vram": status.get("vram", {}) if isinstance(status, dict) else {},
            "auto_download_endpoint": f"POST /api/models/ensure-pipeline/{required_pipeline}?wait=true",
            "auto_download_endpoints": {
                pipeline: f"POST /api/models/ensure-pipeline/{pipeline}?wait=true"
                for pipeline in pipeline_statuses
            },
        }

    def _normalize_pipeline_statuses(
        self,
        *,
        raw_pipeline_statuses: dict[str, Any],
        pipelines: dict[str, Any],
        models: dict[str, Any],
    ) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for pipeline in ("shorts_sdxl", "shorts_ltx", "shorts_wan", "shorts_full", "tts", "subtitles"):
            raw = raw_pipeline_statuses.get(pipeline)
            if isinstance(raw, dict):
                model_ids = list(raw.get("model_ids") or pipelines.get(pipeline) or PIPELINE_MODELS.get(pipeline, ()))
                missing = list(raw.get("missing") or [])
                normalized[pipeline] = {
                    "model_ids": model_ids,
                    "ready": bool(raw.get("ready")),
                    "missing": missing,
                    "download_endpoint": raw.get("download_endpoint")
                    or f"POST /api/models/ensure-pipeline/{pipeline}?wait=true",
                    "download_status_endpoint": raw.get("download_status_endpoint")
                    or f"GET /api/models/downloads/{pipeline}",
                }
                continue
            normalized[pipeline] = self._pipeline_model_status(
                pipeline=pipeline,
                model_ids=list(pipelines.get(pipeline) or PIPELINE_MODELS.get(pipeline, ())),
                models=models,
            )
        return normalized

    @staticmethod
    def _pipeline_model_status(*, pipeline: str, model_ids: list[str], models: dict[str, Any]) -> dict[str, Any]:
        missing = [
            model_id
            for model_id in model_ids
            if not isinstance(models.get(model_id), dict) or models[model_id].get("status") != "ok"
        ]
        return {
            "model_ids": model_ids,
            "ready": not missing,
            "missing": missing,
            "download_endpoint": f"POST /api/models/ensure-pipeline/{pipeline}?wait=true",
            "download_status_endpoint": f"GET /api/models/downloads/{pipeline}",
        }

    def _next_actions(
        self,
        ollama_snapshot: OllamaModelRoleSnapshot,
        comfyui_readiness: ComfyUiRuntimeReadiness,
        models_status: dict[str, Any],
        readiness: dict[str, Any],
        required_pipeline: str,
    ) -> list[str]:
        actions: list[str] = []
        required_models = models_status.get("pipelines", {}).get(required_pipeline, {})
        local_runtime_incomplete = (
            not ollama_snapshot.ready
            or not comfyui_readiness.image_generation
            or (isinstance(required_models, dict) and not required_models.get("ready"))
        )
        if local_runtime_incomplete:
            actions.append(
                "Jednym krokiem przygotuj lokalny runtime: POST /api/production/runtime/prepare "
                "z JSON "
                f'{{"start_comfyui": true, "ensure_models": true, "ensure_ollama_models": false, '
                f'"model_pipeline": "{required_pipeline}"}}.'
            )
        actions.extend(str(item) for item in ollama_snapshot.suggestions if str(item).strip())
        if not comfyui_readiness.image_generation:
            actions.append("Uruchom ComfyUI albo wywolaj /api/production/runtime/prepare z start_comfyui=true.")
        if isinstance(required_models, dict) and not required_models.get("ready"):
            missing = ", ".join(str(item) for item in (required_models.get("missing") or []))
            actions.append(
                f"Pobierz brakujace modele {required_pipeline} przez "
                f"POST /api/models/ensure-pipeline/{required_pipeline}?wait=true"
                + (f" ({missing})." if missing else ".")
            )
        actions.extend(str(item) for item in readiness.get("next_actions", []) if str(item).strip())
        deduped: list[str] = []
        seen: set[str] = set()
        for action in actions:
            if action in seen:
                continue
            seen.add(action)
            deduped.append(action)
        return deduped[:12]
