from __future__ import annotations

from enum import Enum

from atnos.core.config import Settings


class AutonomyMode(str, Enum):
    ASSISTED = "assisted"
    SUPERVISED = "supervised"
    FULL_AUTO_SANDBOX = "full_auto_sandbox"


FULL_AUTO_SANDBOX_MODE = AutonomyMode.FULL_AUTO_SANDBOX


def resolve_autonomy_mode(settings: Settings | None = None) -> AutonomyMode:
    resolved_settings = settings or Settings()
    explicit_mode = resolved_settings.atnos_autonomy_mode
    if explicit_mode:
        return AutonomyMode(explicit_mode)
    if resolved_settings.atnos_agent_autonomy_level == "full_sandbox":
        return AutonomyMode.FULL_AUTO_SANDBOX
    return AutonomyMode.ASSISTED


def is_full_auto_sandbox_mode(settings: Settings | None = None) -> bool:
    return resolve_autonomy_mode(settings) == AutonomyMode.FULL_AUTO_SANDBOX
