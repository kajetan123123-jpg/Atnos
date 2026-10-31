from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


ProductionStepName = Literal["hook", "script", "voice", "visuals", "video", "assembly", "thumbnail", "qa", "upload_package"]


@dataclass(frozen=True, slots=True)
class ApprovedTopic:
    id: str
    account_id: str
    title: str
    description: str
    total_score: float
    content_profile: str = ""


@dataclass(slots=True)
class ProductionContext:
    job_id: str
    account_id: str
    topic: ApprovedTopic
    artifact_dir: Path
    artifacts: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StepResult:
    artifact_name: str
    artifact_path: Path
    payload: dict[str, Any]
    message: str
    evidence_paths: tuple[Path, ...] = ()
