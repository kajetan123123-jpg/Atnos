from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.skills.skill_cards import build_skill_dashboard_payload


class SkillDashboardService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def build_payload(self) -> dict[str, Any]:
        return build_skill_dashboard_payload(fast_mode=False)
