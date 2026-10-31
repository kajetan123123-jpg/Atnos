from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.db.models.enums import ProjectMemoryKind, ProjectMemoryPriority
from atnos.db.models.project import ProjectMemory


class ProjectMemoryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_active_summary_for_conversation(
        self,
        *,
        project_id: int,
        source_conversation_id: int,
    ) -> ProjectMemory | None:
        statement = (
            select(ProjectMemory)
            .where(
                ProjectMemory.project_id == project_id,
                ProjectMemory.source_conversation_id == source_conversation_id,
                ProjectMemory.memory_kind == ProjectMemoryKind.SUMMARY,
                ProjectMemory.is_archived.is_(False),
            )
            .order_by(ProjectMemory.id.desc())
            .limit(1)
        )
        return self.session.scalar(statement)

    def upsert_conversation_summary(
        self,
        *,
        project_id: int,
        source_conversation_id: int,
        source_message_id: int,
        content: str,
        priority: ProjectMemoryPriority = ProjectMemoryPriority.MEDIUM,
    ) -> ProjectMemory:
        memory = self.get_active_summary_for_conversation(
            project_id=project_id,
            source_conversation_id=source_conversation_id,
        )
        if memory is None:
            memory = ProjectMemory(
                project_id=project_id,
                source_conversation_id=source_conversation_id,
                source_message_id=source_message_id,
                memory_kind=ProjectMemoryKind.SUMMARY,
                priority=priority,
                content=content,
                is_pinned=False,
                is_archived=False,
            )
            self.session.add(memory)
        else:
            memory.source_message_id = source_message_id
            memory.priority = priority
            memory.content = content
            memory.is_archived = False
            self.session.add(memory)

        self.session.flush()
        return memory

    def list_active_for_project(
        self,
        project_id: int,
        *,
        limit: int = 6,
    ) -> list[ProjectMemory]:
        statement = (
            select(ProjectMemory)
            .where(
                ProjectMemory.project_id == project_id,
                ProjectMemory.is_archived.is_(False),
            )
            .order_by(
                ProjectMemory.is_pinned.desc(),
                ProjectMemory.updated_at.desc(),
                ProjectMemory.id.desc(),
            )
            .limit(limit)
        )
        return list(self.session.scalars(statement))
