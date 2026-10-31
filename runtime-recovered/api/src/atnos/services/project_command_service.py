from __future__ import annotations

"""Evidence-first command layer for the Project Command Chat.

The service deliberately aggregates existing Atnos systems instead of keeping a
second project database.  Runtime checks, project readiness, task memory,
knowledge and delegated work remain the sources of truth.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
import asyncio
import re
import unicodedata
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.ai_workers.delegation_router import AiDelegationRouter, DelegatePlanInput
from atnos.ai_workers.redaction import redact_text
from atnos.core.config import Settings
from atnos.db.models.ai_worker_task import AiWorkerTask
from atnos.db.models.enums import (
    ExecutionTraceKind,
    ExecutionTraceStatus,
    MessageRole,
    MessageStatus,
    ProjectMemoryKind,
    ProjectMemoryPriority,
)
from atnos.db.models.enums import OperationalWorkItemPriority, OperationalWorkItemReviewState
from atnos.db.models.execution_trace import ExecutionTrace
from atnos.db.models.project import Project, ProjectMemory
from atnos.db.models.project_command_task import ProjectCommandTask
from atnos.db.models.rollback_point import RollbackPoint
from atnos.repositories.conversation_repository import ConversationRepository
from atnos.repositories.message_repository import MessageRepository
from atnos.repositories.project_repository import ProjectRepository
from atnos.services.conversation_service import ConversationService, ConversationTurnRequest
from atnos.services.knowledge_library_service import KnowledgeLibraryService
from atnos.services.obsidian_read_service import ObsidianReadService
from atnos.services.operational_memory_service import OperationalMemoryService
from atnos.services.project_app_links_service import ProjectAppLinksService
from atnos.services.project_readiness_service import ProjectReadinessService
from atnos.services.project_brain_service import ProjectBrainService
from atnos.services.project_execution_service import ProjectExecutionError, ProjectExecutionService
from atnos.services.project_verification_service import ProjectVerificationService, VerificationRecipe
from atnos.services.runtime_health_service import RuntimeHealthService
from atnos.services.youtube_channel_identity_service import YouTubeChannelIdentityService
from atnos.llm.base import ToolCall
from atnos.tools.builtin.file_inventory import FileInventoryTool
from atnos.tools.executor import ToolExecutor
from atnos.tools.registry import ToolRegistry


CommandIntent = Literal[
    "status",
    "blockers",
    "next_step",
    "health",
    "knowledge",
    "verification",
    "file_inventory",
    "delegate",
    "safe_change",
    "high_risk",
    "insufficient_data",
]


HIGH_RISK_COMMAND_TOKENS = (
    "opublikuj",
    "publish",
    "upload",
    "wyślij",
    "wyslij",
    "send email",
    "oauth",
    "2fa",
    "hasło",
    "haslo",
    "password",
    "kup",
    "sprzedaj",
    "trade",
    "place order",
    "delete",
    "usuń",
    "usun",
    "payment",
    "checkout",
    "karta",
)

@dataclass(frozen=True, slots=True)
class ProjectCommandError(Exception):
    """Raised for a project-command request that cannot be safely resolved."""

    message: str


class ProjectCommandNotFoundError(ProjectCommandError):
    pass


class ProjectCommandService:
    """Builds evidence-backed project answers and safe delegation plans."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.projects = ProjectRepository(session)

    def overview(self, project_id: int, *, knowledge_query: str | None = None) -> dict[str, Any]:
        project = self._require_project(project_id)
        runtime = RuntimeHealthService(self.settings).snapshot(session=self.session)
        channel_identity = YouTubeChannelIdentityService(self.session).snapshot(project.id)
        readiness = self._readiness_for(project, channel_identity=channel_identity)
        operational = OperationalMemoryService(self.session).build_snapshot(project_id=project.id)
        knowledge = self._knowledge_for(project, query=knowledge_query)
        delegation = self._delegations_for(project.id)
        memories = self._recent_memories(project.id)
        traces = self._recent_traces(project.id)
        rollback_points = self._recent_rollback_points(project.id)
        evidence = self._build_evidence(
            project=project,
            runtime=runtime,
            readiness=readiness,
            operational=operational,
            knowledge=knowledge,
            delegation=delegation,
            memories=memories,
            traces=traces,
            rollback_points=rollback_points,
            channel_identity=channel_identity,
        )
        blockers = self._build_blockers(
            runtime,
            readiness,
            operational,
            delegation,
            rollback_points,
            channel_identity=channel_identity,
        )
        next_step = self._next_step(blockers=blockers, operational=operational, readiness=readiness)
        brain = ProjectBrainService(self.session).reconcile(
            project=project,
            runtime=runtime,
            readiness=readiness,
            knowledge=knowledge,
            blockers=blockers,
            next_step=next_step,
            evidence=evidence,
            delegations=delegation,
            traces=traces,
        )
        return {
            "project": self._serialize_project(project),
            "status": self._project_status(runtime=runtime, blockers=blockers, evidence=evidence),
            "checked_at": _utc_now(),
            "runtime": runtime,
            "readiness": readiness,
            "channel_identity": channel_identity,
            "blockers": blockers,
            "next_step": next_step,
            "work_items": [self._serialize_work_item(view.item) for view in operational.work_items],
            "active_decisions": [self._serialize_decision(item) for item in operational.active_decisions],
            "delegations": delegation,
            "knowledge": knowledge,
            "memories": memories,
            "recent_evidence": evidence,
            "recent_traces": traces,
            "rollback_points": rollback_points,
            "brain": brain,
        }

    def execute(
        self,
        project_id: int,
        *,
        content: str,
        conversation_id: int | None = None,
        auto_delegate: bool = False,
        privacy_mode: str = "strict_local",
    ) -> dict[str, Any]:
        project = self._require_project(project_id)
        normalized_content = " ".join(content.split())
        if not normalized_content:
            raise ProjectCommandError("Polecenie projektu nie może być puste.")

        try:
            conversation_turn = ConversationService(
                self.session,
                settings=self.settings,
            ).process_user_turn(
                ConversationTurnRequest(
                    content=normalized_content,
                    conversation_id=conversation_id,
                    project_id=project.id,
                    message_metadata_json={
                        "kind": "project_command_request",
                        "project_id": project.id,
                    },
                )
            )
        except ValueError as exc:
            raise ProjectCommandError(str(exc)) from exc

        overview = self.overview(project.id, knowledge_query=normalized_content)
        intent = self._classify_intent(normalized_content)
        command_task = (
            self._create_command_task(
                project=project,
                conversation_id=conversation_turn.conversation.id,
                intent=intent,
                content=normalized_content,
            )
            if intent == "file_inventory"
            else None
        )
        result: dict[str, Any]
        if intent == "high_risk":
            result = self._high_risk_result(project, normalized_content, overview)
        elif intent == "file_inventory":
            result = self._file_inventory_result(
                project,
                normalized_content,
                overview,
                command_task=command_task,
            )
        elif intent == "verification":
            result = self._verification_result(project, normalized_content, overview)
        elif intent in {"delegate", "safe_change"}:
            result = self._delegate_result(
                project,
                normalized_content,
                overview,
                auto_delegate=auto_delegate,
                privacy_mode=privacy_mode,
                safe_change=intent == "safe_change",
            )
        elif intent in {"status", "blockers", "next_step", "health", "knowledge"}:
            result = self._deterministic_result(intent, normalized_content, overview)
        else:
            result = self._insufficient_data_result(normalized_content, overview)

        trace = self._record_command_trace(project, normalized_content, intent, result)
        trace_status = trace.status
        pending_answer_trace = conversation_turn.traces[-1]
        pending_answer_trace.status = trace_status
        pending_answer_trace.step_name = "project_command_answer"
        pending_answer_trace.provider_key = "deterministic_project_command"
        pending_answer_trace.input_summary = _shorten(normalized_content, 240)
        pending_answer_trace.output_summary = _shorten(
            str(result.get("title") or result.get("status") or "Project command"),
            240,
        )
        pending_answer_trace.error_message = (
            _shorten(
                str(result.get("error") or result.get("title") or "Project command failed."),
                320,
            )
            if trace_status == ExecutionTraceStatus.FAILED
            else None
        )
        pending_answer_trace.metadata_json = {
            "project_trace_id": trace.id,
            "intent": intent,
            "result_status": result.get("status"),
            "requires_confirmation": bool(result.get("requires_confirmation")),
        }
        result_verified = self._result_is_verified(result)
        result["verified"] = result_verified
        result_verification_status = self._result_verification_status(result)
        result["verification_status"] = result_verification_status
        assistant_message = MessageRepository(self.session).create(
            conversation_id=conversation_turn.conversation.id,
            role=MessageRole.ASSISTANT,
            status=(
                MessageStatus.FAILED
                if trace_status == ExecutionTraceStatus.FAILED
                else MessageStatus.COMPLETED
            ),
            content=str(result.get("answer") or result.get("title") or ""),
            parent_message_id=conversation_turn.user_message.id,
            metadata_json={
                "kind": "project_command_result",
                "project_id": project.id,
                "command_intent": intent,
                "command_status": result.get("status"),
                "trace_id": trace.id,
                "trace_status": trace_status.value,
                "evidence_count": len(result.get("evidence") or []),
                "requires_confirmation": bool(result.get("requires_confirmation")),
                "verified": result_verified,
                "verification_status": result_verification_status,
            },
        )
        ConversationRepository(self.session).touch_last_message(conversation_turn.conversation)
        self.session.flush()
        result["trace_id"] = trace.id
        result["trace_status"] = trace.status.value
        result["conversation_id"] = conversation_turn.conversation.id
        result["conversation_title"] = conversation_turn.conversation.title
        result["user_message_id"] = conversation_turn.user_message.id
        result["assistant_message_id"] = assistant_message.id
        if command_task is not None:
            result["task_id"] = command_task.id
            result["task_status"] = command_task.status
        return result

    def _file_inventory_result(
        self,
        project: Project,
        content: str,
        overview: dict[str, Any],
        *,
        command_task: ProjectCommandTask,
    ) -> dict[str, Any]:
        minimum_size_bytes = self._parse_file_size_threshold(content)
        scan_root = self.settings.app_root.resolve()
        plan = [
            {"step": "planning", "status": "completed"},
            {"step": "tool_execution", "status": "running"},
            {"step": "independent_validation", "status": "pending"},
            {"step": "memory_writeback", "status": "pending"},
        ]
        self._transition_command_task(
            command_task,
            status="running",
            phase="tool_execution",
            plan=plan,
            increment_attempt=True,
        )
        tool_trace = ExecutionTrace(
            project_id=project.id,
            trace_kind=ExecutionTraceKind.TOOL,
            status=ExecutionTraceStatus.RUNNING,
            step_name="file_inventory_report",
            provider_key="local_tool_registry",
            input_summary=f"root={scan_root}; minimum_size_bytes={minimum_size_bytes}",
            metadata_json={"plan": plan, "tool_name": "file_inventory_report"},
        )
        self.session.add(tool_trace)
        self.session.flush()

        registry = ToolRegistry()
        registry.register_tool(FileInventoryTool(self.settings))
        result = asyncio.run(
            ToolExecutor(registry).execute_tool_call(
                ToolCall(
                    name="file_inventory_report",
                    arguments={
                        "root": str(scan_root),
                        "minimum_size_bytes": minimum_size_bytes,
                    },
                )
            )
        )
        if not result.success or not isinstance(result.data, dict):
            tool_trace.status = ExecutionTraceStatus.FAILED
            tool_trace.error_message = _shorten(result.error or "File inventory tool failed.", 420)
            tool_trace.output_summary = "File inventory did not produce a verified artifact."
            plan[1]["status"] = "failed"
            failed_result = {
                "status": "failed",
                "intent": "file_inventory",
                "title": "Raport plikow nie przeszedl wykonania",
                "answer": self._format_answer(
                    state={"project": project.name},
                    evidence=overview["recent_evidence"],
                    limitations=[result.error or "Narzędzie nie zwrocilo danych."],
                    action="Nie zapisano pamieci projektu i nie uznano zadania za ukonczone.",
                    next_step={
                        "source": "file_inventory_report",
                        "action": "Usun przyczyne bledu i powtorz kontrolowany skan.",
                        "reason": "Wykonanie narzedzia nie powiodlo sie.",
                        "requires_confirmation": False,
                    },
                ),
                "data": {"plan": plan, "tool_trace_id": tool_trace.id},
                "evidence": overview["recent_evidence"],
                "requires_confirmation": False,
            }
            self._finish_command_task(command_task, failed_result, plan=plan, status="failed")
            return failed_result

        tool_data = result.data
        validation = tool_data.get("validation") if isinstance(tool_data.get("validation"), dict) else {}
        verified = validation.get("verified") is True
        tool_trace.status = ExecutionTraceStatus.COMPLETED if verified else ExecutionTraceStatus.FAILED
        tool_trace.duration_ms = round(result.latency_seconds * 1000)
        tool_trace.output_summary = _shorten(
            f"Found {tool_data.get('match_count', 0)} files; validation={verified}; "
            f"artifact={tool_data.get('artifact', {}).get('absolute_path')}",
            420,
        )
        tool_trace.metadata_json = {
            "tool_name": "file_inventory_report",
            "match_count": tool_data.get("match_count", 0),
            "artifact": tool_data.get("artifact"),
            "validation": validation,
            "plan": plan,
        }
        plan[1]["status"] = "completed" if verified else "failed"
        plan[2]["status"] = "completed" if verified else "failed"
        self._transition_command_task(
            command_task,
            status="validating",
            phase="independent_validation",
            plan=plan,
        )

        verification_trace = ExecutionTrace(
            project_id=project.id,
            trace_kind=ExecutionTraceKind.VERIFY,
            status=ExecutionTraceStatus.COMPLETED if verified else ExecutionTraceStatus.FAILED,
            step_name="file_inventory_independent_validation",
            provider_key="local_filesystem_second_pass",
            input_summary=f"Validate tool trace #{tool_trace.id}",
            output_summary=_shorten(
                f"verified={verified}; expected={validation.get('expected_count')}; "
                f"observed={validation.get('observed_count')}; "
                f"sha256={validation.get('report_sha256')}",
                420,
            ),
            metadata_json={"tool_trace_id": tool_trace.id, "validation": validation},
        )
        self.session.add(verification_trace)
        self.session.flush()
        if not verified:
            failed_result = {
                "status": "failed",
                "intent": "file_inventory",
                "title": "Raport plikow nie przeszedl walidacji",
                "answer": self._format_answer(
                    state={"project": project.name},
                    evidence=overview["recent_evidence"],
                    limitations=list(validation.get("errors") or ["Walidacja raportu nie powiodla sie."]),
                    action="Nie zapisano wyniku do pamieci projektu.",
                    next_step={
                        "source": "file_inventory_independent_validation",
                        "action": "Powtorz skan po usunieciu rozbieznosci.",
                        "reason": "Drugi odczyt systemu plikow nie potwierdzil wyniku.",
                        "requires_confirmation": False,
                    },
                ),
                "data": {
                    "plan": plan,
                    "tool": tool_data,
                    "tool_trace_id": tool_trace.id,
                    "verification_trace_id": verification_trace.id,
                },
                "evidence": overview["recent_evidence"],
                "requires_confirmation": False,
            }
            self._finish_command_task(command_task, failed_result, plan=plan, status="failed")
            return failed_result

        artifact = tool_data["artifact"]
        memory = ProjectMemory(
            project_id=project.id,
            memory_kind=ProjectMemoryKind.FACT,
            priority=ProjectMemoryPriority.MEDIUM,
            content=(
                f"Verified file inventory: {tool_data['match_count']} files at least "
                f"{minimum_size_bytes} bytes under {scan_root}. Report: "
                f"{artifact['absolute_path']}. SHA-256: {artifact['sha256']}."
            ),
            confidence_score=1.0,
        )
        self.session.add(memory)
        self.session.flush()
        memory_trace = ExecutionTrace(
            project_id=project.id,
            trace_kind=ExecutionTraceKind.MEMORY,
            status=ExecutionTraceStatus.COMPLETED,
            step_name="file_inventory_memory_writeback",
            provider_key="project_memory",
            input_summary=f"Verified report trace #{verification_trace.id}",
            output_summary=f"Saved project memory #{memory.id}.",
            metadata_json={
                "memory_id": memory.id,
                "verification_trace_id": verification_trace.id,
                "report_sha256": artifact["sha256"],
            },
        )
        self.session.add(memory_trace)
        self.session.flush()
        plan[3]["status"] = "completed"
        evidence = [
            *overview["recent_evidence"],
            {
                "key": f"artifact:file_inventory:{verification_trace.id}",
                "source": "file_inventory_report",
                "status": "verified",
                "verified": True,
                "checked_at": _utc_now(),
                "summary": (
                    f"{tool_data['match_count']} files; report {artifact['absolute_path']}; "
                    f"sha256 {artifact['sha256']}."
                ),
                "trace_id": verification_trace.id,
            },
        ]
        completed_result = {
            "status": "completed",
            "intent": "file_inventory",
            "title": "Raport duzych plikow zostal utworzony i zweryfikowany",
            "answer": self._format_answer(
                state={"project": project.name},
                evidence=evidence,
                limitations=self._limitations(overview),
                action=(
                    f"Przeskanowano {scan_root}, znaleziono {tool_data['match_count']} plikow "
                    f"i zapisano zweryfikowany raport Markdown: {artifact['absolute_path']} "
                    f"(SHA-256: {artifact['sha256']})."
                ),
                next_step={
                    "source": "file_inventory_report",
                    "action": "Otworz raport i zdecyduj, ktore duze pliki wymagaja archiwizacji lub usuniecia.",
                    "reason": "Raport przeszedl niezalezna walidacje i zostal zapisany w pamieci projektu.",
                    "requires_confirmation": False,
                },
            ),
            "data": {
                "plan": plan,
                "tool": tool_data,
                "tool_trace_id": tool_trace.id,
                "verification_trace_id": verification_trace.id,
                "memory_id": memory.id,
            },
            "evidence": evidence,
            "requires_confirmation": False,
        }
        self._finish_command_task(command_task, completed_result, plan=plan, status="completed")
        return completed_result

    def resume_command_task(self, project_id: int, *, task_id: int) -> dict[str, Any]:
        """Wznawia idempotentne lokalne zadanie przerwane przed walidacja wyniku."""
        project = self._require_project(project_id)
        task = self.session.get(ProjectCommandTask, task_id)
        if task is None or task.project_id != project.id:
            raise ProjectCommandNotFoundError(f"Nie znaleziono zadania projektu #{task_id}.")
        if task.intent != "file_inventory":
            raise ProjectCommandError("Ten typ zadania nie ma bezpiecznej procedury wznowienia.")
        if task.status == "completed":
            raise ProjectCommandError("Ukonczone zadanie nie wymaga wznowienia.")
        overview = self.overview(project.id, knowledge_query=task.redacted_command)
        result = self._file_inventory_result(
            project,
            task.redacted_command,
            overview,
            command_task=task,
        )
        result["task_id"] = task.id
        result["task_status"] = task.status
        result["resumed"] = True
        return result

    def _create_command_task(
        self,
        *,
        project: Project,
        conversation_id: int,
        intent: CommandIntent,
        content: str,
    ) -> ProjectCommandTask:
        plan: list[dict[str, object]] = [
            {"step": "planning", "status": "completed", "at": _utc_now()},
            {"step": "tool_execution", "status": "pending"},
            {"step": "independent_validation", "status": "pending"},
            {"step": "memory_writeback", "status": "pending"},
        ]
        task = ProjectCommandTask(
            project_id=project.id,
            conversation_id=conversation_id,
            intent=intent,
            status="planning",
            phase="planning",
            redacted_command=redact_text(content),
            plan_json=plan,
            evidence_json=[],
        )
        self.session.add(task)
        self.session.flush()
        # The task and user message must survive a worker/process interruption.
        self.session.commit()
        self.session.refresh(task)
        return task

    def _transition_command_task(
        self,
        task: ProjectCommandTask,
        *,
        status: str,
        phase: str,
        plan: list[dict[str, object]],
        increment_attempt: bool = False,
    ) -> None:
        task.status = status
        task.phase = phase
        task.plan_json = [dict(item) for item in plan]
        task.started_at = task.started_at or datetime.now(UTC)
        if increment_attempt:
            task.attempt_count += 1
        task.error_message = None
        self.session.add(task)
        self.session.commit()
        self.session.refresh(task)

    def _finish_command_task(
        self,
        task: ProjectCommandTask,
        result: dict[str, Any],
        *,
        plan: list[dict[str, object]],
        status: str,
    ) -> None:
        task.status = status
        task.phase = status
        task.plan_json = [dict(item) for item in plan]
        task.result_json = {
            "status": result.get("status"),
            "title": result.get("title"),
            "data": result.get("data"),
        }
        task.evidence_json = list(result.get("evidence") or [])
        task.error_message = (
            _shorten(str(result.get("error") or result.get("title") or "Task failed."), 420)
            if status == "failed"
            else None
        )
        task.completed_at = datetime.now(UTC)
        self.session.add(task)
        self.session.commit()
        self.session.refresh(task)

    def apply_approved_code_change(self, project_id: int, *, draft_id: int) -> dict[str, Any]:
        """Wykonuje zatwierdzony lokalny diff i zwraca dowod testu lub rollbacku."""
        project = self._require_project(project_id)
        if not self.settings.self_improvement_allow_self_apply:
            reviewer = self.settings.self_improvement_final_reviewer
            raise ProjectCommandError(
                f"Bezpieczne wykonanie zmian jest wylaczone. Wymagane jest wlaczenie trusted_project_auto lub review: {reviewer}."
            )
        from atnos.services.account_action_broker_service import AccountActionBrokerService

        draft = AccountActionBrokerService(self.session, self.settings).get_action_draft(draft_id)
        if draft is None:
            raise ProjectCommandError(f"Nie znaleziono draftu zmiany #{draft_id}.")
        if draft.project_id != project.id:
            raise ProjectCommandError("Draft zmiany nie nalezy do aktywnego projektu.")
        try:
            execution = ProjectExecutionService(self.session, self.settings).execute_approved_code_apply(draft_id=draft_id)
        except ProjectExecutionError as exc:
            raise ProjectCommandError(str(exc)) from exc

        overview = self.overview(project.id)
        status = "completed" if execution.status == "verified" else execution.status
        limitations = self._limitations(overview)
        if execution.status != "verified":
            limitations.append("Zmiana nie zostala oznaczona jako zweryfikowana; wynik kontrolowanej weryfikacji jest w dowodach.")
        return {
            "status": status,
            "intent": "safe_change",
            "title": "Bezpieczna zmiana zostala zweryfikowana" if execution.status == "verified" else "Bezpieczna zmiana nie przeszla weryfikacji",
            "answer": self._format_answer(
                state={"execution": execution.to_dict()},
                evidence=overview["recent_evidence"],
                limitations=limitations,
                action=(
                    f"Zastosowano zatwierdzony draft #{draft_id}; punkt cofniecia #{execution.rollback_point_id}; "
                    f"status weryfikacji: {execution.verification_status}."
                ),
                next_step={
                    "source": "project_execution_service",
                    "action": execution.next_step,
                    "reason": execution.status,
                    "requires_confirmation": False,
                },
            ),
            "data": {"execution": execution.to_dict(), "overview": overview},
            "evidence": overview["recent_evidence"],
            "requires_confirmation": False,
        }

    def _require_project(self, project_id: int) -> Project:
        project = self.projects.get(project_id)
        if project is None:
            raise ProjectCommandNotFoundError(f"Nie znaleziono projektu o id '{project_id}'.")
        return project

    def _readiness_for(self, project: Project, *, channel_identity: dict[str, Any]) -> dict[str, Any] | None:
        try:
            payload = ProjectReadinessService(self.settings, session=self.session).build()
        except Exception as exc:  # Defensive: a status view must not conceal its own read failure.
            return {
                "status": "unknown",
                "project_id": None,
                "error": f"{type(exc).__name__}: {exc}",
                "evidence": {"source": "project_readiness_service", "verified": False},
            }

        candidates = payload.get("projects") if isinstance(payload, dict) else []
        if not isinstance(candidates, list):
            candidates = []
        target = _normalize(project.name)
        persisted_binding = str(
            ((channel_identity.get("identity") or {}).get("account_binding_key"))
            if isinstance(channel_identity.get("identity"), dict)
            else ""
        ).strip()
        binding_source = "persisted_youtube_channel_identity"
        if not persisted_binding:
            persisted_binding = self._project_youtube_binding(project.id)
            binding_source = "persisted_project_platform_binding"
        if persisted_binding:
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                if str(candidate.get("project_id") or "") != persisted_binding:
                    continue
                return {
                    **candidate,
                    "generated_at": payload.get("generated_at"),
                    "source": "project_readiness_service",
                    "binding": binding_source,
                }
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            if _normalize(str(candidate.get("name") or "")) == target:
                return {
                    **candidate,
                    "generated_at": payload.get("generated_at"),
                    "source": "project_readiness_service",
                }

        return {
            "status": "unknown",
            "project_id": None,
            "source": "project_readiness_service",
            "generated_at": payload.get("generated_at"),
            "evidence": {"verified": False, "reason": "No canonical readiness profile matches this project."},
        }

    def _project_youtube_binding(self, project_id: int) -> str:
        """Resolve a readiness account only from persisted project bindings."""
        links = ProjectAppLinksService(self.session, self.settings).list_links(project_id) or []
        for link in links:
            if link.integration_key != "youtube" or link.status == "disabled":
                continue
            account = link.account_summary if isinstance(link.account_summary, dict) else {}
            candidates = (link.resource_external_id, account.get("account_identifier"))
            for candidate in candidates:
                value = str(candidate or "").strip()
                if value.casefold().startswith("youtube:"):
                    return value
        return ""

    def _knowledge_for(self, project: Project, *, query: str | None) -> dict[str, Any]:
        search_query = " ".join(part for part in (query, project.name, project.description or "") if part).strip()
        library = KnowledgeLibraryService(self.settings)
        semantic_items: list[dict[str, Any]] = []
        semantic_error: str | None = None
        semantic_status = "not_run_in_test" if self.settings.app_env == "test" else "ready"
        if self.settings.app_env != "test":
            try:
                from atnos.services.semantic_knowledge_index_service import SemanticKnowledgeIndexService

                semantic_matches = SemanticKnowledgeIndexService(
                    self.settings,
                    library=library,
                ).lookup(search_query, max_results=5)
                semantic_items = [
                    {
                        "source": "semantic_knowledge_index",
                        "title": item.title,
                        "path": item.path,
                        "topic": item.topic,
                        "score": item.score,
                        "semantic_score": item.semantic_score,
                        "lexical_score": item.lexical_score,
                        "excerpt": item.text,
                        "source_label": item.source_label,
                        "source_url": item.source_url,
                        "source_updated_at": item.source_updated_at,
                        "source_type": getattr(item, "source_type", "unknown"),
                        "project_id": getattr(item, "project_id", None),
                        "claim_type": self._knowledge_claim_type(
                            getattr(item, "source_type", "unknown")
                        ),
                        "citation": item.citation,
                        "line_start": item.line_start,
                        "line_end": item.line_end,
                        "match_reasons": list(item.match_reasons),
                        "verified": False,
                    }
                    for item in semantic_matches
                ]
            except Exception as exc:
                semantic_status = "blocked_technical"
                semantic_error = f"{type(exc).__name__}: {exc}"
        try:
            matches = library.lookup(search_query, max_docs=5, max_chars=1_800)
            library_items = [
                {
                    "source": "knowledge_library",
                    "source_type": "knowledge_library",
                    "project_id": str(project.id),
                    "claim_type": "stored_fact",
                    "title": item.title,
                    "path": item.path,
                    "topic": item.topic,
                    "score": item.score,
                    "excerpt": item.excerpt,
                    "source_label": item.source_label,
                    "source_url": item.source_url,
                    "source_updated_at": item.source_updated_at,
                    "indexed_at": item.indexed_at,
                    "citation": item.citation,
                    "excerpt_line_start": item.excerpt_line_start,
                    "excerpt_line_end": item.excerpt_line_end,
                    "verified": False,
                }
                for item in matches
            ]
        except Exception as exc:
            library_items = []
            library_error = f"{type(exc).__name__}: {exc}"
        else:
            library_error = None

        obsidian = ObsidianReadService(self.session, self.settings)
        try:
            vault = obsidian.get_vault_status()
            notes = (
                obsidian.list_notes(search=(query or project.name), limit=5)
                if vault.readable
                else ()
            )
            obsidian_payload: dict[str, Any] = {
                "source": "obsidian",
                "configured": vault.path_configured,
                "readable": vault.readable,
                "note_count": vault.note_count,
                "warnings": list(vault.warnings),
                "matches": [
                    {
                        "title": item.title,
                        "path": item.relative_path,
                        "modified_at": item.modified_at,
                        "excerpt": item.excerpt,
                        "tags": list(item.tags),
                        "source_type": "obsidian_note",
                        "project_id": str(project.id),
                        "claim_type": "stored_fact",
                        "verified": False,
                    }
                    for item in notes
                ],
            }
        except Exception as exc:
            obsidian_payload = {
                "source": "obsidian",
                "configured": False,
                "readable": False,
                "note_count": 0,
                "warnings": [f"{type(exc).__name__}: {exc}"],
                "matches": [],
            }

        return {
            "query": search_query,
            "retrieval_mode": "semantic_hybrid" if semantic_items else "lexical_fallback",
            "semantic_status": semantic_status,
            "semantic_matches": semantic_items,
            "semantic_error": semantic_error,
            "library_matches": library_items,
            "library_error": library_error,
            "obsidian": obsidian_payload,
            "claim_legend": {
                "current_source_fact": "Aktualny odczyt operacyjny; nadal wymaga jawnego pola verified.",
                "stored_fact": "Wcześniej zapisana wiedza lub dokument projektu; nie jest bieżącym health-checkiem.",
                "inference": "Wniosek wyprowadzony z danych, a nie bezpośredni fakt źródłowy.",
                "recommendation": "Rekomendowane działanie, a nie opis stanu faktycznego.",
                "missing_data": "Brak źródła wystarczającego do udzielenia odpowiedzi.",
            },
        }

    @staticmethod
    def _knowledge_claim_type(source_type: str) -> str:
        normalized = str(source_type or "").strip().casefold()
        if normalized in {"runtime_report", "runtime_log", "test_result", "worker_result"}:
            return "current_source_fact"
        if normalized in {
            "project_documentation",
            "project_note",
            "obsidian_note",
            "knowledge_workspace",
            "learning_result",
        }:
            return "stored_fact"
        return "missing_data"

    def _delegations_for(self, project_id: int) -> list[dict[str, Any]]:
        tasks = (
            self.session.scalars(
                select(AiWorkerTask)
                .order_by(AiWorkerTask.created_at.desc(), AiWorkerTask.id.desc())
                .limit(100)
            ).all()
        )
        results: list[dict[str, Any]] = []
        for task in tasks:
            payload = task.payload_json if isinstance(task.payload_json, dict) else {}
            context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
            try:
                task_project_id = int(context.get("project_id"))
            except (TypeError, ValueError):
                continue
            if task_project_id != project_id:
                continue
            results.append(
                {
                    "id": task.id,
                    "task_type": task.task_type,
                    "status": task.status,
                    "worker_key": task.worker_key,
                    "verification_status": task.verification_status,
                    "result_summary": task.result_summary,
                    "error_message": task.error_message,
                    "blocked_reason": task.blocked_reason,
                    "created_at": task.created_at.isoformat() if task.created_at else None,
                    "updated_at": task.updated_at.isoformat() if task.updated_at else None,
                }
            )
            if len(results) == 12:
                break
        return results

    def _recent_memories(self, project_id: int) -> list[dict[str, Any]]:
        items = self.session.scalars(
            select(ProjectMemory)
            .where(ProjectMemory.project_id == project_id, ProjectMemory.is_archived.is_(False))
            .order_by(ProjectMemory.is_pinned.desc(), ProjectMemory.updated_at.desc(), ProjectMemory.id.desc())
            .limit(8)
        ).all()
        return [
            {
                "id": item.id,
                "kind": item.memory_kind.value,
                "priority": item.priority.value,
                "content": _shorten(item.content, 420),
                "confidence_score": item.confidence_score,
                "is_pinned": item.is_pinned,
                "updated_at": item.updated_at.isoformat() if item.updated_at else None,
            }
            for item in items
        ]

    def _recent_traces(self, project_id: int) -> list[dict[str, Any]]:
        from atnos.db.models.execution_trace import ExecutionTrace

        items = self.session.scalars(
            select(ExecutionTrace)
            .where(ExecutionTrace.project_id == project_id)
            .order_by(ExecutionTrace.updated_at.desc(), ExecutionTrace.id.desc())
            .limit(12)
        ).all()
        return [
            {
                "id": item.id,
                "kind": item.trace_kind.value,
                "status": item.status.value,
                "step_name": item.step_name,
                "output_summary": _shorten(item.output_summary or "", 420),
                "error_message": _shorten(item.error_message or "", 320) or None,
                "updated_at": item.updated_at.isoformat() if item.updated_at else None,
            }
            for item in items
        ]

    def _recent_rollback_points(self, project_id: int) -> list[dict[str, Any]]:
        items = self.session.scalars(
            select(RollbackPoint)
            .where(RollbackPoint.project_id == project_id)
            .order_by(RollbackPoint.updated_at.desc(), RollbackPoint.id.desc())
            .limit(8)
        ).all()
        return [
            {
                "id": item.id,
                "label": item.label,
                "status": item.status,
                "target_root": item.target_root,
                "manifest_path": item.manifest_path,
                "failure_detail": _shorten(item.failure_detail or "", 320) or None,
                "updated_at": item.updated_at.isoformat() if item.updated_at else None,
            }
            for item in items
        ]

    def _build_evidence(
        self,
        *,
        project: Project,
        runtime: dict[str, Any],
        readiness: dict[str, Any] | None,
        operational: Any,
        knowledge: dict[str, Any],
        delegation: list[dict[str, Any]],
        memories: list[dict[str, Any]],
        traces: list[dict[str, Any]],
        rollback_points: list[dict[str, Any]],
        channel_identity: dict[str, Any],
    ) -> list[dict[str, Any]]:
        items = [
            {
                "key": "runtime_live_check",
                "source": "runtime_health_service",
                "status": runtime.get("status", "unknown"),
                "verified": True,
                "checked_at": _utc_now(),
                "summary": "Live runtime health snapshot executed for this command.",
            },
            {
                "key": "project_readiness",
                "source": "project_readiness_service",
                "status": str((readiness or {}).get("status") or "unknown"),
                "verified": bool(readiness and readiness.get("project_id")),
                "checked_at": (readiness or {}).get("generated_at"),
                "summary": _shorten(str((readiness or {}).get("operator_decision", {}).get("state") or "No matching readiness profile."), 240),
            },
            {
                "key": "operational_memory",
                "source": "operational_memory_service",
                "status": "available",
                "verified": True,
                "checked_at": _utc_now(),
                "summary": f"{len(operational.work_items)} tracked work items and {len(operational.active_decisions)} active decisions.",
            },
            {
                "key": "project_knowledge",
                "source": "semantic_index+knowledge_library+obsidian",
                "status": (
                    "available"
                    if knowledge["semantic_matches"] or knowledge["library_matches"] or knowledge["obsidian"]["readable"]
                    else "insufficient"
                ),
                "verified": False,
                "checked_at": _utc_now(),
                "summary": (
                    f"retrieval={knowledge['retrieval_mode']}; semantic={len(knowledge['semantic_matches'])}; "
                    f"lexical={len(knowledge['library_matches'])}; Obsidian readable={knowledge['obsidian']['readable']}."
                ),
            },
        ]
        if delegation:
            items.append(
                {
                    "key": "delegated_work",
                    "source": "ai_worker_tasks",
                    "status": delegation[0]["status"],
                    "verified": delegation[0]["verification_status"] == "completed",
                    "checked_at": delegation[0]["updated_at"],
                    "summary": f"Latest delegation #{delegation[0]['id']} is {delegation[0]['status']}.",
                }
            )
        if memories:
            items.append(
                {
                    "key": "project_memory",
                    "source": "project_memories",
                    "status": "available",
                    "verified": False,
                    "checked_at": memories[0]["updated_at"],
                    "summary": f"{len(memories)} recent project memory entries available.",
                }
            )
        if traces:
            items.append(
                {
                    "key": "execution_history",
                    "source": "execution_traces",
                    "status": traces[0]["status"],
                    "verified": traces[0]["status"] == "completed",
                    "checked_at": traces[0]["updated_at"],
                    "summary": f"Latest tracked action: {traces[0]['step_name']}.",
                }
            )
        if rollback_points:
            latest = rollback_points[0]
            items.append(
                {
                    "key": "rollback_point:latest",
                    "source": "project_execution_service",
                    "status": latest["status"],
                    "verified": latest["status"] == "verified",
                    "checked_at": latest["updated_at"],
                    "summary": f"Latest rollback point #{latest['id']} status={latest['status']}.",
                }
            )
        identity = channel_identity.get("identity") if isinstance(channel_identity.get("identity"), dict) else None
        eligibility = channel_identity.get("publish_eligibility") if isinstance(channel_identity.get("publish_eligibility"), dict) else {}
        if identity is not None:
            items.append(
                {
                    "key": "youtube_channel_identity",
                    "source": "youtube_channel_identity_service",
                    "status": str(channel_identity.get("status") or "unknown"),
                    "verified": bool(identity.get("owner_confirmed")),
                    "checked_at": identity.get("last_verified_at") or identity.get("owner_confirmed_at"),
                    "summary": (
                        f"Channel ID={identity.get('youtube_channel_id') or 'missing'}; "
                        f"manifest={((channel_identity.get('manifest') or {}).get('status') or 'not_started')}; "
                        f"policy={((channel_identity.get('publishing_policy') or {}).get('status') or 'not_started')}; "
                        f"local_eligibility={eligibility.get('status') or 'unknown'}."
                    ),
                }
            )
        return items

    def _build_blockers(
        self,
        runtime: dict[str, Any],
        readiness: dict[str, Any] | None,
        operational: Any,
        delegation: list[dict[str, Any]],
        rollback_points: list[dict[str, Any]],
        *,
        channel_identity: dict[str, Any],
    ) -> list[dict[str, Any]]:
        blockers: list[dict[str, Any]] = []
        identity = channel_identity.get("identity")
        readiness_id = str((readiness or {}).get("project_id") or "")
        is_managed_youtube_project = isinstance(identity, dict) or readiness_id.startswith("youtube:")

        # A content project cannot produce or upload safely until its target
        # channel is known.  Keep these blockers ahead of runtime concerns such
        # as ComfyUI so the operator is never directed to render for an unknown
        # or disconnected YouTube account.
        if is_managed_youtube_project:
            blockers.extend(self._youtube_channel_blockers(channel_identity))

        for component in runtime.get("components", []):
            if not isinstance(component, dict) or component.get("status") not in {"blocked", "missing"}:
                continue
            blockers.append(
                {
                    "source": "runtime_health_service",
                    "key": component.get("key"),
                    "title": component.get("label") or component.get("key"),
                    "detail": component.get("detail"),
                    "next_action": component.get("next_action"),
                    "verified": True,
                }
            )
        for blocker in (readiness or {}).get("blockers") or []:
            blockers.append(
                {
                    "source": "project_readiness_service",
                    "key": str(blocker),
                    "title": str(blocker),
                    "detail": "Reported by the canonical project readiness check.",
                    "next_action": (readiness or {}).get("next_action"),
                    "verified": True,
                }
            )
        for view in operational.work_items:
            item = view.item
            if item.status.value != "blocked":
                continue
            blockers.append(
                {
                    "source": "operational_work_items",
                    "key": f"work_item:{item.id}",
                    "title": item.title,
                    "detail": item.blocked_reason or item.summary,
                    "next_action": item.next_step,
                    "verified": True,
                }
            )
        for task in delegation:
            if task["status"] not in {"blocked", "failed", "pending_external"}:
                continue
            blockers.append(
                {
                    "source": "ai_worker_tasks",
                    "key": f"delegation:{task['id']}",
                    "title": f"Delegation #{task['id']} ({task['worker_key'] or 'unassigned'})",
                    "detail": task["blocked_reason"] or task["error_message"] or task["result_summary"] or "Worker result requires review.",
                    "next_action": "Review the delegated task and run only the next safe step.",
                    "verified": task["status"] in {"blocked", "failed"},
                }
            )
        for rollback in rollback_points:
            if rollback["status"] not in {"rolled_back", "failed"}:
                continue
            blockers.append(
                {
                    "source": "project_execution_service",
                    "key": f"rollback:{rollback['id']}",
                    "title": f"Code change #{rollback['id']} was rolled back",
                    "detail": rollback["failure_detail"] or "The controlled verification did not pass.",
                    "next_action": "Review the verification evidence, then prepare the smallest corrected patch.",
                    "verified": True,
                }
            )
        return blockers[:16]

    @staticmethod
    def _youtube_channel_blockers(channel_identity: dict[str, Any]) -> list[dict[str, Any]]:
        """Return ordered channel setup blockers before production blockers.

        ``publish_eligibility`` deliberately covers only the persisted local
        policy.  OAuth readiness is kept as a separate explicit blocker because
        a policy approval must never be presented as evidence of a usable token.
        """

        blockers: list[dict[str, Any]] = []
        identity = channel_identity.get("identity")
        oauth = channel_identity.get("oauth") if isinstance(channel_identity.get("oauth"), dict) else {}
        eligibility = (
            channel_identity.get("publish_eligibility")
            if isinstance(channel_identity.get("publish_eligibility"), dict)
            else {}
        )

        if isinstance(identity, dict) and not oauth.get("ready"):
            oauth_status = str(oauth.get("status") or "not_linked")
            blockers.append(
                {
                    "source": "youtube_channel_identity_service",
                    "key": f"youtube_oauth_{oauth_status}",
                    "title": "YouTube OAuth nie jest aktywne dla tego projektu",
                    "detail": (
                        "Brak przypisanego aktywnego połączenia YouTube. "
                        "Nie wykonano live probe ani uploadu."
                    ),
                    "next_action": (
                        "W Konfiguracji kanału wygeneruj bezpieczny link YouTube OAuth, "
                        "dokończ zgodę w Google, a następnie wybierz utworzone połączenie."
                    ),
                    "verified": True,
                }
            )

        reasons = eligibility.get("reasons") if isinstance(eligibility.get("reasons"), list) else []
        priority = {
            "identity_missing": 0,
            "channel_id_missing": 1,
            "live_identity_mismatch": 2,
            "identity_unconfirmed": 3,
            "manifest_incomplete": 4,
            "manifest_unapproved": 5,
            "publishing_policy_incomplete": 6,
            "publishing_policy_unapproved": 7,
        }
        actions = {
            "identity_missing": (
                "Otwórz Konfigurację kanału, wygeneruj bezpieczny link YouTube OAuth, "
                "a po powrocie zapisz połączenie do tego projektu."
            ),
            "channel_id_missing": "Wykonaj read-only live probe i zapisz odczytany youtube_channel_id (UC...).",
            "live_identity_mismatch": "Połącz właściwe konto Google, wykonaj live probe ponownie i sprawdź zgodność UC... .",
            "identity_unconfirmed": "Wykonaj live probe, porównaj UC... i ręcznie potwierdź mapowanie kanału.",
            "manifest_incomplete": "Uzupełnij wszystkie pola Channel Identity Manifest.",
            "manifest_unapproved": "Przejrzyj i zatwierdź kompletny Channel Identity Manifest.",
            "publishing_policy_incomplete": "Uzupełnij wszystkie pola Channel Publishing Policy.",
            "publishing_policy_unapproved": "Przejrzyj i zatwierdź kompletną Channel Publishing Policy.",
        }
        normalized_reasons = [reason for reason in reasons if isinstance(reason, dict)]
        normalized_reasons.sort(key=lambda reason: priority.get(str(reason.get("code") or ""), 99))
        for reason in normalized_reasons:
            code = str(reason.get("code") or "channel_identity")
            blockers.append(
                {
                    "source": "youtube_channel_identity_service",
                    "key": code,
                    "title": "Kanał YouTube nie jest gotowy do routingu uploadu",
                    "detail": str(reason.get("detail") or "Brak warunku bezpieczeństwa kanału."),
                    "next_action": actions.get(
                        code,
                        "Uzupełnij i zatwierdź Channel Identity Manifest oraz Channel Publishing Policy.",
                    ),
                    "verified": True,
                }
            )
        return blockers

    def _next_step(self, *, blockers: list[dict[str, Any]], operational: Any, readiness: dict[str, Any] | None) -> dict[str, Any]:
        if blockers:
            primary = blockers[0]
            return {
                "source": primary["source"],
                "action": primary.get("next_action") or "Resolve the first verified blocker before starting new work.",
                "reason": primary["title"],
                "requires_confirmation": False,
            }
        for view in operational.work_items:
            item = view.item
            if item.status.value in {"active", "waiting"}:
                return {
                    "source": "operational_work_items",
                    "action": item.next_step or item.summary,
                    "reason": item.title,
                    "requires_confirmation": False,
                }
        action = (readiness or {}).get("next_action") or "Run a verified health check or add a project task."
        return {
            "source": "project_readiness_service",
            "action": action,
            "reason": "No active local blocker is recorded.",
            "requires_confirmation": False,
        }

    def _project_status(self, *, runtime: dict[str, Any], blockers: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> str:
        if blockers or runtime.get("status") == "blocked":
            return "blocked"
        if not any(item.get("verified") for item in evidence):
            return "insufficient_evidence"
        if runtime.get("status") == "ready":
            return "verified"
        return "partially_verified"

    def _deterministic_result(self, intent: CommandIntent, content: str, overview: dict[str, Any]) -> dict[str, Any]:
        title = {
            "status": "Aktualny stan projektu",
            "blockers": "Zweryfikowane blokery projektu",
            "next_step": "Następny zalecany krok",
            "health": "Stan runtime projektu",
            "knowledge": "Kontekst wiedzy projektu",
        }[intent]
        if (
            intent == "knowledge"
            and not overview["knowledge"]["semantic_matches"]
            and not overview["knowledge"]["library_matches"]
            and not overview["knowledge"]["obsidian"]["matches"]
        ):
            return self._insufficient_data_result(
                content,
                overview,
                message="Brak wystarczających danych do potwierdzenia. Nie znaleziono notatek ani dokumentów pasujących do zapytania.",
            )
        state = {
            "project_status": overview["status"],
            "runtime_status": overview["runtime"]["status"],
            "blockers": overview["blockers"],
            "next_step": overview["next_step"],
        }
        if intent == "health":
            state["components"] = overview["runtime"]["components"]
        if intent == "knowledge":
            state["knowledge"] = overview["knowledge"]
        return {
            "status": "completed",
            "intent": intent,
            "title": title,
            "answer": self._format_answer(
                state=state,
                evidence=overview["recent_evidence"],
                limitations=self._limitations(overview),
                action="Wykonano lokalne odczyty: health-check, gotowość projektu, pamięć operacyjna i indeks wiedzy.",
                next_step=overview["next_step"],
            ),
            "data": state,
            "evidence": overview["recent_evidence"],
            "requires_confirmation": False,
        }

    def _delegate_result(
        self,
        project: Project,
        content: str,
        overview: dict[str, Any],
        *,
        auto_delegate: bool,
        privacy_mode: str,
        safe_change: bool,
    ) -> dict[str, Any]:
        work_item = self._create_work_item_for_change(project, content) if safe_change else None
        task_type = "engineering_project_change" if safe_change else "project_operator_command"
        allowed_workers = ("ollama",) if privacy_mode == "strict_local" else ("ollama", "browser_claude", "browser_kimi", "browser_chatgpt")
        plan = AiDelegationRouter(self.session, self.settings).delegate_plan(
            DelegatePlanInput(
                task_type=task_type,
                prompt=content,
                context={
                    "project_id": project.id,
                    "project_name": project.name,
                    "project_description": project.description or "",
                    "acceptance_criteria": [
                        "State concrete evidence and changed files/artifacts.",
                        "Do not claim completion without a verification result.",
                        "Stay within local analysis and project-file scope; do not invoke external account actions.",
                    ],
                    "known_blockers": [item["title"] for item in overview["blockers"][:6]],
                },
                preferred_worker="ollama",
                allowed_workers=allowed_workers,
                privacy_mode=privacy_mode,
                priority="normal",
                requested_by="project_command_chat",
                requires_approval_for_result=True,
                max_steps=4,
            )
        )
        plan_status = plan.get("status")
        return {
            "status": "waiting_for_worker" if plan_status == "planned" else plan_status,
            "intent": "safe_change" if safe_change else "delegate",
            "title": "Bezpieczna zmiana projektu została zaplanowana" if safe_change else "Delegacja projektu została zaplanowana",
            "answer": self._format_answer(
                state={
                    "delegation": plan,
                    "work_item": self._serialize_work_item(work_item) if work_item is not None else None,
                    "auto_run_requested": auto_delegate,
                },
                evidence=overview["recent_evidence"],
                limitations=[
                    "Plan delegacji nie jest wynikiem pracy workera.",
                    "Każdy rezultat wymaga weryfikacji przed oznaczeniem zadania jako ukończone.",
                    *( ["Zmiana kodu nie została jeszcze zastosowana; plan czeka na bezpieczne wykonanie i testy."] if safe_change else [] ),
                ],
                action=(
                    f"Utworzono work item #{work_item.id} oraz plan delegacji #{plan.get('id')} z {len(plan.get('children') or [])} krokami."
                    if work_item is not None
                    else f"Utworzono plan delegacji #{plan.get('id')} z {len(plan.get('children') or [])} krokami."
                ),
                next_step={
                    "source": "ai_worker_tasks",
                    "action": "Uruchom wyłącznie wybrany bezpieczny krok lokalny albo przekaż zadanie do review.",
                    "reason": "Plan został zapisany, ale nie jest wykonany.",
                    "requires_confirmation": True,
                },
            ),
            "data": {
                "delegation": plan,
                "work_item": self._serialize_work_item(work_item) if work_item is not None else None,
                "auto_run_requested": auto_delegate,
            },
            "evidence": overview["recent_evidence"],
            "requires_confirmation": True,
        }

    def _verification_result(
        self,
        project: Project,
        content: str,
        overview: dict[str, Any],
    ) -> dict[str, Any]:
        """Run one fixed local verification recipe and save its technical proof."""
        recipe = self._verification_recipe_for(content)
        outcome = ProjectVerificationService(self.settings).run(recipe)
        status_map = {
            "verified": ExecutionTraceStatus.COMPLETED,
            "failed": ExecutionTraceStatus.FAILED,
            "blocked_technical": ExecutionTraceStatus.SKIPPED,
        }
        verification_trace = ExecutionTrace(
            project_id=project.id,
            trace_kind=ExecutionTraceKind.VERIFY,
            status=status_map[outcome.status],
            step_name=f"project_verification:{outcome.recipe}",
            provider_key="controlled_project_verification",
            duration_ms=outcome.duration_ms,
            input_summary=" ".join(outcome.command),
            output_summary=_shorten(outcome.summary, 420),
            error_message=outcome.output if outcome.status == "failed" else None,
            metadata_json={
                "recipe": outcome.recipe,
                "command": list(outcome.command),
                "cwd": outcome.cwd,
                "exit_code": outcome.exit_code,
                "output": outcome.output,
                "next_step": outcome.next_step,
            },
        )
        self.session.add(verification_trace)
        self.session.flush()
        outcome_data = outcome.to_dict()
        evidence = [
            *overview["recent_evidence"],
            {
                "key": f"verification:{outcome.recipe}",
                "source": "controlled_project_verification",
                "status": outcome.status,
                "verified": outcome.status == "verified",
                "checked_at": _utc_now(),
                "summary": outcome.summary,
                "trace_id": verification_trace.id,
            },
        ]
        result_status = "completed" if outcome.status == "verified" else outcome.status
        limitations = self._limitations(overview)
        if outcome.status != "verified":
            limitations.append("Weryfikacja nie przeszla, dlatego Atnos nie oznaczyl zadnej zmiany jako ukonczonej.")
        return {
            "status": result_status,
            "intent": "verification",
            "title": "Weryfikacja projektu zakonczona" if outcome.status == "verified" else "Weryfikacja projektu nie potwierdzila gotowosci",
            "answer": self._format_answer(
                state={"verification": outcome_data},
                evidence=evidence,
                limitations=limitations,
                action=(
                    f"Uruchomiono dozwolona recepte '{outcome.recipe}' w {outcome.cwd}; "
                    f"kod wyjscia: {outcome.exit_code if outcome.exit_code is not None else 'brak'}."
                ),
                next_step={
                    "source": "controlled_project_verification",
                    "action": outcome.next_step,
                    "reason": outcome.summary,
                    "requires_confirmation": False,
                },
            ),
            "data": {"verification": outcome_data, "verification_trace_id": verification_trace.id},
            "evidence": evidence,
            "requires_confirmation": False,
        }

    @staticmethod
    def _verification_recipe_for(content: str) -> VerificationRecipe:
        normalized = _normalize(content)
        if any(token in normalized for token in ("frontend build", "build frontend", "zbuduj frontend", "build ui")):
            return "frontend_build"
        if any(token in normalized for token in ("frontend test", "test ui", "test interfejs", "test czatu")):
            return "frontend_project_command_tests"
        return "backend_project_command_tests"

    def _create_work_item_for_change(self, project: Project, content: str):
        title = _shorten(f"Safe project change: {redact_text(content)}", 200)
        return OperationalMemoryService(self.session).create_work_item(
            project_id=project.id,
            title=title,
            summary=_shorten(redact_text(content), 4_000),
            next_step="Run the planned local diagnosis, apply the smallest safe patch, then verify tests and build.",
            priority=OperationalWorkItemPriority.P1,
            review_state=OperationalWorkItemReviewState.UNREVIEWED,
        )

    def _high_risk_result(self, project: Project, content: str, overview: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "waiting_for_user",
            "intent": "high_risk",
            "title": "Wymagane potwierdzenie działania zewnętrznego",
            "answer": self._format_answer(
                state={"requested_action": redact_text(content), "project_status": overview["status"]},
                evidence=overview["recent_evidence"],
                limitations=["Atnos nie wykonuje automatycznie publikacji, wysyłki, OAuth, operacji finansowych, zakupów ani operacji destrukcyjnych."],
                action="Przygotowano bezpieczny opis działania; nie wykonano żadnej operacji zewnętrznej.",
                next_step={
                    "source": "action_broker",
                    "action": "Utwórz zatwierdzalny draft w Action Broker i potwierdź go ręcznie.",
                    "reason": "Polecenie należy do poziomu autonomii D.",
                    "requires_confirmation": True,
                },
            ),
            "data": {"requested_action": redact_text(content)},
            "evidence": overview["recent_evidence"],
            "requires_confirmation": True,
        }

    def _insufficient_data_result(self, content: str, overview: dict[str, Any], *, message: str | None = None) -> dict[str, Any]:
        return {
            "status": "insufficient_data",
            "intent": "insufficient_data",
            "title": "Brak potwierdzonej odpowiedzi",
            "answer": self._format_answer(
                state={"query": redact_text(content), "project": overview["project"]["name"]},
                evidence=overview["recent_evidence"],
                limitations=[message or "Brak wystarczających danych do potwierdzenia."],
                action="Nie uruchomiono modelu językowego i nie utworzono niezweryfikowanego statusu.",
                next_step={
                    "source": "project_command",
                    "action": "Uruchom odpowiedni health-check, dodaj dokument do Knowledge Index albo zleć research workerowi.",
                    "reason": "Pytanie nie ma aktualnego źródła deterministycznego.",
                    "requires_confirmation": False,
                },
            ),
            "data": {"query": redact_text(content)},
            "evidence": overview["recent_evidence"],
            "requires_confirmation": False,
        }

    @staticmethod
    def _result_is_verified(result: dict[str, Any]) -> bool:
        if result.get("status") != "completed":
            return False
        evidence = result.get("evidence")
        return bool(
            isinstance(evidence, list)
            and any(
                isinstance(item, dict)
                and item.get("verified") is True
                and item.get("status") == "verified"
                for item in evidence
            )
        )

    @classmethod
    def _result_verification_status(cls, result: dict[str, Any]) -> str:
        if str(result.get("status") or "") in {"failed", "error"}:
            return "failed"
        if cls._result_is_verified(result):
            return "verified"
        evidence = result.get("evidence")
        if isinstance(evidence, list) and any(
            isinstance(item, dict) and item.get("verified") is True
            for item in evidence
        ):
            return "partially_verified"
        return "insufficient_evidence"

    def _record_command_trace(self, project: Project, content: str, intent: CommandIntent, result: dict[str, Any]) -> ExecutionTrace:
        result_status = str(result.get("status") or "")
        trace_status = {
            "completed": ExecutionTraceStatus.COMPLETED,
            "verified": ExecutionTraceStatus.COMPLETED,
            "failed": ExecutionTraceStatus.FAILED,
            "error": ExecutionTraceStatus.FAILED,
            "waiting_for_worker": ExecutionTraceStatus.PENDING,
            "waiting_for_user": ExecutionTraceStatus.PENDING,
            "blocked_technical": ExecutionTraceStatus.SKIPPED,
            "insufficient_data": ExecutionTraceStatus.SKIPPED,
        }.get(result_status, ExecutionTraceStatus.SKIPPED)
        trace = ExecutionTrace(
            project_id=project.id,
            trace_kind=ExecutionTraceKind.ANSWER,
            status=trace_status,
            step_name="project_command",
            provider_key="deterministic_project_command",
            input_summary=_shorten(redact_text(content), 240),
            output_summary=_shorten(str(result.get("title") or result.get("status") or "Project command"), 240),
            metadata_json={
                "intent": intent,
                "result_status": result.get("status"),
                "requires_confirmation": bool(result.get("requires_confirmation")),
                "evidence_count": len(result.get("evidence") or []),
            },
        )
        self.session.add(trace)
        self.session.flush()
        return trace

    @staticmethod
    def _classify_intent(content: str) -> CommandIntent:
        normalized = _normalize(content)
        if any(_normalize(token) in normalized for token in HIGH_RISK_COMMAND_TOKENS):
            return "high_risk"
        if (
            any(token in normalized.split() for token in ("plik", "pliki", "file", "files"))
            and any(token in normalized for token in ("wieksz", "large", "larger", "bigger", "rozmiar", "size"))
        ):
            return "file_inventory"
        if any(token in normalized for token in ("deleguj", "zlec", "delegate", "assign", "przekaz worker")):
            return "delegate"
        if any(
            token in normalized
            for token in (
                "uruchom test",
                "run test",
                "sprawdz test",
                "testy backend",
                "test backend",
                "frontend build",
                "build frontend",
                "zbuduj frontend",
                "test ui",
                "test interfejs",
                "test czatu",
            )
        ) and not any(token in normalized for token in ("napraw", "popraw", "fix", "implement", "wdroz")):
            return "verification"
        if any(
            token in normalized
            for token in (
                "napraw",
                "popraw",
                "fix",
                "implement",
                "wdroz",
                "build",
                "uruchom test",
                "run test",
                "przeglad kodu",
                "code review",
            )
        ):
            return "safe_change"
        if any(token in normalized for token in ("co blokuje", "blokuje", "blokery", "blocker", "zablok", "blocked")):
            return "blockers"
        if any(token in normalized for token in ("nastepny krok", "co dalej", "next step", "next action")):
            return "next_step"
        if any(token in normalized for token in ("health", "runtime", "backend", "frontend", "dziala", "stan uslug")):
            return "health"
        if any(token in normalized for token in ("obsidian", "notatk", "knowledge", "wiedz", "dokument")):
            return "knowledge"
        if any(token in normalized for token in ("status", "stan projektu", "raport", "dowody", "evidence")):
            return "status"
        return "insufficient_data"

    @staticmethod
    def _parse_file_size_threshold(content: str) -> int:
        match = re.search(
            r"(\d+(?:[.,]\d+)?)\s*(kib|kb|mib|mb|gib|gb|b)\b",
            content,
            flags=re.IGNORECASE,
        )
        if match is None:
            return 100 * 1024 * 1024
        value = float(match.group(1).replace(",", "."))
        unit = match.group(2).lower()
        multiplier = {
            "b": 1,
            "kb": 1000,
            "kib": 1024,
            "mb": 1024**2,
            "mib": 1024**2,
            "gb": 1024**3,
            "gib": 1024**3,
        }[unit]
        threshold = int(value * multiplier)
        if threshold < 1:
            raise ProjectCommandError("Minimalny rozmiar pliku musi byc wiekszy od zera.")
        return threshold

    @staticmethod
    def _serialize_project(project: Project) -> dict[str, Any]:
        return {
            "id": project.id,
            "name": project.name,
            "slug": project.slug,
            "description": project.description,
            "summary": project.summary,
            "status": project.status.value,
            "provider_posture": project.provider_posture.value if project.provider_posture else None,
            "persona_profile": project.persona_profile.value if project.persona_profile else None,
        }

    @staticmethod
    def _serialize_work_item(item: Any) -> dict[str, Any]:
        return {
            "id": item.id,
            "title": item.title,
            "summary": item.summary,
            "next_step": item.next_step,
            "status": item.status.value,
            "priority": item.priority.value,
            "review_state": item.review_state.value,
            "blocked_reason": item.blocked_reason,
            "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        }

    @staticmethod
    def _serialize_decision(item: Any) -> dict[str, Any]:
        return {
            "id": item.id,
            "kind": item.kind.value,
            "summary": item.summary,
            "status": item.status.value,
            "made_at": item.made_at.isoformat() if item.made_at else None,
            "due_at": item.due_at.isoformat() if item.due_at else None,
        }

    @staticmethod
    def _limitations(overview: dict[str, Any]) -> list[str]:
        limitations: list[str] = []
        if overview["runtime"].get("status") != "ready":
            limitations.append(f"Runtime ma status {overview['runtime'].get('status')}; szczegóły są w dowodach health-checku.")
        if not overview["knowledge"]["obsidian"]["readable"]:
            limitations.append("Obsidian nie jest aktualnie czytelny albo nie został skonfigurowany.")
        if overview["knowledge"].get("library_error"):
            limitations.append("Indeks wiedzy zgłosił błąd podczas odczytu.")
        channel_identity = overview.get("channel_identity", {})
        identity = channel_identity.get("identity") if isinstance(channel_identity, dict) else None
        readiness_id = str((overview.get("readiness") or {}).get("project_id") or "")
        eligibility = channel_identity.get("publish_eligibility", {}) if isinstance(channel_identity, dict) else {}
        if (isinstance(identity, dict) or readiness_id.startswith("youtube:")) and isinstance(eligibility, dict) and not eligibility.get("eligible_by_local_policy"):
            limitations.append("Kanał nie przeszedł jeszcze lokalnej walidacji tożsamości i polityki publikacji.")
        if not limitations:
            limitations.append("Stan opiera się na aktualnym odczycie lokalnym; zewnętrzne akcje nadal wymagają potwierdzenia.")
        return limitations

    @staticmethod
    def _format_answer(*, state: dict[str, Any], evidence: list[dict[str, Any]], limitations: list[str], action: str, next_step: dict[str, Any]) -> str:
        evidence_lines = [
            f"- {item['source']}: {item['status']} ({item['summary']})"
            for item in evidence[:5]
        ] or ["- Brak dodatkowych źródeł."]
        limitation_lines = [f"- {item}" for item in limitations] or ["- Brak."]
        state_text = ProjectCommandService._format_state(state)
        return "\n".join(
            [
                "### Stan",
                state_text,
                "",
                "### Dowody",
                *evidence_lines,
                "",
                "### Ograniczenia",
                *limitation_lines,
                "",
                "### Wykonane działanie",
                action,
                "",
                "### Następny krok",
                str(next_step.get("action") or "Brak rekomendacji."),
            ]
        )

    @staticmethod
    def _format_state(state: dict[str, Any]) -> str:
        """Renderuje stan komendy dla operatora, bez surowego repr słownika."""
        lines: list[str] = []
        if state.get("project"):
            lines.append(f"- Projekt: {state['project']}")
        if state.get("project_status"):
            lines.append(f"- Status projektu: {state['project_status']}")
        if state.get("runtime_status"):
            lines.append(f"- Status runtime: {state['runtime_status']}")

        blockers = state.get("blockers")
        if isinstance(blockers, list):
            if blockers:
                lines.append(f"- Potwierdzone blokery: {len(blockers)}")
                lines.extend(
                    f"  - {item.get('title') or item.get('detail') or 'Bloker bez opisu.'}"
                    for item in blockers[:4]
                    if isinstance(item, dict)
                )
            else:
                lines.append("- Potwierdzone blokery: brak")

        if state.get("query"):
            lines.append(f"- Zapytanie: {state['query']}")
        if state.get("requested_action"):
            lines.append(f"- Przygotowane działanie: {state['requested_action']}")
        if state.get("next_step") and isinstance(state["next_step"], dict):
            lines.append(f"- Zalecenie: {state['next_step'].get('action') or 'brak'}")
        if not lines:
            lines.append("- Brak znormalizowanego stanu do wyświetlenia.")
        return _shorten_multiline("\n".join(lines), 1_100)


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", normalized).strip()


def _shorten(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 3].rstrip() + "..."


def _shorten_multiline(value: str, limit: int) -> str:
    """Skraca tekst zachowując strukturę list i podział na wiersze dla interfejsu."""
    normalized = "\n".join(" ".join(line.split()) for line in value.splitlines() if line.strip())
    return normalized if len(normalized) <= limit else normalized[: limit - 3].rstrip() + "..."


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
