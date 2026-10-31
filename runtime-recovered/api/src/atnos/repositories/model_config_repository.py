from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from atnos.db.models.enums import ModelTask
from atnos.db.models.model_config import ModelConfig


class ModelConfigRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, model_id: int) -> ModelConfig | None:
        statement = (
            select(ModelConfig)
            .options(joinedload(ModelConfig.provider))
            .where(ModelConfig.id == model_id)
        )
        return self.session.scalar(statement)

    def get_by_key(self, key: str) -> ModelConfig | None:
        statement = (
            select(ModelConfig)
            .options(joinedload(ModelConfig.provider))
            .where(ModelConfig.key == key)
        )
        return self.session.scalar(statement)

    def list_enabled_for_task(self, task: ModelTask) -> list[ModelConfig]:
        statement = (
            select(ModelConfig)
            .options(joinedload(ModelConfig.provider))
            .where(ModelConfig.task == task, ModelConfig.is_enabled.is_(True))
            .order_by(ModelConfig.key.asc())
        )
        return list(self.session.scalars(statement))

    def list_enabled_defaults(self) -> list[ModelConfig]:
        statement = (
            select(ModelConfig)
            .options(joinedload(ModelConfig.provider))
            .where(ModelConfig.is_enabled.is_(True), ModelConfig.is_default.is_(True))
            .order_by(ModelConfig.key.asc())
        )
        return list(self.session.scalars(statement))

    def list_all(self) -> list[ModelConfig]:
        statement = (
            select(ModelConfig)
            .options(joinedload(ModelConfig.provider))
            .order_by(ModelConfig.key.asc())
        )
        return list(self.session.scalars(statement))

    def create(self, **kwargs) -> ModelConfig:
        model_config = ModelConfig(**kwargs)
        self.session.add(model_config)
        self.session.flush()
        return model_config

    def update_runtime_model_name(
        self,
        model_config: ModelConfig,
        runtime_model_name: str | None,
    ) -> ModelConfig:
        model_config.runtime_model_name = runtime_model_name
        self.session.add(model_config)
        self.session.flush()
        return model_config
