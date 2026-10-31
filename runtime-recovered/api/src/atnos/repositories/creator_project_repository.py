from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.db.models.creator_project import ContentCalendarItem, CreatorProject, CreatorWorkItem


class CreatorProjectRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_projects(self) -> list[CreatorProject]:
        statement = select(CreatorProject).order_by(
            CreatorProject.updated_at.desc(),
            CreatorProject.id.desc(),
        )
        return list(self.session.scalars(statement))

    def get_project_by_id(self, project_id: int) -> CreatorProject | None:
        statement = select(CreatorProject).where(CreatorProject.id == project_id)
        return self.session.scalar(statement)

    def get_project_by_canonical_project_id(self, project_id: int) -> CreatorProject | None:
        statement = select(CreatorProject).where(CreatorProject.project_id == project_id)
        return self.session.scalar(statement)

    def create_project(
        self,
        *,
        project_id: int | None,
        name: str,
        project_type: str,
        status: str,
        persona_type: str,
        disclosure_policy: str,
        niche: str | None,
        target_audience: str | None,
        monetization_goal: str | None,
        content_boundaries: str | None,
        safety_boundaries: str | None,
        platforms_json: list[str],
        linked_accounts_json: list[int],
        linked_integrations_json: list[str],
        operating_mode: str,
        approval_policy: str,
        notes: str | None,
        strategy_profile_json: dict[str, object] | None = None,
    ) -> CreatorProject:
        item = CreatorProject(
            project_id=project_id,
            name=name,
            project_type=project_type,
            status=status,
            persona_type=persona_type,
            disclosure_policy=disclosure_policy,
            niche=niche,
            target_audience=target_audience,
            monetization_goal=monetization_goal,
            content_boundaries=content_boundaries,
            safety_boundaries=safety_boundaries,
            platforms_json=platforms_json,
            linked_accounts_json=linked_accounts_json,
            linked_integrations_json=linked_integrations_json,
            operating_mode=operating_mode,
            approval_policy=approval_policy,
            notes=notes,
            strategy_profile_json=strategy_profile_json,
        )
        self.session.add(item)
        self.session.flush()
        return item

    def save_project(self, item: CreatorProject) -> CreatorProject:
        self.session.add(item)
        self.session.flush()
        return item

    def list_work_items(self, creator_project_id: int) -> list[CreatorWorkItem]:
        statement = select(CreatorWorkItem).where(
            CreatorWorkItem.creator_project_id == creator_project_id,
        ).order_by(
            CreatorWorkItem.created_at.asc(),
            CreatorWorkItem.id.asc(),
        )
        return list(self.session.scalars(statement))

    def get_work_item_by_id(self, work_item_id: int) -> CreatorWorkItem | None:
        statement = select(CreatorWorkItem).where(CreatorWorkItem.id == work_item_id)
        return self.session.scalar(statement)

    def create_work_item(
        self,
        *,
        creator_project_id: int,
        title: str,
        work_type: str,
        status: str,
        risk_level: str,
        approval_required: bool,
        platform: str | None,
        draft_payload_json: dict[str, object] | None,
        review_notes: str | None,
        next_action: str | None,
        safety_flags_json: list[str],
    ) -> CreatorWorkItem:
        item = CreatorWorkItem(
            creator_project_id=creator_project_id,
            title=title,
            work_type=work_type,
            status=status,
            risk_level=risk_level,
            approval_required=approval_required,
            platform=platform,
            draft_payload_json=draft_payload_json,
            review_notes=review_notes,
            next_action=next_action,
            safety_flags_json=safety_flags_json,
        )
        self.session.add(item)
        self.session.flush()
        return item

    def save_work_item(self, item: CreatorWorkItem) -> CreatorWorkItem:
        self.session.add(item)
        self.session.flush()
        return item

    def list_content_calendar_items(self, creator_project_id: int) -> list[ContentCalendarItem]:
        statement = select(ContentCalendarItem).where(
            ContentCalendarItem.creator_project_id == creator_project_id,
        ).order_by(
            ContentCalendarItem.planned_date.asc(),
            ContentCalendarItem.updated_at.desc(),
            ContentCalendarItem.id.asc(),
        )
        return list(self.session.scalars(statement))

    def get_content_calendar_item_by_id(self, item_id: int) -> ContentCalendarItem | None:
        statement = select(ContentCalendarItem).where(ContentCalendarItem.id == item_id)
        return self.session.scalar(statement)

    def create_content_calendar_item(
        self,
        *,
        creator_project_id: int,
        work_item_id: int | None,
        title: str,
        platform: str | None,
        content_type: str,
        planned_date,
        status: str,
        risk_level: str,
        approval_required: bool,
        linked_app_link_id: int | None,
        notes: str | None,
    ) -> ContentCalendarItem:
        item = ContentCalendarItem(
            creator_project_id=creator_project_id,
            work_item_id=work_item_id,
            title=title,
            platform=platform,
            content_type=content_type,
            planned_date=planned_date,
            status=status,
            risk_level=risk_level,
            approval_required=approval_required,
            linked_app_link_id=linked_app_link_id,
            notes=notes,
        )
        self.session.add(item)
        self.session.flush()
        return item

    def save_content_calendar_item(self, item: ContentCalendarItem) -> ContentCalendarItem:
        self.session.add(item)
        self.session.flush()
        return item
