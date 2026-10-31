from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel

router = APIRouter(tags=["memory"])


@router.get("/memory/index-map")
def get_memory_index_map(request: Request, limit: int = Query(default=80, ge=1, le=120),
                         source_type: str | None = Query(default=None, min_length=1, max_length=64)) -> dict:
    """Presentation of the existing index; this GET never reindexes or invokes a model."""
    service = getattr(request.app.state, "semantic_knowledge_index", None)
    if service is None:
        from atnos.services.semantic_knowledge_index_service import SemanticKnowledgeIndexService

        service = SemanticKnowledgeIndexService(request.app.state.settings)
    return service.catalog_snapshot(limit=limit, source_type=source_type)


def _load_summary_cards_dependencies():
    from atnos.services.summary_cards_service import SummaryCardsNotFoundError, SummaryCardsService

    return SummaryCardsNotFoundError, SummaryCardsService


class ConversationSummaryCardResponse(BaseModel):
    conversation_id: int
    conversation_title: str
    conversation_summary: str | None = None
    has_conversation_summary: bool
    project_id: int | None = None


class ProjectSummaryCardResponse(BaseModel):
    project_id: int
    project_name: str
    project_summary: str | None = None
    has_project_summary: bool


class ObsidianMemoryCardResponse(BaseModel):
    vault_name: str
    linked_label: str | None = None
    project_linked: bool
    path_configured: bool
    export_enabled: bool
    ready_for_preview: bool
    ready_for_confirm: bool
    relative_path_hint: str
    helper_text: str


class SummaryCardsResponse(BaseModel):
    conversation: ConversationSummaryCardResponse
    project: ProjectSummaryCardResponse | None = None
    obsidian_memory: ObsidianMemoryCardResponse | None = None


@router.get("/memory/cards/{conversation_id}", response_model=SummaryCardsResponse)
def get_summary_cards(request: Request, conversation_id: int) -> SummaryCardsResponse:
    SummaryCardsNotFoundError, SummaryCardsService = _load_summary_cards_dependencies()
    database = request.app.state.database
    settings = request.app.state.settings

    try:
        with database.session_scope() as session:
            service = SummaryCardsService(session, settings)
            result = service.get_for_conversation(conversation_id)
    except SummaryCardsNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return SummaryCardsResponse(
        conversation=ConversationSummaryCardResponse(
            conversation_id=result.conversation.conversation_id,
            conversation_title=result.conversation.conversation_title,
            conversation_summary=result.conversation.conversation_summary,
            has_conversation_summary=result.conversation.has_conversation_summary,
            project_id=result.conversation.project_id,
        ),
        project=(
            ProjectSummaryCardResponse(
                project_id=result.project.project_id,
                project_name=result.project.project_name,
                project_summary=result.project.project_summary,
                has_project_summary=result.project.has_project_summary,
            )
            if result.project is not None
            else None
        ),
        obsidian_memory=(
            ObsidianMemoryCardResponse(
                vault_name=result.obsidian_memory.vault_name,
                linked_label=result.obsidian_memory.linked_label,
                project_linked=result.obsidian_memory.project_linked,
                path_configured=result.obsidian_memory.path_configured,
                export_enabled=result.obsidian_memory.export_enabled,
                ready_for_preview=result.obsidian_memory.ready_for_preview,
                ready_for_confirm=result.obsidian_memory.ready_for_confirm,
                relative_path_hint=result.obsidian_memory.relative_path_hint,
                helper_text=result.obsidian_memory.helper_text,
            )
            if result.obsidian_memory is not None
            else None
        ),
    )
