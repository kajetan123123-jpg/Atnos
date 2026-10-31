"""Wealth Blueprint Sound Design Engine — semantic events, not hardcoded files."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "1.0"
CHANNEL = "wealth_blueprint"

SoundLayer = Literal["VOICE", "MUSIC", "SFX", "AMBIENCE"]
SOUND_LAYERS: tuple[SoundLayer, ...] = ("VOICE", "MUSIC", "SFX", "AMBIENCE")

SemanticSoundEvent = Literal[
    "soft_whoosh",
    "pop",
    "click",
    "impact",
    "coin",
    "money",
    "notification",
    "rise",
    "drop",
    "bed_editorial",
    "bed_calm",
    "ambience_soft",
]

SEMANTIC_SOUND_EVENTS: tuple[SemanticSoundEvent, ...] = (
    "soft_whoosh",
    "pop",
    "click",
    "impact",
    "coin",
    "money",
    "notification",
    "rise",
    "drop",
    "bed_editorial",
    "bed_calm",
    "ambience_soft",
)

SFX_EVENTS: frozenset[str] = frozenset(
    {
        "soft_whoosh",
        "pop",
        "click",
        "impact",
        "coin",
        "money",
        "notification",
        "rise",
        "drop",
    }
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SoundAssetRecord(StrictModel):
    """One approved (or pending) sound file bound to a semantic event."""

    id: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,119}$")
    event: SemanticSoundEvent
    layer: SoundLayer
    file_path: str = Field(min_length=1, max_length=500)
    channel: str = CHANNEL
    variant: str = Field(default="a", min_length=1, max_length=40)
    tags: list[str] = Field(default_factory=list, max_length=24)
    duration_sec: float | None = Field(default=None, ge=0.01, le=600)
    default_gain_db: float = Field(default=-6.0, ge=-48.0, le=6.0)
    approved: bool = False
    source: str = Field(default="library", min_length=1, max_length=120)
    license_status: Literal["owned", "licensed", "generated_pending_review", "unknown"] = "owned"
    description: str = Field(default="", max_length=400)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in value:
            tag = " ".join(str(raw).strip().casefold().split())
            if tag and tag not in seen:
                seen.add(tag)
                cleaned.append(tag)
        return cleaned

    @model_validator(mode="after")
    def layer_matches_event(self) -> "SoundAssetRecord":
        if self.event in SFX_EVENTS and self.layer != "SFX":
            raise ValueError(f"SFX event {self.event} must use layer SFX")
        if self.event.startswith("bed_") and self.layer != "MUSIC":
            raise ValueError("bed_* events must use MUSIC layer")
        if self.event.startswith("ambience_") and self.layer != "AMBIENCE":
            raise ValueError("ambience_* events must use AMBIENCE layer")
        return self


class SoundCatalog(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    channel: str = CHANNEL
    library_id: str = "wealth_blueprint_sound_registry_v1"
    assets: list[SoundAssetRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_ids(self) -> "SoundCatalog":
        ids = [asset.id for asset in self.assets]
        if len(ids) != len(set(ids)):
            raise ValueError("sound asset ids must be unique")
        return self


class SoundEventRequest(StrictModel):
    """Scene Planner declaration — semantic only, never a file path."""

    event: SemanticSoundEvent
    at_sec: float = Field(ge=0.0, le=600.0)
    gain_db: float | None = Field(default=None, ge=-48.0, le=6.0)
    fade_in_sec: float = Field(default=0.01, ge=0.0, le=5.0)
    fade_out_sec: float = Field(default=0.08, ge=0.0, le=5.0)
    duration_sec: float | None = Field(default=None, ge=0.02, le=60.0)
    prefer_variant: str | None = Field(default=None, max_length=40)
    allow_random_variant: bool = True
    priority: int = Field(default=0, ge=0, le=100)


class MusicBedRequest(StrictModel):
    event: Literal["bed_editorial", "bed_calm"] = "bed_editorial"
    gain_db: float = Field(default=-18.0, ge=-48.0, le=-6.0)
    fade_in_sec: float = Field(default=1.2, ge=0.0, le=8.0)
    fade_out_sec: float = Field(default=1.5, ge=0.0, le=8.0)
    allow_random_variant: bool = True


class AmbienceRequest(StrictModel):
    event: Literal["ambience_soft"] = "ambience_soft"
    gain_db: float = Field(default=-24.0, ge=-48.0, le=-10.0)
    fade_in_sec: float = Field(default=2.0, ge=0.0, le=10.0)
    fade_out_sec: float = Field(default=2.0, ge=0.0, le=10.0)
    allow_random_variant: bool = True


class VoiceTrackSpec(StrictModel):
    file_path: str = Field(min_length=1, max_length=500)
    duration_sec: float = Field(gt=0.05, le=600.0)
    gain_db: float = Field(default=0.0, ge=-12.0, le=6.0)
    speech_windows: list[tuple[float, float]] = Field(default_factory=list)
    """Inclusive narration activity windows (start_sec, end_sec) for ducking."""


class SoundMixPolicy(StrictModel):
    """Pacing + safety — SFX supports story rhythm, never spam."""

    voice_priority: Literal[True] = True
    music_duck_db: float = Field(default=-12.0, ge=-24.0, le=-3.0)
    music_duck_attack_sec: float = Field(default=0.12, ge=0.02, le=1.0)
    music_duck_release_sec: float = Field(default=0.55, ge=0.1, le=3.0)
    ambience_duck_db: float = Field(default=-8.0, ge=-20.0, le=-2.0)
    min_sfx_gap_sec: float = Field(default=0.55, ge=0.15, le=5.0)
    max_sfx_per_minute: int = Field(default=7, ge=1, le=20)
    max_sfx_total: int = Field(default=12, ge=1, le=40)
    limiter_ceiling_db: float = Field(default=-1.5, ge=-6.0, le=-0.5)
    target_lufs: float = Field(default=-14.0, ge=-20.0, le=-10.0)
    auto_attach_motion_sfx: Literal[False] = False


class ResolvedSoundClip(StrictModel):
    layer: SoundLayer
    event: str
    asset_id: str
    file_path: str
    start_sec: float
    end_sec: float
    gain_db: float
    fade_in_sec: float
    fade_out_sec: float
    variant: str
    ducked: bool = False


class SoundMixPlan(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    channel: str = CHANNEL
    duration_sec: float
    voice: ResolvedSoundClip | None = None
    clips: list[ResolvedSoundClip] = Field(default_factory=list)
    dropped_events: list[dict[str, str | float]] = Field(default_factory=list)
    policy: SoundMixPolicy = Field(default_factory=SoundMixPolicy)
    ffmpeg_filter_complex: str = ""
    notes: list[str] = Field(default_factory=list)
