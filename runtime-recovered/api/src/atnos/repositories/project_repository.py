from __future__ import annotations

import re
import unicodedata

from sqlalchemy.orm import Session

from atnos.db.models.project import Project
from atnos.db.models.enums import PersonaProfile, ProviderPosture


class ProjectRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, project_id: int) -> Project | None:
        return self.session.get(Project, project_id)

    def list_all(self) -> list[Project]:
        return (
            self.session.query(Project)
            .order_by(Project.name.asc(), Project.id.asc())
            .all()
        )

    def create(
        self,
        name: str,
        *,
        description: str | None = None,
        provider_posture: ProviderPosture | None = None,
        persona_profile: PersonaProfile | None = None,
    ) -> Project:
        project = Project(
            name=name,
            slug=self._build_unique_slug(name),
            description=description,
            provider_posture=provider_posture,
            persona_profile=persona_profile,
        )
        self.session.add(project)
        self.session.flush()
        return project

    def update(
        self,
        project: Project,
        *,
        name: str,
        description: str | None,
        provider_posture: ProviderPosture | None,
        persona_profile: PersonaProfile | None,
    ) -> Project:
        project.name = name
        project.slug = self._build_unique_slug(name, excluding_project_id=project.id)
        project.description = description
        project.provider_posture = provider_posture
        project.persona_profile = persona_profile
        self.session.add(project)
        self.session.flush()
        return project

    def delete(self, project: Project) -> None:
        self.session.delete(project)
        self.session.flush()

    def update_summary(self, project: Project, summary: str | None) -> Project:
        project.summary = summary
        self.session.add(project)
        self.session.flush()
        return project

    def update_provider_posture(
        self,
        project: Project,
        provider_posture: ProviderPosture | None,
    ) -> Project:
        project.provider_posture = provider_posture
        self.session.add(project)
        self.session.flush()
        return project

    def update_persona_profile(
        self,
        project: Project,
        persona_profile: PersonaProfile | None,
    ) -> Project:
        project.persona_profile = persona_profile
        self.session.add(project)
        self.session.flush()
        return project

    def _build_unique_slug(
        self,
        name: str,
        *,
        excluding_project_id: int | None = None,
    ) -> str:
        base_slug = self._slugify(name)
        slug = base_slug
        suffix = 2

        while self._slug_exists(slug, excluding_project_id=excluding_project_id):
            slug = f"{base_slug}-{suffix}"
            suffix += 1

        return slug

    def _slug_exists(
        self,
        slug: str,
        *,
        excluding_project_id: int | None = None,
    ) -> bool:
        query = self.session.query(Project).filter(Project.slug == slug)
        if excluding_project_id is not None:
            query = query.filter(Project.id != excluding_project_id)
        return self.session.query(query.exists()).scalar() is True

    @staticmethod
    def _slugify(name: str) -> str:
        normalized = unicodedata.normalize("NFKD", name)
        ascii_name = normalized.encode("ascii", "ignore").decode("ascii").lower()
        slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")
        return slug or "project"
