from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.db.models.plugin import Plugin


class PluginRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_key(self, key: str) -> Plugin | None:
        statement = select(Plugin).where(Plugin.key == key)
        return self.session.scalar(statement)

    def list_enabled(self) -> list[Plugin]:
        statement = select(Plugin).where(Plugin.is_enabled.is_(True)).order_by(Plugin.key.asc())
        return list(self.session.scalars(statement))

    def list_all(self) -> list[Plugin]:
        statement = select(Plugin).order_by(Plugin.key.asc())
        return list(self.session.scalars(statement))

    def create(self, **kwargs) -> Plugin:
        plugin = Plugin(**kwargs)
        self.session.add(plugin)
        self.session.flush()
        return plugin

    def update(self, plugin: Plugin, **kwargs) -> Plugin:
        for field, value in kwargs.items():
            setattr(plugin, field, value)
        self.session.add(plugin)
        self.session.flush()
        return plugin
