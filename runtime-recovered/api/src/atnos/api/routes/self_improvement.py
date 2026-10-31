from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator

router = APIRouter(tags=["self-improvement"])

SelfImprovementSeverityValue = Literal["P0", "P1", "P2"]
SelfImprovementDecisionValue = Literal["accepted", "follow_up", "rejected", "undecided"]
SelfImprovementStatusValue = Literal["open", "selected", "closed"]


def _load_self_improvement_enum_dependencies():
    from atnos.db.models.enums import (
        SelfImprovementDecision,
        SelfImprovementSeverity,
        SelfImprovementStatus,
    )

    return SelfImprovementDecision, SelfImprovementSeverity, SelfImprovementStatus


def _load_self_improvement_notes_dependencies():
    from atnos.services.self_improvement_notes_service import (
        SelfImprovementNotesNotFoundError,
        SelfImprovementNotesSelectionError,
        SelfImprovementNotesService,
    )

    return (
        SelfImprovementNotesNotFoundError,
        SelfImprovementNotesSelectionError,
        SelfImprovementNotesService,
    )


def _load_self_improvement_governance_service():
    from atnos.services.self_improvement_governance_service import SelfImprovementGovernanceService

    return SelfImprovementGovernanceService


def _load_self_coding_draft_dependencies():
    from atnos.services.self_coding_draft_service import (
        SelfCodingExecutionError,
        SelfCodingDraftNotFoundError,
        SelfCodingDraftService,
    )

    return SelfCodingExecutionError, SelfCodingDraftNotFoundError, SelfCodingDraftService


class SelfImprovementNoteCreateRequest(BaseModel):
    source: str = Field(max_length=80)
    view: str = Field(max_length=80)
    friction: str = Field(max_length=4000)
    severity: SelfImprovementSeverityValue
    proposed_fix: str | None = Field(default=None, max_length=4000)


class SelfImprovementNotePatchRequest(BaseModel):
    severity: SelfImprovementSeverityValue | None = None
    proposed_fix: str | None = Field(default=None, max_length=4000)
    chosen_next_patch: str | None = Field(default=None, max_length=4000)
    decision: SelfImprovementDecisionValue | None = None
    status: SelfImprovementStatusValue | None = None

    @field_validator("severity", "decision", "status", mode="before")
    @classmethod
    def _reject_null_enum_fields(cls, value):
        if value is None:
            raise ValueError("Explicit null is not allowed for enum fields.")
        return value


class SelfImprovementNoteResponse(BaseModel):
    id: int
    created_at: datetime
    updated_at: datetime
    source: str
    view: str
    friction: str
    severity: str
    proposed_fix: str | None
    chosen_next_patch: str | None
    decision: str
    status: str


class SelfCodingDraftPreviewRequest(BaseModel):
    note_id: int | None = Field(default=None, gt=0)


class SelfCodingDraftPreviewResponse(BaseModel):
    note_id: int
    mode: str
    patch_plan: str
    codex_prompt: str
    code_draft: str
    test_plan: str
    safety_notes: list[str]
    warnings: list[str]


class ScaffoldPreviewRequest(BaseModel):
    app_type: str = Field(min_length=1, max_length=80)
    language: str = Field(min_length=1, max_length=80)
    framework: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=4000)
    target_dir: str = Field(min_length=1, max_length=4000)
    project_id: int | None = Field(default=None, gt=0)


class EditExistingAppRequest(BaseModel):
    project_root: str = Field(min_length=1, max_length=4000)
    task_description: str = Field(min_length=1, max_length=4000)
    files_to_modify: list[str] | None = None
    project_id: int | None = Field(default=None, gt=0)


class CodeGenerationPreviewResponse(BaseModel):
    mode: str
    action_draft_id: int
    title: str
    summary: str
    metadata: dict[str, object]


class ExecuteCodeApplyResponse(BaseModel):
    status: str
    written_files: list[str]
    project_root: str


class RunProjectTestsRequest(BaseModel):
    project_root: str = Field(min_length=1, max_length=4000)
    timeout_s: int = Field(default=300, ge=1, le=900)


class RunProjectTestsResponse(BaseModel):
    runner: str
    command: list[str]
    cwd: str
    exit_code: int
    summary: str
    first_failures: list[str]
    stdout: str
    stderr: str


class SelfImprovementGovernanceResponse(BaseModel):
    final_reviewer: str
    self_apply_allowed: bool
    handoff_dir: str
    log_path: str
    rules: list[str]


class CodexHandoffPackageResponse(BaseModel):
    note_id: int
    handoff_path: str
    report_path: str
    status: str
    message: str


