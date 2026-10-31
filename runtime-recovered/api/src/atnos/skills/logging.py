from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from atnos.core.logging import append_redacted_jsonl
from atnos.skills.models import SkillRouteDecision


API_ROOT = Path(__file__).resolve().parents[3]
SKILL_DECISION_LOG_PATH = API_ROOT / "data" / "logs" / "skill_decisions.jsonl"


def log_skill_decision(decision: SkillRouteDecision) -> None:
    SKILL_DECISION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "logged_at": datetime.now(UTC).isoformat(),
        "decision": decision.model_dump(),
    }
    append_redacted_jsonl(SKILL_DECISION_LOG_PATH, payload)
