from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from atnos.db.models.attachment import Attachment
    from atnos.db.models.conversation import Conversation
    from atnos.db.models.execution_trace import ExecutionTrace
    from atnos.db.models.message import Message
    from atnos.services.memory_curation_service import MemoryCandidate
    from atnos.services.runtime_context_preferences import RuntimeContextPreferences


class SummaryWritebackService:
    MAX_USER_SNIPPET_CHARS = 180
    MAX_ASSISTANT_SNIPPET_CHARS = 220
    MAX_ATTACHMENT_LABEL_CHARS = 120
    MAX_CONVERSATION_SUMMARY_CHARS = 520
    MAX_PROJECT_SUMMARY_CHARS = 680
    RECENT_PROJECT_CONVERSATIONS = 3
    PROJECT_MEMORY_WRITEBACK_KEYWORDS = (
        "projekt",
        "project",
        "sprint",
        "roadmap",
        "pamiec",
        "memory",
        "kontekst",
        "summary",
        "podsum",
        "plan",
    )

    def __init__(self, session) -> None:
        from atnos.repositories.conversation_repository import ConversationRepository
        from atnos.repositories.execution_trace_repository import ExecutionTraceRepository
        from atnos.repositories.project_memory_repository import ProjectMemoryRepository
        from atnos.repositories.project_repository import ProjectRepository
        from atnos.services.memory_curation_service import MemoryCurationService

        self.session = session
        self.conversations = ConversationRepository(session)
        self.projects = ProjectRepository(session)
        self.project_memories = ProjectMemoryRepository(session)
        self.execution_traces = ExecutionTraceRepository(session)
        self.memory_curator = MemoryCurationService()

    def refresh_after_turn(
        self,
        *,
        conversation: Conversation,
        user_message: Message,
        assistant_message: Message,
        attachments: tuple[Attachment, ...] | list[Attachment],
        runtime_preferences: RuntimeContextPreferences,
        project_context_applied: bool,
    ) -> ExecutionTrace:
        from atnos.db.models.enums import ExecutionTraceKind, ExecutionTraceStatus
        from atnos.services.chat_tool_focus import memory_opt_out

        # Enforce at persistence, even if a caller forgot to disable writeback.
        # Conversation messages/traces remain operational history, not memory.
        if memory_opt_out(user_message.content or ''):
            return self.execution_traces.create(
                step_name="memory-writeback", trace_kind=ExecutionTraceKind.MEMORY,
                status=ExecutionTraceStatus.SKIPPED, conversation_id=conversation.id,
                message_id=assistant_message.id,
                input_summary="Explicit user memory write denial",
                output_summary="Skipped summaries, project memory and Obsidian writeback.",
                metadata_json={"memory_write_denied": True, "obsidian_memory_written": False,
                               "project_memory_updated": False, "project_summary_updated": False},
            )

        conversation_summary = self._build_conversation_summary(
            conversation=conversation,
            user_message=user_message,
            assistant_message=assistant_message,
            attachments=attachments,
        )
        self.conversations.update_summary(conversation, conversation_summary)

        project_memory_updated = False
        project_summary_updated = False
        obsidian_memory_path = None
        if runtime_preferences.memory_enabled:
            try:
                from atnos.core.config import Settings
                from atnos.services.chat_session_memory import persist_curated_chat_memory

                obsidian_memory_path = persist_curated_chat_memory(
                    settings=Settings(),
                    user_message=user_message.content or "",
                    conversation_id=conversation.id,
                    message_id=assistant_message.id,
                    project_name=(
                        conversation.project.name
                        if conversation.project is not None
                        else None
                    ),
                    enabled=True,
                    session=self.session,
                )
            except Exception:
                # Obsidian is a human-readable mirror; a vault outage must not
                # fail the canonical conversation summary writeback.
                obsidian_memory_path = None
        if conversation.project_id is not None:
            project = self.projects.get(conversation.project_id)
            memory_candidate = self.memory_curator.classify_turn(
                user_text=user_message.content,
                assistant_text=assistant_message.content,
            )
            should_refresh_project_memory = self._should_refresh_project_memory(
                project_summary=project.summary if project is not None else None,
                project_context_applied=project_context_applied,
                user_message=user_message,
                memory_candidate=memory_candidate,
            )
            if should_refresh_project_memory and project is not None:
                self.project_memories.upsert_conversation_summary(
                    project_id=project.id,
                    source_conversation_id=conversation.id,
                    source_message_id=assistant_message.id,
                    content=conversation_summary,
                    priority=self.memory_curator.to_project_memory_priority(memory_candidate.priority),
                )
                project_memory_updated = True

                project_summary = self._build_project_summary(project.id)
                self.projects.update_summary(project, project_summary)
                project_summary_updated = True

        output_summary = "conversation summary refreshed"
        if project_memory_updated and project_summary_updated:
            output_summary += "; project summary refreshed"
        elif conversation.project_id is not None and not project_context_applied:
            output_summary += "; project memory skipped because project context was not applied"
        if obsidian_memory_path is not None:
            output_summary += "; curated Obsidian memory written"

        return self.execution_traces.create(
            step_name="memory-writeback",
            trace_kind=ExecutionTraceKind.MEMORY,
            status=ExecutionTraceStatus.COMPLETED,
            conversation_id=conversation.id,
            message_id=assistant_message.id,
            input_summary=(
                f"messages={assistant_message.sequence_number} "
                f"attachments={len(tuple(attachments))} "
                f"work_mode={runtime_preferences.work_mode} "
                f"response_style={runtime_preferences.response_style or 'none'} "
                f"persona_profile={runtime_preferences.persona_profile.value if runtime_preferences.persona_profile is not None else 'none'} "
                f"project_context_applied={project_context_applied}"
            ),
            output_summary=output_summary,
            metadata_json={
                "conversation_summary_chars": len(conversation_summary),
                "project_memory_updated": project_memory_updated,
                "project_summary_updated": project_summary_updated,
                "project_context_applied": project_context_applied,
                "obsidian_memory_written": obsidian_memory_path is not None,
                "obsidian_memory_path": str(obsidian_memory_path) if obsidian_memory_path is not None else None,
                "memory_curation": (
                    memory_candidate.to_dict()
                    if conversation.project_id is not None
                    else None
                ),
                "work_mode": runtime_preferences.work_mode,
                "work_mode_source": runtime_preferences.work_mode_source,
                "response_style": runtime_preferences.response_style,
                "response_style_source": runtime_preferences.response_style_source,
                "persona_profile": (
                    runtime_preferences.persona_profile.value
                    if runtime_preferences.persona_profile is not None
                    else None
                ),
                "persona_profile_source": runtime_preferences.persona_profile_source,
                "memory_enabled": runtime_preferences.memory_enabled,
                "memory_enabled_source": runtime_preferences.memory_enabled_source,
            },
        )

    def _build_conversation_summary(
        self,
        *,
        conversation: Conversation,
        user_message: Message,
        assistant_message: Message,
        attachments: tuple[Attachment, ...] | list[Attachment],
    ) -> str:
        attachment_label = self._summarize_attachments(attachments)
        user_focus = user_message.content or (
            f"attachment-only input: {attachment_label}" if attachment_label else "attachment-only input"
        )
        parts = [
            f"Title: {self._truncate(conversation.title, 80)}",
            f"Latest user goal: {self._truncate(user_focus, self.MAX_USER_SNIPPET_CHARS)}",
            f"Current answer: {self._truncate(assistant_message.content, self.MAX_ASSISTANT_SNIPPET_CHARS)}",
        ]
        if attachment_label:
            parts.append(f"Current materials: {self._truncate(attachment_label, self.MAX_ATTACHMENT_LABEL_CHARS)}")

        return self._truncate(" | ".join(parts), self.MAX_CONVERSATION_SUMMARY_CHARS)

    def _build_project_summary(self, project_id: int) -> str:
        conversations = self.conversations.list_by_project(project_id)[: self.RECENT_PROJECT_CONVERSATIONS]
        if not conversations:
            return ""

        entries: list[str] = []
        for conversation in conversations:
            summary_source = conversation.summary or conversation.title
            entries.append(
                f"{self._truncate(conversation.title, 60)}: "
                f"{self._truncate(summary_source, 120)}"
            )

        return self._truncate(
            "Recent project focus: " + " || ".join(entries),
            self.MAX_PROJECT_SUMMARY_CHARS,
        )

    @staticmethod
    def _summarize_attachments(attachments: tuple[Attachment, ...] | list[Attachment]) -> str:
        names = [attachment.filename for attachment in attachments if attachment.filename]
        return ", ".join(names[:3])

    @staticmethod
    def _truncate(value: str, limit: int) -> str:
        normalized = " ".join(value.split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 3].rstrip() + "..."

    @staticmethod
    def _normalized_text(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None

    def _should_refresh_project_memory(
        self,
        *,
        project_summary: str | None,
        project_context_applied: bool,
        user_message: Message,
        memory_candidate: MemoryCandidate,
    ) -> bool:
        if self.memory_curator.should_write_project_memory(
            project_context_applied=project_context_applied,
            project_summary=project_summary,
            candidate=memory_candidate,
        ):
            return True

        normalized_message = " ".join((user_message.content or "").lower().split())
        if not normalized_message:
            return False

        return any(
            keyword in normalized_message
            for keyword in self.PROJECT_MEMORY_WRITEBACK_KEYWORDS
        )
