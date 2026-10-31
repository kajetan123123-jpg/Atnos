from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.db.models.runtime_control_settings import RuntimeControlSettings


class RuntimeControlSettingsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_for_user(self, user_id: int) -> RuntimeControlSettings | None:
        statement = select(RuntimeControlSettings).where(RuntimeControlSettings.user_id == user_id)
        return self.session.scalar(statement)

    def create(self, **kwargs) -> RuntimeControlSettings:
        controls = RuntimeControlSettings(**kwargs)
        self.session.add(controls)
        self.session.flush()
        return controls

    def update(self, controls: RuntimeControlSettings, **kwargs) -> RuntimeControlSettings:
        for field, value in kwargs.items():
            setattr(controls, field, value)
        self.session.add(controls)
        self.session.flush()
        return controls
