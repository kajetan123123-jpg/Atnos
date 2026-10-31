from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.db.models.user import User


class UserRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, user_id: int) -> User | None:
        return self.session.get(User, user_id)

    def get_owner(self) -> User | None:
        statement = select(User).where(User.is_owner.is_(True))
        return self.session.scalar(statement)

    def get_by_handle(self, handle: str) -> User | None:
        statement = select(User).where(User.handle == handle)
        return self.session.scalar(statement)

    def create(
        self,
        *,
        display_name: str,
        handle: str,
        locale: str = "pl-PL",
        timezone: str = "Europe/Warsaw",
        is_owner: bool = False,
        is_active: bool = True,
        email: str | None = None,
        bio: str | None = None,
    ) -> User:
        user = User(
            display_name=display_name,
            handle=handle,
            locale=locale,
            timezone=timezone,
            is_owner=is_owner,
            is_active=is_active,
            email=email,
            bio=bio,
        )
        self.session.add(user)
        self.session.flush()
        return user

    def update(self, user: User, **kwargs) -> User:
        for field, value in kwargs.items():
            setattr(user, field, value)
        self.session.add(user)
        self.session.flush()
        return user
