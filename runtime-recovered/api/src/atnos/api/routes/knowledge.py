from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(tags=["knowledge"])


def _load_knowledge_library_service():
    from atnos.services.knowledge_library_service import KnowledgeLibraryService

    return KnowledgeLibraryService


def _load_knowledge_workspace_service():
    from atnos.services.knowledge_workspace_service import KnowledgeWorkspaceService

    return KnowledgeWorkspaceService


def _load_semantic_knowledge_index_service():
    from atnos.services.semantic_knowledge_index_service import SemanticKnowledgeIndexService

    return SemanticKnowledgeIndexService


class KnowledgeBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KnowledgeDocumentResponse(KnowledgeBaseModel):
    doc_id: str
    path: str
    title: str
    topic: str
    summary: str
    tags: list[str]
    keywords: list[str]
    source_label: str
    source_url: str | None
    license: str
    updated_at: str
    size_bytes: int
    source_sha256: str
    source_mtime: str
    source_mtime_ns: int
    indexed_at: str


class KnowledgeCatalogResponse(KnowledgeBaseModel):
    index_version: int
    generated_at: str
    refreshed_at: str
    knowledge_dir: str
    required_frontmatter: list[str]
    document_count: int
    invalid_file_count: int
    documents: list[KnowledgeDocumentResponse]
    invalid_files: list[dict[str, str]]
    refresh_stats: dict[str, object]


class KnowledgeLookupRequest(KnowledgeBaseModel):
    query: str = Field(min_length=1)
    max_docs: int = Field(default=3, ge=1, le=6)
    max_chars: int = Field(default=4500, ge=400, le=12000)


class KnowledgeMatchResponse(KnowledgeBaseModel):
    doc_id: str
    path: str
    title: str
    topic: str
    summary: str
    score: int
    tags: list[str]
    keywords: list[str]
    excerpt: str
    source_label: str
    source_url: str | None
    source_updated_at: str
    indexed_at: str
    citation: str
    excerpt_line_start: int
    excerpt_line_end: int


class KnowledgeLookupResponse(KnowledgeBaseModel):
    query: str
    total_matches: int
    matches: list[KnowledgeMatchResponse]
    system_section: str | None


class SemanticKnowledgeStatusResponse(KnowledgeBaseModel):
    status: str
    index_version: int
    index_path: str
    embedding_model: str
    indexed_embedding_model: str | None
    embedding_dimension: int | None
    chunk_count: int
    last_indexed_at: str | None
    last_index_duration_ms: float | None
    last_query_duration_ms: float | None
    language_support: dict[str, object]
    hardware_requirements: dict[str, object]
    blocked_reason: dict[str, str] | None
    runtime: dict[str, object]


class SemanticKnowledgeReindexRequest(KnowledgeBaseModel):
    force: bool = False


class SemanticKnowledgeReindexResponse(SemanticKnowledgeStatusResponse):
    refresh: dict[str, object]


class HybridKnowledgeLookupRequest(KnowledgeBaseModel):
    query: str = Field(min_length=1)
    max_results: int = Field(default=5, ge=1, le=12)
    topics: list[str] = Field(default_factory=list)
    document_paths: list[str] = Field(default_factory=list)
    source_types: list[str] = Field(default_factory=list)
    project_ids: list[str] = Field(default_factory=list)
    modified_after: str | None = None
    modified_before: str | None = None


class SemanticKnowledgeMatchResponse(KnowledgeBaseModel):
    doc_id: str
    path: str
    title: str
    topic: str
    chunk_id: str
    text: str
    source_label: str
    source_url: str | None
    source_updated_at: str
    source_mtime: str
    source_type: str
    project_id: str | None
    citation: str
    line_start: int
    line_end: int
    score: float
    semantic_score: float
    lexical_score: float
    exact_score: float
    match_reasons: list[str]


class HybridKnowledgeLookupResponse(KnowledgeBaseModel):
    query: str
    total_matches: int
    matches: list[SemanticKnowledgeMatchResponse]


class KnowledgeWorkspaceItemResponse(KnowledgeBaseModel):
    item_id: str
    bucket: str
    inbox_path: str
    source_path: str
    curated_path: str | None
    knowledge_path: str | None
    filename: str
    title: str | None
    status: str
    detected_kind: str | None
    understanding_status: str | None
    preview_chars: int
    imported_at: str


class KnowledgeWorkspaceResponse(KnowledgeBaseModel):
    workspace_dir: str
    inbox_dir: str
    sources_dir: str
    curated_dir: str
    manifests_dir: str
    catalog_path: str
    readme_path: str
    bucket_names: list[str]
    item_count: int


class KnowledgeWorkspaceSyncResponse(KnowledgeWorkspaceResponse):
    processed_count: int
    imported_count: int
    unsupported_count: int
    mirrored_count: int
    skipped_count: int
    knowledge_document_count: int
    items: list[KnowledgeWorkspaceItemResponse]


def _service(request: Request):
    service = getattr(request.app.state, "knowledge_library", None)
    if service is not None:
        return service
    KnowledgeLibraryService = _load_knowledge_library_service()
    fallback = KnowledgeLibraryService(request.app.state.settings)
    fallback.ensure_library()
    return fallback


def _workspace_service(request: Request):
    KnowledgeWorkspaceService = _load_knowledge_workspace_service()
    return KnowledgeWorkspaceService(request.app.state.settings)


