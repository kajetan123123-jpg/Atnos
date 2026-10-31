from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PluginPermission(str, Enum):
    READ_FILES = "read_files"
    WRITE_FILES = "write_files"
    READ_WEB = "read_web"
    EXECUTE_LOCAL = "execute_local"


@dataclass(slots=True, frozen=True)
class PluginDescriptor:
    key: str
    name: str
    summary: str
    permissions: tuple[PluginPermission, ...]
    requires_confirmation: bool = True
