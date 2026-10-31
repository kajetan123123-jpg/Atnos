from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


BrowserRiskLevel = Literal["low", "medium", "high"]


@dataclass(frozen=True, slots=True)
class BrowserAction:
    action_type: str
    target: str | None
    value: str | None
    reason: str
    expected_result: str
    risk_level: BrowserRiskLevel = "low"

    def to_dict(self) -> dict[str, object]:
        return {
            "action_type": self.action_type,
            "target": self.target,
            "value": self.value,
            "reason": self.reason,
            "expected_result": self.expected_result,
            "risk_level": self.risk_level,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "BrowserAction":
        return cls(
            action_type=str(payload.get("action_type") or ""),
            target=str(payload["target"]) if payload.get("target") is not None else None,
            value=str(payload["value"]) if payload.get("value") is not None else None,
            reason=str(payload.get("reason") or ""),
            expected_result=str(payload.get("expected_result") or ""),
            risk_level=str(payload.get("risk_level") or "low"),  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class BrowserObservation:
    url: str
    title: str
    visible_text: str
    clickable_elements: list[str]
    forms: list[str]
    screenshot_path: str | None = None


@dataclass(frozen=True, slots=True)
class BrowserResult:
    success: bool
    status: str
    url: str
    message: str
    screenshot_path: str | None = None
    extracted_text: str | None = None


@dataclass(frozen=True, slots=True)
class BrowserRecipe:
    site: str
    task: str
    steps: list[BrowserAction] = field(default_factory=list)
    human_required: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "site": self.site,
            "task": self.task,
            "steps": [step.to_dict() for step in self.steps],
            "human_required": list(self.human_required),
            "risks": list(self.risks),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "BrowserRecipe":
        raw_steps = payload.get("steps")
        steps = [
            BrowserAction.from_dict(item)
            for item in raw_steps
            if isinstance(item, dict)
        ] if isinstance(raw_steps, list) else []
        return cls(
            site=str(payload.get("site") or ""),
            task=str(payload.get("task") or ""),
            steps=steps,
            human_required=[
                str(item) for item in (payload.get("human_required") or []) if str(item).strip()
            ],
            risks=[str(item) for item in (payload.get("risks") or []) if str(item).strip()],
            notes=str(payload.get("notes") or ""),
        )


def normalize_path(value: str | Path | None) -> str | None:
    if value is None:
        return None
    return str(Path(value).resolve())
