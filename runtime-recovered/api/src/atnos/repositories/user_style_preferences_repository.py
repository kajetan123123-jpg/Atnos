from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.db.models.user_style_preferences import UserStylePreferences


class UserStylePreferencesRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_for_user(self, user_id: int) -> UserStylePreferences | None:
        statement = select(UserStylePreferences).where(UserStylePreferences.user_id == user_id)
        return self.session.scalar(statement)

    def create(self, **kwargs) -> UserStylePreferences:
        style_preferences = UserStylePreferences(**kwargs)
        self.session.add(style_preferences)
        self.session.flush()
        return style_preferences

    def update(self, style_preferences: UserStylePreferences, **kwargs) -> UserStylePreferences:
        for field, value in kwargs.items():
            setattr(style_preferences, field, value)
        self.session.add(style_preferences)
        self.session.flush()
        return style_preferences
