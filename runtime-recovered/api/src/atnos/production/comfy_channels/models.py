"""Comfy channel profiles — one shared model runtime, three isolated channel contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CHANNEL_IDS = ("wealth_blueprint", "wonder_crew", "dark_chronicles")
ChannelId = Literal["wealth_blueprint", "wonder_crew", "dark_chronicles"]

LEGACY_PROJECT_KEYS: dict[str, str] = {
    "wealth_blueprint": "wealth_blueprint",
    "wonder_crew": "children_stories",
    "dark_chronicles": "horror_stories",
}

ALIAS_TO_CANONICAL: dict[str, ChannelId] = {
    "wealth_blueprint": "wealth_blueprint",
    "wonder_crew": "wonder_crew",
    "children_stories": "wonder_crew",
    "kids": "wonder_crew",
    "dark_chronicles": "dark_chronicles",
    "horror_stories": "dark_chronicles",
    "horror": "dark_chronicles",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class WorkflowSpec(StrictModel):
    workflow_id: str = Field(min_length=3, max_length=80)
    workflow_version: str = Field(pattern=r"^v\d+$")
    title: str
    asset_type: str
    backend_preference: list[str] = Field(min_length=1, max_length=4)
    default_width: int = Field(ge=256, le=2048)
    default_height: int = Field(ge=256, le=2048)
    require_references: bool = False
    allow_transparent_bg: bool = False
    forbid_text_in_image: bool = True
    description: str = ""


class ChannelComfyProfile(StrictModel):
    channel_id: ChannelId
    display_name: str
    legacy_project_key: str
    comfy_project_dir: str
    role: str
    style_summary: str
    primary_characters: list[str] = Field(default_factory=list)
    reference_files: list[str] = Field(default_factory=list)
    image_models: list[str] = Field(min_length=1)
    image_fallback: str | None = None
    workflows: list[WorkflowSpec] = Field(min_length=1)
    output_subdir: str
    negative_profile_id: str
    prompt_profile_id: str

    @field_validator("channel_id", mode="before")
    @classmethod
    def normalize_channel(cls, value: Any) -> str:
        key = str(value).strip().casefold().replace(" ", "_")
        if key not in ALIAS_TO_CANONICAL:
            raise ValueError(f"Unsupported Comfy channel: {value}")
        return ALIAS_TO_CANONICAL[key]


class GenerationRequest(StrictModel):
    channel: ChannelId
    workflow: str = Field(min_length=3, max_length=80)
    subject: str = Field(default="", max_length=120)
    emotion: str = Field(default="neutral", max_length=40)
    direction: str = Field(default="front", max_length=40)
    asset_type: str = Field(default="", max_length=60)
    composition: str = Field(default="", max_length=500)
    lighting: str = Field(default="", max_length=200)
    camera: str = Field(default="", max_length=200)
    references: list[str] = Field(default_factory=list, max_length=8)
    aspect_ratio: str = Field(default="1:1", max_length=16)
    width: int | None = Field(default=None, ge=256, le=2048)
    height: int | None = Field(default=None, ge=256, le=2048)
    seed: int | None = Field(default=None, ge=0, le=2_147_483_647)
    force_regenerate: bool = False
    visual_style_id: Literal["A", "B", "C", "D"] | None = None
    visual_style_prompt: str | None = Field(default=None, min_length=20, max_length=1600)
    visual_style_tags: list[str] = Field(default_factory=list, max_length=4)
    visual_style_definition_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    # Benchmark / experiment override — None keeps channel model profile denoise.
    denoise_override: float | None = Field(default=None, ge=0.35, le=0.92)

    @field_validator("channel", mode="before")
    @classmethod
    def normalize_channel(cls, value: Any) -> str:
        key = str(value).strip().casefold().replace(" ", "_")
        if key not in ALIAS_TO_CANONICAL:
            raise ValueError(f"Unsupported Comfy channel: {value}")
        return ALIAS_TO_CANONICAL[key]

    @model_validator(mode="after")
    def validate_visual_style(self):
        if self.visual_style_id:
            if self.channel != "wealth_blueprint" or not self.visual_style_prompt or len(self.visual_style_tags) < 1:
                raise ValueError("WB visual style requires a frozen prompt and asset tags")
        elif self.visual_style_prompt or self.visual_style_tags or self.visual_style_definition_hash:
            raise ValueError("Visual style fields require visual_style_id")
        return self


class GenerationMetadata(StrictModel):
    request_id: str
    channel: ChannelId
    workflow_id: str
    workflow_version: str
    backend: str
    model: str
    vae: str = ""
    seed: int
    prompt: str
    negative_prompt: str
    width: int
    height: int
    references: list[str] = Field(default_factory=list)
    reference_paths: list[str] = Field(default_factory=list)
    output_path: str
    fingerprint: str
    cache_hit: bool = False
    timestamp: str
    generation_settings: dict[str, Any] = Field(default_factory=dict)