def _semantic_service(request: Request):
    service = getattr(request.app.state, "semantic_knowledge_index", None)
    if service is not None:
        return service
    SemanticKnowledgeIndexService = _load_semantic_knowledge_index_service()
    return SemanticKnowledgeIndexService(request.app.state.settings, library=_service(request))


@router.get("/knowledge/catalog", response_model=KnowledgeCatalogResponse)
def get_knowledge_catalog(request: Request) -> KnowledgeCatalogResponse:
    payload = _service(request).read_index()
    return KnowledgeCatalogResponse(**payload)


@router.post("/knowledge/rebuild-index", response_model=KnowledgeCatalogResponse)
def rebuild_knowledge_index(request: Request) -> KnowledgeCatalogResponse:
    payload = _service(request).rebuild_index()
    return KnowledgeCatalogResponse(**payload)


@router.post("/knowledge/refresh-index", response_model=KnowledgeCatalogResponse)
def refresh_knowledge_index(request: Request) -> KnowledgeCatalogResponse:
    """Odświeża przyrostowo jedynie materiały, które zmieniły się na dysku."""
    payload = _service(request).refresh_index()
    return KnowledgeCatalogResponse(**payload)


@router.post("/knowledge/lookup", response_model=KnowledgeLookupResponse)
def lookup_knowledge(request: Request, payload: KnowledgeLookupRequest) -> KnowledgeLookupResponse:
    service = _service(request)
    matches = service.lookup(payload.query, max_docs=payload.max_docs, max_chars=payload.max_chars)
    return KnowledgeLookupResponse(
        query=payload.query,
        total_matches=len(matches),
        matches=[
            KnowledgeMatchResponse(
                doc_id=match.doc_id,
                path=match.path,
                title=match.title,
                topic=match.topic,
                summary=match.summary,
                score=match.score,
                tags=list(match.tags),
                keywords=list(match.keywords),
                excerpt=match.excerpt,
                source_label=match.source_label,
                source_url=match.source_url,
                source_updated_at=match.source_updated_at,
                indexed_at=match.indexed_at,
                citation=match.citation,
                excerpt_line_start=match.excerpt_line_start,
                excerpt_line_end=match.excerpt_line_end,
            )
            for match in matches
        ],
        system_section=service.build_system_section(
            payload.query,
            max_docs=payload.max_docs,
            max_chars=payload.max_chars,
        ),
    )


@router.get("/knowledge/semantic/status", response_model=SemanticKnowledgeStatusResponse)
def get_semantic_knowledge_status(request: Request) -> SemanticKnowledgeStatusResponse:
    return SemanticKnowledgeStatusResponse(**_semantic_service(request).status())


@router.post("/knowledge/semantic/reindex", response_model=SemanticKnowledgeReindexResponse)
def reindex_semantic_knowledge(
    request: Request,
    payload: SemanticKnowledgeReindexRequest,
) -> SemanticKnowledgeReindexResponse:
    from fastapi import HTTPException
    from atnos.services.semantic_knowledge_index_service import SemanticKnowledgeUnavailableError

    try:
        result = _semantic_service(request).reindex(force=payload.force)
    except SemanticKnowledgeUnavailableError as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": exc.message}) from exc
    return SemanticKnowledgeReindexResponse(**result)


@router.post("/knowledge/hybrid-lookup", response_model=HybridKnowledgeLookupResponse)
def hybrid_lookup_knowledge(
    request: Request,
    payload: HybridKnowledgeLookupRequest,
) -> HybridKnowledgeLookupResponse:
    from fastapi import HTTPException
    from atnos.services.semantic_knowledge_index_service import SemanticKnowledgeUnavailableError

    try:
        matches = _semantic_service(request).lookup(
            payload.query,
            max_results=payload.max_results,
            topics=tuple(payload.topics),
            document_paths=tuple(payload.document_paths),
            source_types=tuple(payload.source_types),
            project_ids=tuple(payload.project_ids),
            modified_after=payload.modified_after,
            modified_before=payload.modified_before,
        )
    except SemanticKnowledgeUnavailableError as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": exc.message}) from exc
    return HybridKnowledgeLookupResponse(
        query=payload.query,
        total_matches=len(matches),
        matches=[
            SemanticKnowledgeMatchResponse(
                doc_id=match.doc_id,
                path=match.path,
                title=match.title,
                topic=match.topic,
                chunk_id=match.chunk_id,
                text=match.text,
                source_label=match.source_label,
                source_url=match.source_url,
                source_updated_at=match.source_updated_at,
                source_mtime=match.source_mtime,
                source_type=match.source_type,
                project_id=match.project_id,
                citation=match.citation,
                line_start=match.line_start,
                line_end=match.line_end,
                score=match.score,
                semantic_score=match.semantic_score,
                lexical_score=match.lexical_score,
                exact_score=match.exact_score,
                match_reasons=list(match.match_reasons),
            )
            for match in matches
        ],
    )


@router.get("/knowledge/workspace", response_model=KnowledgeWorkspaceResponse)
def get_knowledge_workspace(request: Request) -> KnowledgeWorkspaceResponse:
    payload = _workspace_service(request).get_workspace_status()
    return KnowledgeWorkspaceResponse(**payload)


@router.post("/knowledge/workspace/sync", response_model=KnowledgeWorkspaceSyncResponse)
def sync_knowledge_workspace(request: Request) -> KnowledgeWorkspaceSyncResponse:
    payload = _workspace_service(request).sync_inbox()
    return KnowledgeWorkspaceSyncResponse(**payload)
