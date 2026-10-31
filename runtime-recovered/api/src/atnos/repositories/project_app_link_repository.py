from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.db.models.project_app_link import ProjectAppLink


class ProjectAppLinkRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_for_project(self, project_id: int) -> list[ProjectAppLink]:
        statement = (
            select(ProjectAppLink)
            .where(ProjectAppLink.project_id == project_id)
            .order_by(
                ProjectAppLink.is_default_for_project.desc(),
                ProjectAppLink.is_default_for_content.desc(),
                ProjectAppLink.is_default_for_analytics.desc(),
                ProjectAppLink.label.asc(),
                ProjectAppLink.id.asc(),
            )
        )
        return list(self.session.scalars(statement))

    def get_for_project(self, project_id: int, link_id: int) -> ProjectAppLink | None:
        statement = select(ProjectAppLink).where(
            ProjectAppLink.project_id == project_id,
            ProjectAppLink.id == link_id,
        )
        return self.session.scalar(statement)

    def list_for_account(self, account_id: int) -> list[ProjectAppLink]:
        statement = (
            select(ProjectAppLink)
            .where(ProjectAppLink.account_id == account_id)
            .order_by(ProjectAppLink.project_id.asc(), ProjectAppLink.id.asc())
        )
        return list(self.session.scalars(statement))

    def create(self, **kwargs) -> ProjectAppLink:
        item = ProjectAppLink(**kwargs)
        self.session.add(item)
        self.session.flush()
        return item

    def save(self, item: ProjectAppLink) -> ProjectAppLink:
        self.session.add(item)
        self.session.flush()
        return item
