from __future__ import annotations

from abc import ABC, abstractmethod
from logging import Logger, getLogger
from typing import Any

from atnos.jarvis.core.config import JarvisConfigStore
from atnos.jarvis.core.event_bus import EventBus


class JarvisModule(ABC):
    def __init__(self, *, name: str, event_bus: EventBus, config_store: JarvisConfigStore) -> None:
        self.name = name
        self.event_bus = event_bus
        self.config_store = config_store
        self.logger: Logger = getLogger(f"{__name__}.{name}")

    @abstractmethod
    async def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def status(self) -> dict[str, Any]:
        raise NotImplementedError
