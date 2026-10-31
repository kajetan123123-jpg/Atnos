from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status
from pydantic import BaseModel

router = APIRouter(prefix="/api/models", tags=["models"])

_PIPELINE_DOWNLOAD_STATUS: dict[str, dict] = {}


class EnsureModelResponse(BaseModel):
    status: str
    model_id: str
    path: str | None = None
    message: str | None = None


class EnsurePipelineResponse(BaseModel):
    status: str
    pipeline: str
    results: dict[str, bool] | None = None


class ModelDownloadProgressResponse(BaseModel):
    pipeline: str
    status: str
    started_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None
    current_model_id: str | None = None
    progress: dict[str, float]
    messages: dict[str, str]
    results: dict[str, bool] | None = None


class DeleteModelResponse(BaseModel):
    deleted: bool
    model_id: str


def _model_manager(request: Request):
    from atnos.model_manager.model_manager import ModelManager

    return ModelManager.get_instance(request.app.state.settings)


@router.get("/status")
async def models_status(request: Request) -> dict:
    return await _model_manager(request).get_status()


@router.post("/ensure/{model_id}", response_model=EnsureModelResponse)
async def ensure_model(
    request: Request,
    model_id: str,
    background_tasks: BackgroundTasks,
    wait: bool = Query(default=False),
) -> EnsureModelResponse:
    manager = _model_manager(request)
    if model_id not in manager.registry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown model: {model_id}")

    if wait:
        ok, message = await manager.ensure_model(model_id, auto_download=True)
        return EnsureModelResponse(
            status="ready" if ok else "failed",
            model_id=model_id,
            path=message if ok else None,
            message=None if ok else message,
        )

    async def _download() -> None:
        await manager.ensure_model(model_id, auto_download=True)

    background_tasks.add_task(_download)
    return EnsureModelResponse(status="download_started", model_id=model_id)


@router.post("/ensure-pipeline/{pipeline}", response_model=EnsurePipelineResponse)
async def ensure_pipeline(
    request: Request,
    pipeline: str,
    background_tasks: BackgroundTasks,
    wait: bool = Query(default=False),
) -> EnsurePipelineResponse:
    from atnos.model_manager.model_manager import PIPELINE_MODELS

    if pipeline not in PIPELINE_MODELS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown pipeline: {pipeline}")
    manager = _model_manager(request)
    model_ids = tuple(PIPELINE_MODELS[pipeline])

    if wait:
        _mark_pipeline_download_started(pipeline, model_ids=model_ids)
        results = await manager.ensure_pipeline_models(
            pipeline,
            ws_callback=lambda model_id, progress, message: _mark_pipeline_download_progress(
                pipeline,
                model_id,
                progress,
                message,
            ),
        )
        status_value = "ready" if all(results.values()) else "partial"
        _mark_pipeline_download_finished(pipeline, status_value, results)
        return EnsurePipelineResponse(status=status_value, pipeline=pipeline, results=results)

    async def _download() -> None:
        _mark_pipeline_download_started(pipeline, model_ids=model_ids)
        try:
            results = await manager.ensure_pipeline_models(
                pipeline,
                ws_callback=lambda model_id, progress, message: _mark_pipeline_download_progress(
                    pipeline,
                    model_id,
                    progress,
                    message,
                ),
            )
        except Exception as exc:
            _mark_pipeline_download_error(pipeline, str(exc))
            return
        status_value = "ready" if all(results.values()) else "partial"
        _mark_pipeline_download_finished(pipeline, status_value, results)

    background_tasks.add_task(_download)
    return EnsurePipelineResponse(status="download_started", pipeline=pipeline)


@router.get("/downloads/{pipeline}", response_model=ModelDownloadProgressResponse)
async def pipeline_download_status(pipeline: str) -> ModelDownloadProgressResponse:
    from atnos.model_manager.model_manager import PIPELINE_MODELS

    if pipeline not in PIPELINE_MODELS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown pipeline: {pipeline}")
    item = _PIPELINE_DOWNLOAD_STATUS.get(pipeline)
    if item is None:
        item = {
            "pipeline": pipeline,
            "status": "idle",
            "started_at": None,
            "updated_at": None,
            "completed_at": None,
            "current_model_id": None,
            "progress": {},
            "messages": {},
            "results": None,
        }
    return ModelDownloadProgressResponse(**item)


@router.delete("/{model_id}", response_model=DeleteModelResponse)
async def delete_model(request: Request, model_id: str) -> DeleteModelResponse:
    deleted = await _model_manager(request).delete_model(model_id)
    return DeleteModelResponse(deleted=deleted, model_id=model_id)


@router.get("/vram")
async def vram_info() -> dict:
    from atnos.model_manager.vram_monitor import get_vram_info

    return await get_vram_info()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mark_pipeline_download_started(pipeline: str, *, model_ids: tuple[str, ...] = ()) -> None:
    now = _now()
    _PIPELINE_DOWNLOAD_STATUS[pipeline] = {
        "pipeline": pipeline,
        "status": "running",
        "started_at": now,
        "updated_at": now,
        "completed_at": None,
        "current_model_id": None,
        "progress": {model_id: 0.0 for model_id in model_ids},
        "messages": {model_id: "queued" for model_id in model_ids},
        "results": None,
    }


def _mark_pipeline_download_progress(
    pipeline: str,
    model_id: str,
    progress: float,
    message: str,
) -> None:
    item = _PIPELINE_DOWNLOAD_STATUS.setdefault(
        pipeline,
        {
            "pipeline": pipeline,
            "status": "running",
            "started_at": _now(),
            "updated_at": None,
            "completed_at": None,
            "current_model_id": None,
            "progress": {},
            "messages": {},
            "results": None,
        },
    )
    item["status"] = "running"
    item["updated_at"] = _now()
    item["current_model_id"] = model_id
    item.setdefault("progress", {})[model_id] = max(0.0, min(1.0, float(progress)))
    item.setdefault("messages", {})[model_id] = message


def _mark_pipeline_download_finished(
    pipeline: str,
    status_value: str,
    results: dict[str, bool],
) -> None:
    now = _now()
    item = _PIPELINE_DOWNLOAD_STATUS.setdefault(
        pipeline,
        {
            "pipeline": pipeline,
            "started_at": now,
            "progress": {},
            "messages": {},
        },
    )
    item["status"] = status_value
    item["updated_at"] = now
    item["completed_at"] = now
    item["current_model_id"] = None
    item["results"] = dict(results)
    for model_id, ok in results.items():
        item.setdefault("progress", {})[model_id] = 1.0 if ok else item.setdefault("progress", {}).get(model_id, 0.0)
        item.setdefault("messages", {})[model_id] = "ready" if ok else "failed"


def _mark_pipeline_download_error(pipeline: str, message: str) -> None:
    now = _now()
    item = _PIPELINE_DOWNLOAD_STATUS.setdefault(
        pipeline,
        {
            "pipeline": pipeline,
            "started_at": now,
            "progress": {},
            "messages": {},
            "results": None,
        },
    )
    item["status"] = "error"
    item["updated_at"] = now
    item["completed_at"] = now
    item["current_model_id"] = None
    item["messages"] = {**dict(item.get("messages") or {}), "_error": message}
