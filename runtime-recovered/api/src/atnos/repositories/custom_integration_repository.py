from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.db.models.custom_integration import CustomIntegration


class CustomIntegrationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_all(self) -> list[CustomIntegration]:
        statement = select(CustomIntegration).order_by(CustomIntegration.label.asc(), CustomIntegration.id.asc())
        return list(self.session.scalars(statement))

    def get_by_key(self, key: str) -> CustomIntegration | None:
        statement = select(CustomIntegration).where(CustomIntegration.key == key)
        return self.session.scalar(statement)

    def get_by_id(self, integration_id: int) -> CustomIntegration | None:
        statement = select(CustomIntegration).where(CustomIntegration.id == integration_id)
        return self.session.scalar(statement)

    def create(
        self,
        *,
        key: str,
        label: str,
        category: str,
        status: str,
        summary: str | None,
        capabilities_json: list[str],
        permission_level: str,
        notes: str,
        setup_mode: str,
        setup_requirements_json: list[str],
        missing_requirements_json: list[str],
        next_action_label: str,
        source_kind: str,
        has_adapter: bool,
    ) -> CustomIntegration:
        integration = CustomIntegration(
            key=key,
            label=label,
            category=category,
            status=status,
            summary=summary,
            capabilities_json=capabilities_json,
            permission_level=permission_level,
            notes=notes,
            setup_mode=setup_mode,
            setup_requirements_json=setup_requirements_json,
            missing_requirements_json=missing_requirements_json,
            next_action_label=next_action_label,
            source_kind=source_kind,
            has_adapter=has_adapter,
        )
        self.session.add(integration)
        self.session.flush()
        return integration

    def save(self, integration: CustomIntegration) -> CustomIntegration:
        self.session.add(integration)
        self.session.flush()
        return integration
