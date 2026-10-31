from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.db.models.role_profile import RoleProfile


class RoleProfileRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_active(self) -> list[RoleProfile]:
        statement = (
            select(RoleProfile)
            .where(RoleProfile.is_archived.is_(False))
            .order_by(RoleProfile.is_default.desc(), RoleProfile.is_builtin.desc(), RoleProfile.name.asc())
        )
        return list(self.session.scalars(statement))

    def get(self, role_id: int) -> RoleProfile | None:
        return self.session.get(RoleProfile, role_id)

    def get_default(self) -> RoleProfile | None:
        statement = select(RoleProfile).where(RoleProfile.is_default.is_(True))
        return self.session.scalar(statement)

    def clear_default(self, *, exclude_role_id: int | None = None) -> None:
        for role in self.list_active():
            if exclude_role_id is not None and role.id == exclude_role_id:
                continue
            if role.is_default:
                role.is_default = False
                self.session.add(role)
        self.session.flush()

    def create(self, **kwargs) -> RoleProfile:
        role = RoleProfile(**kwargs)
        self.session.add(role)
        self.session.flush()
        return role

    def update(self, role: RoleProfile, **kwargs) -> RoleProfile:
        for field, value in kwargs.items():
            setattr(role, field, value)
        self.session.add(role)
        self.session.flush()
        return role
