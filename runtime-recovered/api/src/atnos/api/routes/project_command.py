from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from atnos.services.project_command_service import (
    ProjectCommandError,
    ProjectCommandNotFoundError,
    ProjectCommandService,
)


router = APIRouter(prefix="/api/project-command", tags=["project-command"])


class ProjectCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=8_000)
    conversation_id: int | None = Field(default=None, ge=1)
    auto_delegate: bool = False
    privacy_mode: Literal["strict_local", "allow_browser_ai", "allow_cloud_api"] = "strict_local"


@router.get("/{project_id}/overview")
def get_project_command_overview(project_id: int, request: Request, knowledge_query: str | None = None) -> dict[str, Any]:
    database = request.app.state.database
    with database.session_scope() as session:
        try:
            return ProjectCommandService(session, request.app.state.settings).overview(
                project_id,
                knowledge_query=knowledge_query,
            )
        except ProjectCommandNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc


@router.post("/{project_id}/execute")
def execute_project_command(project_id: int, payload: ProjectCommandRequest, request: Request) -> dict[str, Any]:
    database = request.app.state.database
    with database.session_scope() as session:
        try:
            return ProjectCommandService(session, request.app.state.settings).execute(
                project_id,
                content=payload.content,
                conversation_id=payload.conversation_id,
                auto_delegate=payload.auto_delegate,
                privacy_mode=payload.privacy_mode,
            )
        except ProjectCommandNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
        except ProjectCommandError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc


@router.post("/{project_id}/tasks/{task_id}/resume")
def resume_project_command_task(project_id: int, task_id: int, request: Request) -> dict[str, Any]:
    """Wznawia tylko zadania posiadajace deterministyczna i bezpieczna procedure."""
    database = request.app.state.database
    with database.session_scope() as session:
        try:
            return ProjectCommandService(session, request.app.state.settings).resume_command_task(
                project_id,
                task_id=task_id,
            )
        except ProjectCommandNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
        except ProjectCommandError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc


@router.post("/{project_id}/apply-approved-code/{draft_id}")
def apply_approved_project_code_change(project_id: int, draft_id: int, request: Request) -> dict[str, Any]:
    """Stosuje zatwierdzony lokalny diff z obowiazkowym snapshotem i rollbackiem."""
    database = request.app.state.database
    with database.session_scope() as session:
        try:
            return ProjectCommandService(session, request.app.state.settings).apply_approved_code_change(
                project_id,
                draft_id=draft_id,
            )
        except ProjectCommandNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=exc.message) from exc
        except ProjectCommandError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc
