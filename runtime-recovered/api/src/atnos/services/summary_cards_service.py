from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from atnos.repositories.conversation_repository import ConversationRepository
from atnos.repositories.project_app_link_repository import ProjectAppLinkRepository
from atnos.services.integration_config_service import (
    IntegrationConfigService,
    OBSIDIAN_KEY,
    OBSIDIAN_VAULT_NAME,
)


class SummaryCardsNotFoundError(ValueError):
    pass


@dataclass(slots=True, frozen=True)
class ConversationSummaryCardData:
    conversation_id: int
    conversation_title: str
    conversation_summary: str | None
    has_conversation_summary: bool
    project_id: int | None


@dataclass(slots=True, frozen=True)
class ProjectSummaryCardData:
    project_id: int
    project_name: str
    project_summary: str | None
    has_project_summary: bool


@dataclass(slots=True, frozen=True)
class ObsidianMemoryCardData:
    vault_name: str
    linked_label: str | None
    project_linked: bool
    path_configured: bool
    export_enabled: bool
    ready_for_preview: bool
    ready_for_confirm: bool
    relative_path_hint: str
    helper_text: str


@dataclass(slots=True, frozen=True)
class SummaryCardsResult:
    conversation: ConversationSummaryCardData
    project: ProjectSummaryCardData | None
    obsidian_memory: ObsidianMemoryCardData | None


class SummaryCardsService:
    def __init__(self, session: Session, settings) -> None:
        self.session = session
        self.conversations = ConversationRepository(session)
        self.project_app_links = ProjectAppLinkRepository(session)
        self.integration_config = IntegrationConfigService(session, settings)

    def get_for_conversation(self, conversation_id: int) -> SummaryCardsResult:
        conversation = self.conversations.get(conversation_id)
        if conversation is None:
            raise SummaryCardsNotFoundError(f"Conversation {conversation_id} does not exist.")

        conversation_card = ConversationSummaryCardData(
            conversation_id=conversation.id,
            conversation_title=conversation.title,
            conversation_summary=conversation.summary,
            has_conversation_summary=bool(conversation.summary and conversation.summary.strip()),
            project_id=conversation.project_id,
        )

        project_card = None
        if conversation.project is not None:
            project_card = ProjectSummaryCardData(
                project_id=conversation.project.id,
                project_name=conversation.project.name,
                project_summary=conversation.project.summary,
                has_project_summary=bool(
                    conversation.project.summary and conversation.project.summary.strip()
                ),
            )

        obsidian_memory_card = None
        if conversation.project is not None:
            config = self.integration_config.get_obsidian_config()
            obsidian_link = next(
                (
                    item
                    for item in self.project_app_links.list_for_project(conversation.project.id)
                    if item.integration_key == OBSIDIAN_KEY and item.status != "disabled"
                ),
                None,
            )
            project_linked = obsidian_link is not None
            path_configured = bool(config.path_exists and config.vault_path is not None)
            export_enabled = config.export_enabled
            ready_for_preview = project_linked and path_configured
            ready_for_confirm = ready_for_preview and export_enabled
            if project_linked or path_configured or export_enabled:
                helper_text = (
                    "Pamiec Obsidiana jest gotowa. Mozesz przygotowac preview albo potwierdzic eksport aktualnej pamieci projektu."
                    if ready_for_confirm
                    else "Podepnij Obsidiana do projektu i zapisz sciezke vaulta, zanim wyeksportujesz pamiec projektu."
                )
                obsidian_memory_card = ObsidianMemoryCardData(
                    vault_name=OBSIDIAN_VAULT_NAME,
                    linked_label=obsidian_link.label if obsidian_link is not None else None,
                    project_linked=project_linked,
                    path_configured=path_configured,
                    export_enabled=export_enabled,
                    ready_for_preview=ready_for_preview,
                    ready_for_confirm=ready_for_confirm,
                    relative_path_hint=f"Atnos/Project Memory/{conversation.project.slug}-memory.md",
                    helper_text=helper_text,
                )

        return SummaryCardsResult(
            conversation=conversation_card,
            project=project_card,
            obsidian_memory=obsidian_memory_card,
        )
