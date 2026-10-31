from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from atnos.db.models.enums import PersonaProfile
from atnos.repositories.execution_policy_repository import ExecutionPolicyRepository
from atnos.repositories.project_repository import ProjectRepository
from atnos.repositories.user_repository import UserRepository


class PersonaProfileResolverError(RuntimeError):
    pass


class PersonaProfileProjectNotFoundError(PersonaProfileResolverError):
    pass


@dataclass(slots=True, frozen=True)
class PersonaProfileResolution:
    global_default: PersonaProfile
    project_override: PersonaProfile | None
    resolved: PersonaProfile
    source: Literal["global_default", "project_override"]
    project_id: int | None


class PersonaProfileResolverService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.execution_policies = ExecutionPolicyRepository(session)
        self.projects = ProjectRepository(session)

    def resolve(self, *, project_id: int | None = None) -> PersonaProfileResolution:
        owner = self.users.get_owner()
        if owner is None:
            raise PersonaProfileResolverError("Owner user is not configured.")

        execution_policy = self.execution_policies.get_for_user(owner.id)
        if execution_policy is None:
            raise PersonaProfileResolverError("Execution policy is not configured for the owner user.")

        project_override = None
        resolved_project_id = None

        if project_id is not None:
            project = self.projects.get(project_id)
            if project is None:
                raise PersonaProfileProjectNotFoundError(
                    f"Unknown project_id '{project_id}' for persona profile resolution."
                )
            project_override = project.persona_profile
            resolved_project_id = project.id

        resolved = project_override or execution_policy.default_persona_profile
        source: Literal["global_default", "project_override"] = (
            "project_override" if project_override is not None else "global_default"
        )

        return PersonaProfileResolution(
            global_default=execution_policy.default_persona_profile,
            project_override=project_override,
            resolved=resolved,
            source=source,
            project_id=resolved_project_id,
        )
