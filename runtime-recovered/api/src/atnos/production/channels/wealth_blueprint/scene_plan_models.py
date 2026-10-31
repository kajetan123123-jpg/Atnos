"""Wealth Blueprint ScenePlan — decision layer between script and renderer.

This is NOT a Remotion ProjectManifest and does NOT trigger render.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from atnos.production.channels.wealth_blueprint.layouts import LAYOUT_IDS, LayoutId

SCHEMA_VERSION = "wealth_blueprint_scene_plan_1"
CHANNEL = "wealth_blueprint"

VisualFocus = Literal["statement", "number", "chart", "icon", "comparison", "character", "simplified"]
PlannerMode = Literal["deterministic", "llm_assisted"]
SceneMode = Literal["diagram", "character", "comparison", "statement", "illustration"]
DominantIntent = Literal[
    "ACTION",
    "TRANSFER",
    "SPLIT",
    "REPEAT",
    "COMPARISON",
    "CHANGE_OVER_TIME",
    "CAUSE_EFFECT",
    "NUMBER_HERO",
    "CHARACTER_EXPLAINER",
    "NOTE_TRACK",
    "RELATIONSHIP",
    "LOOP",
    "CONSEQUENCE",
    "VISUAL_METAPHOR",
    "DATA_STORY",
]
LayoutFamily = Literal[
    "EDITORIAL_HERO",
    "MECHANISM",
    "CHARACTER_EXPLAINER",
    "DATA_STORY",
    "VISUAL_METAPHOR",
]
ShotClass = Literal[
    "HERO",
    "EXPLAINER",
    "DATA",
    "COMPARISON",
    "TIMELINE",
    "PERSON",
    "OBJECT",
    "ENVIRONMENT",
    "UI",
    "QUOTE",
    "ACTION_STEP",
    "TRANSITION",
]
VisualDensity = Literal["low", "medium", "high"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NarrationWord(StrictModel):
    word: str
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)


class NarrationSegment(StrictModel):
    id: str = Field(min_length=1, max_length=40)
    # Preserve the original script identity if short beats are coalesced by the
    # scene planner. Empty defaults keep older planner inputs compatible.
    source_segment_ids: list[str] = Field(default_factory=list, max_length=40)
    source_visual_intents: list[str] = Field(default_factory=list, max_length=40)
    text: str = Field(min_length=1, max_length=2000)
    on_screen: str = Field(default="", max_length=120)
    # The final script's directing handoff.  This is deliberately separate
    # from narration: it tells the planner what physical relation the viewer
    # must see, without putting camera promises into the spoken text.
    visual_intent: str = Field(default="", max_length=350)
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)

    @model_validator(mode="after")
    def end_after_start(self) -> "NarrationSegment":
        if self.end <= self.start:
            raise ValueError("segment end must be > start")
        return self


class AssetNeed(StrictModel):
    """Semantic asset request for Asset Brain — never a hardcoded file path."""

    type: str = Field(min_length=1, max_length=40)
    tags: list[str] = Field(default_factory=list, max_length=12)
    character: str | None = None
    pose: str | None = None
    text: str = Field(default="", max_length=240)
    required: bool = True


class AnimationCue(StrictModel):
    preset_id: str = Field(min_length=1, max_length=40)
    at_sec: float = Field(ge=0.0)
    duration_sec: float = Field(default=0.55, ge=0.1, le=4.0)
    target: str = Field(default="primary", max_length=40)


class SoundCue(StrictModel):
    event: str = Field(min_length=1, max_length=40)
    at_sec: float = Field(ge=0.0)
    priority: int = Field(default=0, ge=0, le=100)


class ChartCue(StrictModel):
    chart_type: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=120)
    labels: list[str] = Field(default_factory=list, max_length=8)
    values: list[float] = Field(default_factory=list, max_length=8)
    claim_ids: list[str] = Field(default_factory=list, max_length=4)
    unit: str | None = Field(default=None, max_length=20)
    verified_series: dict | None = None

    @property
    def is_quantitative(self) -> bool:
        """True only when chart carries source-backed numeric series (≥2 points + claim ids)."""
        return len(self.values) >= 2 and bool(self.claim_ids)


class VisualIntent(StrictModel):
    """What the viewer must understand — planner output that Remotion must honor."""

    viewer_should_understand: str = Field(min_length=1, max_length=240)
    visible_objects: list[str] = Field(default_factory=list, max_length=8)
    visible_change: str = Field(default="", max_length=240)
    scene_mode: SceneMode = "statement"
    why_this_image: str = Field(default="", max_length=240)
    dominant_intent: DominantIntent = "ACTION"
    layout_family: LayoutFamily = "EDITORIAL_HERO"
    primary: str = Field(default="", max_length=80)
    secondary: str = Field(default="", max_length=80)
    motion: str = Field(default="", max_length=80)
    initial_state: str = Field(default="", max_length=240)
    action: str = Field(default="", max_length=80)
    direction: str = Field(default="", max_length=80)
    condition: str = Field(default="", max_length=120)
    final_state: str = Field(default="", max_length=240)
    compliance_criterion: str = Field(default="", max_length=240)
    relation: dict | None = None


class PlannedScene(StrictModel):
    scene_id: str = Field(min_length=1, max_length=40)
    layout_id: LayoutId
    start_sec: float = Field(ge=0.0)
    end_sec: float = Field(ge=0.0)
    narration_segment_ids: list[str] = Field(default_factory=list, max_length=40)
    source_visual_intents: list[str] = Field(default_factory=list, max_length=40)
    narration_excerpt: str = Field(default="", max_length=2000)
    on_screen_text: str = Field(min_length=1, max_length=120)
    emphasis_words: list[str] = Field(default_factory=list, max_length=6)
    visual_focus: VisualFocus
    use_alex: bool = False
    alex_pose: str | None = Field(default=None, max_length=40)
    simplify: bool = False
    number_value: str | None = Field(default=None, max_length=32)
    chart: ChartCue | None = None
    icon_tags: list[str] = Field(default_factory=list, max_length=8)
    asset_needs: list[AssetNeed] = Field(default_factory=list, max_length=8)
    animations: list[AnimationCue] = Field(default_factory=list, max_length=4)
    sound_events: list[SoundCue] = Field(default_factory=list, max_length=4)
    rationale: str = Field(default="", max_length=400)
    visual_intent: VisualIntent | None = None
    visual_concept: str | None = Field(default=None, max_length=40)
    visual_story: dict | None = None
    meaning_contract: dict | None = None
    shot_class: ShotClass | None = None
    visual_density: VisualDensity | None = None

    @model_validator(mode="after")
    def timing_and_alex(self) -> "PlannedScene":
        if self.end_sec <= self.start_sec:
            raise ValueError("scene end must be > start")
        if self.use_alex and not self.alex_pose:
            self.alex_pose = "explaining"
        if not self.use_alex:
            self.alex_pose = None
        return self


class ValidationIssue(StrictModel):
    code: str
    severity: Literal["error", "warning"]
    message: str
    scene_id: str | None = None


class ScenePlanValidation(StrictModel):
    ok: bool
    issues: list[ValidationIssue] = Field(default_factory=list)


class WealthBlueprintScenePlan(StrictModel):
    schema_version: Literal["wealth_blueprint_scene_plan_1"] = SCHEMA_VERSION
    channel: Literal["wealth_blueprint"] = CHANNEL
    planner_mode: PlannerMode = "deterministic"
    script: str = Field(min_length=1, max_length=20_000)
    duration_sec: float = Field(gt=0.0, le=90.0)
    scenes: list[PlannedScene] = Field(min_length=1, max_length=20)
    music_bed: str = "bed_editorial"
    validation: ScenePlanValidation | None = None
    notes: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("scenes")
    @classmethod
    def unique_scene_ids(cls, value: list[PlannedScene]) -> list[PlannedScene]:
        ids = [scene.scene_id for scene in value]
        if len(ids) != len(set(ids)):
            raise ValueError("scene ids must be unique")
        return value


class ScenePlannerInput(StrictModel):
    script: str = Field(min_length=1, max_length=20_000)
    segments: list[NarrationSegment] = Field(default_factory=list, max_length=40)
    words: list[NarrationWord] = Field(default_factory=list, max_length=2000)
    profile: dict = Field(default_factory=dict)
    available_asset_types: list[str] = Field(default_factory=list, max_length=32)
    design_system: dict = Field(default_factory=dict)
    mode: PlannerMode = "deterministic"
    # Approved NumericClaim dicts from editorial script (id/operation/operands/result/unit/…).
    claims: list[dict] = Field(default_factory=list, max_length=20)
    # narration segment id → claim ids attached to that beat
    segment_claim_map: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("script")
    @classmethod
    def normalize_script(cls, value: str) -> str:
        cleaned = " ".join(value.replace("\r\n", "\n").split())
        if not cleaned:
            raise ValueError("script must not be empty")
        return cleaned


# Ensure layout literal stays aligned with catalog.
assert set(LAYOUT_IDS)  # noqa: B018 — import side-effect documentation
