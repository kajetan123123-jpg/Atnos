"""Wealth Blueprint audio pipeline models.

script → TTS → narration.wav → alignment → word_timestamps.json
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "1.0"
CHANNEL = "wealth_blueprint"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class WordTimestamp(StrictModel):
    word: str = Field(min_length=1, max_length=80)
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    segment_id: str | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def end_after_start(self) -> "WordTimestamp":
        if self.end < self.start:
            raise ValueError("word end must be >= start")
        return self


class SentenceSegment(StrictModel):
    id: str = Field(min_length=1, max_length=40)
    text: str = Field(min_length=1, max_length=2000)
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    word_count: int = Field(ge=1, le=500)

    @model_validator(mode="after")
    def end_after_start(self) -> "SentenceSegment":
        if self.end <= self.start:
            raise ValueError("segment end must be > start")
        return self


class ValidationIssue(StrictModel):
    code: str
    severity: Literal["error", "warning"]
    message: str


class AudioValidationReport(StrictModel):
    ok: bool
    audio_exists: bool
    duration_seconds: float | None = None
    empty_segments: bool = False
    monotonic_timestamps: bool = True
    large_gaps: list[dict[str, float | str]] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)


class AudioPipelineResult(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    channel: str = CHANNEL
    script: str
    audio_file: str
    duration_seconds: float
    words: list[WordTimestamp]
    segments: list[SentenceSegment]
    timing_source: str
    engine: str
    fingerprint: str
    cache_hit: bool = False
    validation: AudioValidationReport
    word_timestamps_path: str
    alignment_path: str | None = None


class AudioPipelineRequest(StrictModel):
    script: str = Field(min_length=1, max_length=20_000)
    channel: str = CHANNEL
    sentences: list[str] = Field(default_factory=list, max_length=64)
    force_regenerate: bool = False
    large_gap_seconds: float = Field(default=1.25, ge=0.2, le=10.0)

    @field_validator("script")
    @classmethod
    def normalize_script(cls, value: str) -> str:
        cleaned = " ".join(value.replace("\r\n", "\n").split())
        if not cleaned:
            raise ValueError("script must not be empty")
        return cleaned