@router.get("/self-improvement/governance", response_model=SelfImprovementGovernanceResponse)
def get_self_improvement_governance(request: Request) -> SelfImprovementGovernanceResponse:
    SelfImprovementGovernanceService = _load_self_improvement_governance_service()
    database = request.app.state.database
    settings = request.app.state.settings
    with database.session_scope() as session:
        status_payload = SelfImprovementGovernanceService(session, settings=settings).status().to_dict()
    return SelfImprovementGovernanceResponse(**status_payload)


@router.post("/self-improvement/governance/seed", response_model=SelfImprovementNoteResponse)
def seed_self_improvement_governance(request: Request) -> SelfImprovementNoteResponse:
    SelfImprovementGovernanceService = _load_self_improvement_governance_service()
    database = request.app.state.database
    settings = request.app.state.settings
    with database.session_scope() as session:
        note = SelfImprovementGovernanceService(session, settings=settings).seed_supervised_program()
    return _build_response(note)


@router.post("/self-improvement/codex-handoff", response_model=CodexHandoffPackageResponse)
def create_self_improvement_codex_handoff(
    request: Request,
    payload: SelfCodingDraftPreviewRequest,
) -> CodexHandoffPackageResponse:
    SelfImprovementGovernanceService = _load_self_improvement_governance_service()
    _, SelfCodingDraftNotFoundError, _ = _load_self_coding_draft_dependencies()
    database = request.app.state.database
    settings = request.app.state.settings
    try:
        with database.session_scope() as session:
            package = SelfImprovementGovernanceService(session, settings=settings).write_codex_handoff(
                note_id=payload.note_id,
            )
    except SelfCodingDraftNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return CodexHandoffPackageResponse(**package.to_dict())


@router.get("/self-improvement/notes", response_model=list[SelfImprovementNoteResponse])
def list_self_improvement_notes(
    request: Request,
    status_filter: SelfImprovementStatusValue | None = Query(default=None, alias="status"),
) -> list[SelfImprovementNoteResponse]:
    _, _, SelfImprovementNotesService = _load_self_improvement_notes_dependencies()
    _, _, SelfImprovementStatus = _load_self_improvement_enum_dependencies()
    database = request.app.state.database

    with database.session_scope() as session:
        notes = SelfImprovementNotesService(session).list_notes(
            status=SelfImprovementStatus(status_filter) if status_filter is not None else None
        )

    return [_build_response(note) for note in notes]


