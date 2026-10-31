from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.db.models.integration_config import IntegrationConfig


class IntegrationConfigRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_key(self, integration_key: str) -> IntegrationConfig | None:
        statement = select(IntegrationConfig).where(IntegrationConfig.integration_key == integration_key)
        return self.session.scalar(statement)

    def upsert(
        self,
        *,
        integration_key: str,
        local_path: str | None,
        export_enabled: bool,
    ) -> IntegrationConfig:
        config = self.get_by_key(integration_key)
        if config is None:
            config = IntegrationConfig(
                integration_key=integration_key,
                local_path=local_path,
                export_enabled=export_enabled,
            )
        else:
            config.local_path = local_path
            config.export_enabled = export_enabled

        self.session.add(config)
        self.session.flush()
        return config
