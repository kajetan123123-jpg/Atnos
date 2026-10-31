"""Wealth Blueprint video job statuses and checkpoint schema."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

JobStatus = Literal[
    "PENDING",
    "SCRIPT",
    "AUDIO",
    "PLANNING",
    "ASSETS",
    "RENDER_PREVIEW",
    "QC",
    "CORRECTIONS",
    "FINAL_RENDER",
    "DONE",
    "FAILED",
    "BLOCKED_DEPENDENCY",
]

STAGE_ORDER: tuple[JobStatus, ...] = (
    "PENDING",
    "SCRIPT",
    "AUDIO",
    "PLANNING",
    "ASSETS",
    "RENDER_PREVIEW",
    "QC",
    "CORRECTIONS",
    "FINAL_RENDER",
    "DONE",
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class StageRecord(StrictModel):
    name: JobStatus
    status: Literal["pending", "running", "completed", "failed", "skipped", "blocked"] = "pending"
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None
    input_hash: str | None = None
    output_paths: list[str] = Field(default_factory=list)
    error: str | None = None
    notes: list[str] = Field(default_factory=list)
    blocked_dependency: str | None = None


class JobState(StrictModel):
    job_id: str
    job_type: Literal["wealth_blueprint_video_job"] = "wealth_blueprint_video_job"
    status: JobStatus = "PENDING"
    topic: str = ""
    created_at: str
    updated_at: str
    artifact_dir: str
    stages: dict[str, StageRecord] = Field(default_factory=dict)
    auto_corrections: list[str] = Field(default_factory=list)
    blocked_dependencies: list[str] = Field(default_factory=list)
    final_mp4: str | None = None
    report_path: str | None = None
    upload: Literal[False] = False