@router.post(
    "/self-improvement/notes",
    response_model=SelfImprovementNoteResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_self_improvement_note(
    request: Request,
    payload: SelfImprovementNoteCreateRequest,
) -> SelfImprovementNoteResponse:
    _, _, SelfImprovementNotesService = _load_self_improvement_notes_dependencies()
    _, SelfImprovementSeverity, _ = _load_self_improvement_enum_dependencies()
    database = request.app.state.database

    try:
        with database.session_scope() as session:
            note = SelfImprovementNotesService(session).create_note(
                source=payload.source,
                view=payload.view,
                friction=payload.friction,
                severity=SelfImprovementSeverity(payload.severity),
                proposed_fix=payload.proposed_fix,
            )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return _build_response(note)


@router.patch("/self-improvement/notes/{note_id}", response_model=SelfImprovementNoteResponse)
def patch_self_improvement_note(
    note_id: int,
    request: Request,
    payload: SelfImprovementNotePatchRequest,
) -> SelfImprovementNoteResponse:
    (
        SelfImprovementNotesNotFoundError,
        SelfImprovementNotesSelectionError,
        SelfImprovementNotesService,
    ) = _load_self_improvement_notes_dependencies()
    (
        SelfImprovementDecision,
        SelfImprovementSeverity,
        SelfImprovementStatus,
    ) = _load_self_improvement_enum_dependencies()
    database = request.app.state.database
    updates = payload.model_dump(exclude_unset=True)
    if "severity" in updates:
        updates["severity"] = SelfImprovementSeverity(updates["severity"])
    if "decision" in updates:
        updates["decision"] = SelfImprovementDecision(updates["decision"])
    if "status" in updates:
        updates["status"] = SelfImprovementStatus(updates["status"])

    try:
        with database.session_scope() as session:
            note = SelfImprovementNotesService(session).update_triage(note_id, **updates)
    except SelfImprovementNotesNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except SelfImprovementNotesSelectionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return _build_response(note)


@router.post("/self-improvement/draft-preview", response_model=SelfCodingDraftPreviewResponse)
def preview_self_coding_draft(
    request: Request,
    payload: SelfCodingDraftPreviewRequest,
) -> SelfCodingDraftPreviewResponse:
    _, SelfCodingDraftNotFoundError, SelfCodingDraftService = _load_self_coding_draft_dependencies()
    database = request.app.state.database

    try:
        with database.session_scope() as session:
            preview = SelfCodingDraftService(session).build_preview(note_id=payload.note_id)
    except SelfCodingDraftNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return SelfCodingDraftPreviewResponse(
        note_id=preview.note_id,
        mode=preview.mode,
        patch_plan=preview.patch_plan,
        codex_prompt=preview.codex_prompt,
        code_draft=preview.code_draft,
        test_plan=preview.test_plan,
        safety_notes=list(preview.safety_notes),
        warnings=list(preview.warnings),
    )


@router.post("/self-improvement/codegen/scaffold-preview", response_model=CodeGenerationPreviewResponse)
def generate_application_scaffold_preview(
    request: Request,
    payload: ScaffoldPreviewRequest,
) -> CodeGenerationPreviewResponse:
    SelfCodingExecutionError, _, SelfCodingDraftService = _load_self_coding_draft_dependencies()
    database = request.app.state.database
    settings = request.app.state.settings
    try:
        with database.session_scope() as session:
            preview = SelfCodingDraftService(session, settings=settings).generate_application_scaffold(
                app_type=payload.app_type,
                language=payload.language,
                framework=payload.framework,
                description=payload.description,
                target_dir=payload.target_dir,
                project_id=payload.project_id,
            )
    except SelfCodingExecutionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return CodeGenerationPreviewResponse(
        mode=preview.mode,
        action_draft_id=preview.action_draft_id,
        title=preview.title,
        summary=preview.summary,
        metadata=preview.metadata,
    )


@router.post("/self-improvement/codegen/edit-preview", response_model=CodeGenerationPreviewResponse)
def edit_existing_app_preview(
    request: Request,
    payload: EditExistingAppRequest,
) -> CodeGenerationPreviewResponse:
    SelfCodingExecutionError, _, SelfCodingDraftService = _load_self_coding_draft_dependencies()
    database = request.app.state.database
    settings = request.app.state.settings
    providers = request.app.state.providers
    try:
        with database.session_scope() as session:
            preview = SelfCodingDraftService(
                session,
                settings=settings,
                provider_registry=providers,
            ).edit_existing_app(
                project_root=payload.project_root,
                task_description=payload.task_description,
                files_to_modify=payload.files_to_modify,
                project_id=payload.project_id,
            )
    except SelfCodingExecutionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return CodeGenerationPreviewResponse(
        mode=preview.mode,
        action_draft_id=preview.action_draft_id,
        title=preview.title,
        summary=preview.summary,
        metadata=preview.metadata,
    )


@router.post("/self-improvement/codegen/action-drafts/{draft_id}/execute", response_model=ExecuteCodeApplyResponse)
def execute_codegen_action_draft(draft_id: int, request: Request) -> ExecuteCodeApplyResponse:
    SelfCodingExecutionError, _, SelfCodingDraftService = _load_self_coding_draft_dependencies()
    database = request.app.state.database
    settings = request.app.state.settings
    providers = request.app.state.providers
    try:
        with database.session_scope() as session:
            result = SelfCodingDraftService(
                session,
                settings=settings,
                provider_registry=providers,
            ).execute_code_apply(draft_id=draft_id)
    except SelfCodingExecutionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ExecuteCodeApplyResponse(
        status=str(result.get("status") or "applied"),
        written_files=[str(item) for item in result.get("written_files", [])],
        project_root=str(result.get("project_root") or ""),
    )


@router.post("/self-improvement/codegen/run-tests", response_model=RunProjectTestsResponse)
def run_project_tests(
    request: Request,
    payload: RunProjectTestsRequest,
) -> RunProjectTestsResponse:
    SelfCodingExecutionError, _, SelfCodingDraftService = _load_self_coding_draft_dependencies()
    database = request.app.state.database
    settings = request.app.state.settings
    providers = request.app.state.providers
    try:
        with database.session_scope() as session:
            result = SelfCodingDraftService(
                session,
                settings=settings,
                provider_registry=providers,
            ).run_project_tests(project_root=payload.project_root, timeout_s=payload.timeout_s)
    except SelfCodingExecutionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return RunProjectTestsResponse(
        runner=result.runner,
        command=list(result.command),
        cwd=result.cwd,
        exit_code=result.exit_code,
        summary=result.summary,
        first_failures=list(result.first_failures),
        stdout=result.stdout,
        stderr=result.stderr,
    )


def _build_response(note) -> SelfImprovementNoteResponse:
    return SelfImprovementNoteResponse(
        id=note.id,
        created_at=note.created_at,
        updated_at=note.updated_at,
        source=note.source,
        view=note.view,
        friction=note.friction,
        severity=_enum_value(note.severity),
        proposed_fix=note.proposed_fix,
        chosen_next_patch=note.chosen_next_patch,
        decision=_enum_value(note.decision),
        status=_enum_value(note.status),
    )


def _enum_value(value) -> str:
    enum_value = getattr(value, "value", None)
    return enum_value if enum_value is not None else str(value)
