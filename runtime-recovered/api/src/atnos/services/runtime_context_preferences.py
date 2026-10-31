from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from atnos.db.models.enums import PersonaProfile

PreferenceSource = Literal["default", "global", "project", "conversation"]


@dataclass(slots=True, frozen=True)
class RuntimeContextPreferences:
    conversation_id: int
    project_id: int | None
    response_style: str | None
    response_style_source: PreferenceSource
    persona_profile: PersonaProfile | None
    persona_profile_source: PreferenceSource
    memory_enabled: bool
    memory_enabled_source: PreferenceSource
    work_mode: str
    work_mode_source: PreferenceSource
    provider_posture: str | None = None
    provider_posture_source: PreferenceSource = "default"
    files_enabled: bool = True
    files_enabled_source: PreferenceSource = "default"
    internet_enabled: bool = True
    internet_enabled_source: PreferenceSource = "default"
    quality_profile: str = "standard"
    quality_profile_source: PreferenceSource = "default"
