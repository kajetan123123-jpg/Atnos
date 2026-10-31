from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from atnos.db.models.conversation import Conversation
from atnos.db.models.enums import PersonaProfile, ProviderPosture
from atnos.repositories.conversation_repository import ConversationRepository
from atnos.repositories.execution_policy_repository import ExecutionPolicyRepository
from atnos.repositories.project_repository import ProjectRepository
from atnos.repositories.user_repository import UserRepository


RESPONSE_STYLE_VALUES = {
    "concise-direct",
    "professional-clear",
    "supportive-guide",
    "critical-deep",
}
WORK_MODE_VALUES = {
    "auto",
    "quick",
    "deep",
    "execute",
}


class ConversationSettingsResolverError(RuntimeError):
    pass


class ConversationSettingsNotFoundError(ConversationSettingsResolverError):
    pass


@dataclass(slots=True, frozen=True)
class ConversationSettingsResolution:
    conversation_id: int
    active_work_mode: str
    active_work_mode_source: Literal["conversation", "default"]
    response_style_override: str | None
    memory_enabled_override: bool | None
    file_enabled_override: bool | None
    internet_enabled_override: bool | None
    persona_profile_override: PersonaProfile | None
    provider_posture_override: ProviderPosture | None
    resolved_response_style: str | None
    resolved_response_style_source: Literal["conversation", "default"]
    resolved_memory_enabled: bool
    resolved_memory_enabled_source: Literal["conversation", "default"]
    resolved_file_enabled: bool
    resolved_file_enabled_source: Literal["conversation", "default"]
    resolved_internet_enabled: bool
    resolved_internet_enabled_source: Literal["conversation", "default", "request"]
    resolved_persona_profile: PersonaProfile
    resolved_persona_profile_source: Literal["conversation", "project", "global"]
    resolved_provider_posture: ProviderPosture
    resolved_provider_posture_source: Literal["conversation", "project", "global", "request"]


class ConversationSettingsResolverService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.execution_policies = ExecutionPolicyRepository(session)
        self.projects = ProjectRepository(session)
        self.conversations = ConversationRepository(session)

    def resolve(
        self,
        *,
        conversation: Conversation | None = None,
        conversation_id: int | None = None,
    ) -> ConversationSettingsResolution:
        resolved_conversation = conversation
        if resolved_conversation is None:
            if conversation_id is None:
                raise ConversationSettingsResolverError(
                    "Conversation settings resolution requires a conversation or conversation_id."
                )
            resolved_conversation = self.conversations.get(conversation_id)
            if resolved_conversation is None:
                raise ConversationSettingsNotFoundError(
                    f"Unknown conversation_id '{conversation_id}'."
                )

        owner = self.users.get_owner()
        if owner is None:
            raise ConversationSettingsResolverError("Owner user is not configured.")

        execution_policy = self.execution_policies.get_for_user(owner.id)
        if execution_policy is None:
            raise ConversationSettingsResolverError(
                "Execution policy is not configured for the owner user."
            )

        project = None
        if resolved_conversation.project_id is not None:
            project = resolved_conversation.project or self.projects.get(
                resolved_conversation.project_id
            )

        resolved_response_style = resolved_conversation.response_style_override
        resolved_response_style_source: Literal["conversation", "default"] = (
            "conversation" if resolved_response_style is not None else "default"
        )

        resolved_memory_enabled = (
            resolved_conversation.memory_enabled_override
            if resolved_conversation.memory_enabled_override is not None
            else True
        )
        resolved_memory_enabled_source: Literal["conversation", "default"] = (
            "conversation"
            if resolved_conversation.memory_enabled_override is not None
            else "default"
        )

        resolved_file_enabled = (
            resolved_conversation.file_enabled_override
            if resolved_conversation.file_enabled_override is not None
            else True
        )
        resolved_file_enabled_source: Literal["conversation", "default"] = (
            "conversation"
            if resolved_conversation.file_enabled_override is not None
            else "default"
        )

        resolved_internet_enabled = (
            resolved_conversation.internet_enabled_override
            if resolved_conversation.internet_enabled_override is not None
            else True
        )
        resolved_internet_enabled_source: Literal["conversation", "default"] = (
            "conversation"
            if resolved_conversation.internet_enabled_override is not None
            else "default"
        )

        if resolved_conversation.persona_profile_override is not None:
            resolved_persona_profile = resolved_conversation.persona_profile_override
            resolved_persona_profile_source: Literal[
                "conversation",
                "project",
                "global",
            ] = "conversation"
        elif project is not None and project.persona_profile is not None:
            resolved_persona_profile = project.persona_profile
            resolved_persona_profile_source = "project"
        else:
            resolved_persona_profile = execution_policy.default_persona_profile
            resolved_persona_profile_source = "global"

        if resolved_conversation.provider_posture_override is not None:
            resolved_provider_posture = resolved_conversation.provider_posture_override
            resolved_provider_posture_source: Literal[
                "conversation",
                "project",
                "global",
            ] = "conversation"
        elif project is not None and project.provider_posture is not None:
            resolved_provider_posture = project.provider_posture
            resolved_provider_posture_source = "project"
        else:
            resolved_provider_posture = execution_policy.default_provider_posture
            resolved_provider_posture_source = "global"

        return ConversationSettingsResolution(
            conversation_id=resolved_conversation.id,
            active_work_mode=self._normalize_work_mode(
                resolved_conversation.active_work_mode
            ),
            active_work_mode_source=(
                "conversation"
                if self._normalize_work_mode(resolved_conversation.active_work_mode) != "auto"
                else "default"
            ),
            response_style_override=resolved_conversation.response_style_override,
            memory_enabled_override=resolved_conversation.memory_enabled_override,
            file_enabled_override=resolved_conversation.file_enabled_override,
            internet_enabled_override=resolved_conversation.internet_enabled_override,
            persona_profile_override=resolved_conversation.persona_profile_override,
            provider_posture_override=resolved_conversation.provider_posture_override,
            resolved_response_style=resolved_response_style,
            resolved_response_style_source=resolved_response_style_source,
            resolved_memory_enabled=resolved_memory_enabled,
            resolved_memory_enabled_source=resolved_memory_enabled_source,
            resolved_file_enabled=resolved_file_enabled,
            resolved_file_enabled_source=resolved_file_enabled_source,
            resolved_internet_enabled=resolved_internet_enabled,
            resolved_internet_enabled_source=resolved_internet_enabled_source,
            resolved_persona_profile=resolved_persona_profile,
            resolved_persona_profile_source=resolved_persona_profile_source,
            resolved_provider_posture=resolved_provider_posture,
            resolved_provider_posture_source=resolved_provider_posture_source,
        )

    @staticmethod
    def _normalize_work_mode(value: str | None) -> str:
        normalized = " ".join((value or "").split()).lower()
        if normalized in WORK_MODE_VALUES:
            return normalized
        return "auto"
