from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from atnos.db.models.media_pipeline_preset import MediaPipelinePreset


class MediaPipelinePresetRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_all(self) -> list[MediaPipelinePreset]:
        statement = select(MediaPipelinePreset).order_by(
            MediaPipelinePreset.project_id.asc().nullsfirst(),
            MediaPipelinePreset.name.asc(),
            MediaPipelinePreset.id.asc(),
        )
        return list(self.session.scalars(statement))

    def list_for_project(self, project_id: int | None) -> list[MediaPipelinePreset]:
        statement = (
            select(MediaPipelinePreset)
            .where(or_(MediaPipelinePreset.project_id == project_id, MediaPipelinePreset.project_id.is_(None)))
            .order_by(MediaPipelinePreset.project_id.asc().nullsfirst(), MediaPipelinePreset.name.asc())
        )
        return list(self.session.scalars(statement))

    def get_by_id(self, preset_id: int) -> MediaPipelinePreset | None:
        statement = select(MediaPipelinePreset).where(MediaPipelinePreset.id == preset_id)
        return self.session.scalar(statement)

    def create(self, **kwargs) -> MediaPipelinePreset:
        item = MediaPipelinePreset(**kwargs)
        self.session.add(item)
        self.session.flush()
        return item

    def save(self, item: MediaPipelinePreset) -> MediaPipelinePreset:
        self.session.add(item)
        self.session.flush()
        return item
