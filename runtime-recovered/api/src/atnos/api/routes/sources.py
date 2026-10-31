from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from atnos.services.source_envelope_service import (
    SourceContextSummary,
    SourceEnvelope,
    SourceEnvelopeService,
)

router = APIRouter(tags=["sources"])


class SourceSchemaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: list[str]
    allowed_sources: list[str]
    privacy_classes: list[str]
    retention_policies: list[str]
    permission_modes: list[str]
    memory_eligibility: list[str]
    prompt_injection_risks: list[str]
    preview_limits: dict[str, int]


class SourceEnvelopePreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    envelope: SourceEnvelope
    summary: SourceContextSummary


class ExternalSourceStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    crawl4ai: dict[str, Any]
    public_apis: dict[str, Any]
    turbovec: dict[str, Any]
    easyslice_ai: dict[str, Any]
    checked_at: str


class PublicApiCatalogResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: list[dict[str, Any]]


class PublicApiCatalogIngestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    entry_count: int
    note_id: str
    curated_path: str
    knowledge_path: str


class ExternalCrawlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=8)
    bucket: str = "general"
    title: str | None = None
    research_focus: str | None = None
    tags: list[str] = Field(default_factory=list)
    semantic_reindex: bool = False


class ExternalCrawlIngestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    note_id: str
    curated_path: str
    knowledge_path: str
    crawl: dict[str, Any]
    semantic_refresh: dict[str, Any] | None = None


def _external_learning_service(request: Request):
    from atnos.services.external_source_learning_service import ExternalSourceLearningService

    return ExternalSourceLearningService(request.app.state.settings)


@router.get("/sources/schema", response_model=SourceSchemaResponse)
def get_source_schema() -> SourceSchemaResponse:
    service = SourceEnvelopeService()
    return SourceSchemaResponse(**service.schema())


@router.post("/sources/preview-envelope", response_model=SourceEnvelopePreviewResponse)
def preview_source_envelope(payload: SourceEnvelope) -> SourceEnvelopePreviewResponse:
    service = SourceEnvelopeService()
    envelope, summary = service.preview_envelope(payload)
    return SourceEnvelopePreviewResponse(envelope=envelope, summary=summary)


@router.get("/sources/external/status", response_model=ExternalSourceStatusResponse)
def get_external_source_status() -> ExternalSourceStatusResponse:
    from atnos.services.external_source_learning_service import external_source_learning_status

    return ExternalSourceStatusResponse(**external_source_learning_status())


@router.get("/sources/external/public-apis", response_model=PublicApiCatalogResponse)
def get_public_api_catalog(request: Request, category: str | None = None) -> PublicApiCatalogResponse:
    entries = _external_learning_service(request).public_api_catalog(category=category)
    return PublicApiCatalogResponse(entries=entries)


@router.post("/sources/external/public-apis/ingest", response_model=PublicApiCatalogIngestResponse)
def ingest_public_api_catalog(request: Request) -> PublicApiCatalogIngestResponse:
    payload = _external_learning_service(request).ingest_public_api_catalog()
    return PublicApiCatalogIngestResponse(**payload)


@router.post("/sources/external/crawl", response_model=ExternalCrawlIngestResponse)
async def crawl_external_source(request: Request, payload: ExternalCrawlRequest) -> ExternalCrawlIngestResponse:
    try:
        result = await _external_learning_service(request).crawl_and_ingest(
            url=payload.url,
            bucket=payload.bucket,
            title=payload.title,
            research_focus=payload.research_focus,
            tags=payload.tags,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    semantic_refresh = None
    if payload.semantic_reindex:
        try:
            semantic_refresh = request.app.state.semantic_knowledge_index.reindex(force=False)
        except Exception as exc:  # pragma: no cover - endpoint must not lose the persisted source
            semantic_refresh = {"status": "failed", "error": str(exc)}
    return ExternalCrawlIngestResponse(**result, semantic_refresh=semantic_refresh)
