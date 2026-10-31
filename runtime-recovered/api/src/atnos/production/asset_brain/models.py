"""Wealth Blueprint Asset Brain — reusable production asset library.

Search the internal catalog first. Missing assets may be provisioned via ComfyUI
image generation (never full video). See provisioner.py.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "1.0"
WORKFLOW_IMAGE_GENERIC = "wb_asset_image_v1"
WORKFLOW_IMAGE_ALEX = "wb_alex_character_v1"
ApprovalState = Literal["pending", "approved", "rejected"]

# Formal character/asset quality workflow (generated != approved).
QualityStatus = Literal[
    "GENERATED",
    "TECHNICALLY_VALID",
    "AWAITING_REVIEW",
    "APPROVED",
    "REJECTED",
    "SUPERSEDED",
    "NEEDS_REPROCESS",
]
QUALITY_STATUSES: tuple[QualityStatus, ...] = (
    "GENERATED",
    "TECHNICALLY_VALID",
    "AWAITING_REVIEW",
    "APPROVED",
    "REJECTED",
    "SUPERSEDED",
    "NEEDS_REPROCESS",
)
PRODUCTION_SELECTABLE_STATUSES: frozenset[str] = frozenset({"APPROVED"})
REVIEWABLE_STATUSES: frozenset[str] = frozenset({"TECHNICALLY_VALID", "AWAITING_REVIEW"})

RejectReason = Literal[
    "FACE_DRIFT",
    "OUTFIT_DRIFT",
    "COLOR_DRIFT",
    "ANATOMY",
    "HAND_ERROR",
    "ACCESSORY_ERROR",
    "STYLE_MISMATCH",
    "LOW_QUALITY",
    "BACKGROUND_ERROR",
    "ALPHA_QC_FAIL",
    "OTHER",
]
REJECT_REASONS: tuple[RejectReason, ...] = (
    "FACE_DRIFT",
    "OUTFIT_DRIFT",
    "COLOR_DRIFT",
    "ANATOMY",
    "HAND_ERROR",
    "ACCESSORY_ERROR",
    "STYLE_MISMATCH",
    "LOW_QUALITY",
    "BACKGROUND_ERROR",
    "ALPHA_QC_FAIL",
    "OTHER",
)

CHARACTER_IDS_REQUIRING_APPROVAL: frozenset[str] = frozenset(
    {"alex", "niko", "pif", "tika", "mira", "wb_alex_01", "wb-alex-01"}
)
AssetType = Literal[
    "character",
    "background",
    "icon",
    "money",
    "banking",
    "investing",
    "business",
    "houses",
    "cars",
    "technology",
    "charts",
    "decorations",
    "transitions",
]

ASSET_TYPES: tuple[AssetType, ...] = (
    "character",
    "background",
    "icon",
    "money",
    "banking",
    "investing",
    "business",
    "houses",
    "cars",
    "technology",
    "charts",
    "decorations",
    "transitions",
)

# Production roles: reference sheets may be APPROVED for generation/review only.
ProductionRole = Literal[
    "reference_sheet",
    "character_cutout",
    "background",
    "icon",
    "diagram",
    "object",
    "other",
]
PRODUCTION_ROLES: tuple[ProductionRole, ...] = (
    "reference_sheet",
    "character_cutout",
    "background",
    "icon",
    "diagram",
    "object",
    "other",
)
# Roles allowed as final-film scene production assets (never reference_sheet).
FILM_PRODUCTION_ROLES: frozenset[str] = frozenset(
    {"character_cutout", "background", "icon", "diagram", "object", "other"}
)


def infer_production_role(
    *,
    asset_type: AssetType | str,
    source: str | None = None,
    file_path: str | None = None,
    transparent_background: bool = False,
    tags: list[str] | None = None,
) -> ProductionRole:
    """Infer production role when catalog entry omits an explicit role."""
    source_n = " ".join(str(source or "").casefold().split())
    path_n = str(file_path or "").replace("\\", "/").casefold()
    tag_set = {" ".join(str(t).casefold().split()) for t in (tags or [])}
    if (
        "reference_sheet" in tag_set
        or "contact_sheet" in tag_set
        or "channel_references" in source_n
        or "channel_references/" in path_n
        or source_n in {"channel_references", "reference", "reference_sheet"}
    ):
        return "reference_sheet"
    if asset_type == "character":
        return "character_cutout" if transparent_background else "other"
    if asset_type == "background":
        return "background"
    if asset_type == "icon":
        return "icon"
    if asset_type == "charts":
        return "diagram"
    if asset_type in {"money", "banking", "investing", "business", "houses", "cars", "technology"}:
        return "object"
    return "other"

LicenseStatus = Literal[
    "owned",
    "licensed",
    "generated_pending_review",
    "unknown",
    "restricted",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AssetGenerationProvenance(StrictModel):
    """Provenance required for every Comfy-generated library asset."""

    prompt: str = Field(min_length=1, max_length=8000)
    workflow_id: str = Field(min_length=1, max_length=80)
    seed: int | None = None
    model: str = Field(min_length=1, max_length=200)
    generated_at: str = Field(min_length=1, max_length=64)
    source_workflow: str = Field(min_length=1, max_length=120)
    channel: str = Field(min_length=1, max_length=80)
    approval_state: ApprovalState = "pending"
    fingerprint: str = Field(min_length=16, max_length=128)
    reference_files: list[str] = Field(default_factory=list, max_length=16)
    comfy_prompt_id: str | None = Field(default=None, max_length=120)
    backend: str | None = Field(default=None, max_length=80)
    evidence_path: str | None = Field(default=None, max_length=500)
    negative_prompt: str = Field(default="", max_length=2000)


class AssetGenerationRequest(StrictModel):
    """Deterministic request for a still asset (never a full video)."""

    channel: str = "wealth_blueprint"
    type: AssetType
    character: str | None = None
    pose: str | None = None
    emotion: str | None = None
    direction: str | None = None
    tags: list[str] = Field(default_factory=list, max_length=16)
    text: str = Field(default="", max_length=500)
    width: int = Field(default=1024, ge=256, le=2048)
    height: int = Field(default=1024, ge=256, le=2048)
    transparent_background: bool = False
    seed: int | None = None
    workflow_id: str | None = None
    require_alex_anchors: bool = False
    media_kind: Literal["image"] = "image"

    @field_validator("tags")
    @classmethod
    def normalize_request_tags(cls, value: list[str]) -> list[str]:
        return [" ".join(str(item).strip().casefold().split()) for item in value if str(item).strip()]

    @model_validator(mode="before")
    @classmethod
    def image_only_and_alex_rules(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        media = data.get("media_kind", "image")
        if media != "image":
            raise ValueError("ComfyUI asset generation is image-only; video is forbidden")
        character = str(data.get("character") or "").strip().casefold()
        if data.get("type") == "character" and character in {"alex", "wb_alex_01", "wb-alex-01"}:
            data["require_alex_anchors"] = True
        return data

    @classmethod
    def from_query(cls, query: "AssetQuery", *, generation_need: GenerationNeed | None = None) -> "AssetGenerationRequest":
        suggested_type = (
            generation_need.suggested_type
            if generation_need is not None
            else (query.type or (query.types[0] if query.types else "icon"))
        )
        tags = list(dict.fromkeys([*query.tags_all, *query.tags_any]))
        if generation_need is not None:
            tags = list(dict.fromkeys([*tags, *generation_need.suggested_tags]))
        character = (generation_need.suggested_character if generation_need else None) or query.character
        pose = (generation_need.suggested_pose if generation_need else None) or query.pose
        emotion = (generation_need.suggested_emotion if generation_need else None) or query.emotion
        direction = (generation_need.suggested_direction if generation_need else None) or query.direction
        width, height = _default_dimensions(suggested_type)
        return cls(
            channel=query.channel,
            type=suggested_type,
            character=character,
            pose=pose,
            emotion=emotion,
            direction=direction,
            tags=tags,
            text=query.text,
            width=width,
            height=height,
            transparent_background=bool(query.require_transparent) if query.require_transparent is not None else suggested_type != "background",
        )


def _default_dimensions(asset_type: AssetType) -> tuple[int, int]:
    if asset_type == "background":
        return 1080, 1920
    if asset_type == "character":
        return 1024, 1536
    if asset_type == "charts":
        return 1080, 1080
    return 1024, 1024


class AssetRecord(StrictModel):
    """One reusable visual asset with stable metadata for Atnos selection."""

    id: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,119}$")
    type: AssetType
    file_path: str = Field(min_length=1, max_length=500)
    channel: str = Field(min_length=1, max_length=80)
    character: str | None = Field(default=None, max_length=80)
    pose: str | None = Field(default=None, max_length=80)
    emotion: str | None = Field(default=None, max_length=80)
    direction: str | None = Field(default=None, max_length=40)
    tags: list[str] = Field(default_factory=list, max_length=32)
    transparent_background: bool = False
    width: int | None = Field(default=None, ge=1, le=8192)
    height: int | None = Field(default=None, ge=1, le=8192)
    version: str = Field(default="v01", min_length=1, max_length=40)
    approved: bool = False
    # Formal workflow status — source of truth for production selection when set.
    quality_status: QualityStatus = "GENERATED"
    # reference_sheet = generation/review only; never final-film scene still.
    production_role: ProductionRole | None = None
    reject_reason: RejectReason | None = None
    reject_notes: str = Field(default="", max_length=500)
    pre_gate: dict[str, Any] = Field(default_factory=dict)
    reviewed_at: str | None = Field(default=None, max_length=64)
    reviewed_by: str | None = Field(default=None, max_length=128)
    contact_sheet_path: str | None = Field(default=None, max_length=500)
    source: str = Field(default="library", min_length=1, max_length=120)
    license_status: LicenseStatus = "unknown"
    description: str = Field(default="", max_length=500)
    request_fingerprint: str | None = Field(default=None, max_length=128)
    generation: AssetGenerationProvenance | None = None

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in value:
            tag = " ".join(str(raw).strip().casefold().split())
            if not tag or tag in seen:
                continue
            if len(tag) > 48:
                raise ValueError("tag too long")
            seen.add(tag)
            cleaned.append(tag)
        return cleaned

    @model_validator(mode="before")
    @classmethod
    def sync_quality_status_from_legacy_approved(cls, data: object) -> object:
        """Backward compatible: old catalogs only have approved bool."""
        if not isinstance(data, dict):
            return data
        status = data.get("quality_status")
        approved = bool(data.get("approved", False))
        if status is None or status == "":
            if approved:
                data["quality_status"] = "APPROVED"
            elif data.get("generation") is not None:
                data["quality_status"] = "GENERATED"
            else:
                # Legacy library assets without generation block: treat approved flag only
                data["quality_status"] = "APPROVED" if approved else "GENERATED"
        return data

    @model_validator(mode="after")
    def character_assets_need_name_and_sync_approved(self) -> "AssetRecord":
        if self.type == "character" and not (self.character or "").strip():
            raise ValueError("character assets require character metadata")
        role = self.production_role or infer_production_role(
            asset_type=self.type,
            source=self.source,
            file_path=self.file_path,
            transparent_background=self.transparent_background,
            tags=list(self.tags),
        )
        object.__setattr__(self, "production_role", role)
        # Keep boolean in sync for older callers
        object.__setattr__(self, "approved", self.quality_status == "APPROVED")
        if self.generation is not None:
            gen_state = (
                "approved"
                if self.quality_status == "APPROVED"
                else "rejected"
                if self.quality_status == "REJECTED"
                else "pending"
            )
            object.__setattr__(
                self,
                "generation",
                self.generation.model_copy(update={"approval_state": gen_state}),
            )
        return self

    def is_production_selectable(self, *, allow_dev_unapproved: bool = False) -> bool:
        """True only for APPROVED film roles — reference sheets never qualify."""
        from atnos.production.channels.wealth_blueprint.geometric_assets import (
            is_geometric_placeholder_asset,
        )

        role = self.production_role or "other"
        if role == "reference_sheet" or role not in FILM_PRODUCTION_ROLES:
            return False
        if is_geometric_placeholder_asset(self):
            return False
        if allow_dev_unapproved:
            return self.quality_status not in {"REJECTED", "SUPERSEDED"}
        return self.quality_status == "APPROVED"

    def searchable_text(self) -> str:
        parts = [
            self.id,
            self.type,
            self.channel,
            self.production_role or "",
            self.character or "",
            self.pose or "",
            self.emotion or "",
            self.direction or "",
            self.description,
            " ".join(self.tags),
        ]
        return " ".join(part for part in parts if part).strip()


class AssetCatalog(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    channel: str = "wealth_blueprint"
    library_id: str = "wealth_blueprint_asset_brain_v1"
    assets: list[AssetRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_ids(self) -> "AssetCatalog":
        ids = [asset.id for asset in self.assets]
        if len(ids) != len(set(ids)):
            raise ValueError("asset ids must be unique")
        return self


class AssetQuery(StrictModel):
    """Selection request from scene planning / Remotion layout slots."""

    channel: str = "wealth_blueprint"
    type: AssetType | None = None
    types: list[AssetType] = Field(default_factory=list, max_length=13)
    production_role: ProductionRole | None = None
    production_roles: list[ProductionRole] = Field(default_factory=list, max_length=8)
    # When True (default), exclude reference_sheet even if APPROVED for generation.
    exclude_reference_sheets: bool = True
    character: str | None = None
    pose: str | None = None
    emotion: str | None = None
    direction: str | None = None
    version: str | None = None
    tags_any: list[str] = Field(default_factory=list, max_length=16)
    tags_all: list[str] = Field(default_factory=list, max_length=16)
    require_transparent: bool | None = None
    require_approved: bool = True
    # When True (default with require_approved), only APPROVED quality_status is selectable.
    # Dev/test may set require_approved=False to include GENERATED / AWAITING_REVIEW.
    allow_dev_unapproved: bool = False
    text: str = Field(default="", max_length=500)
    limit: int = Field(default=5, ge=1, le=50)
    min_semantic_score: float = Field(default=0.32, ge=0.0, le=1.0)

    @field_validator("tags_any", "tags_all")
    @classmethod
    def normalize_query_tags(cls, value: list[str]) -> list[str]:
        return [" ".join(str(item).strip().casefold().split()) for item in value if str(item).strip()]


class AssetMatch(StrictModel):
    asset: AssetRecord
    score: float
    method: Literal["metadata", "semantic", "hybrid"]
    reasons: list[str] = Field(default_factory=list)


class GenerationNeed(StrictModel):
    """Emitted when the library has no suitable reusable asset."""

    needed: Literal[True] = True
    reason: str
    suggested_type: AssetType
    suggested_tags: list[str] = Field(default_factory=list)
    suggested_character: str | None = None
    suggested_pose: str | None = None
    suggested_emotion: str | None = None
    suggested_direction: str | None = None
    query: AssetQuery
    candidates_considered: int = 0


class AssetSelectionResult(StrictModel):
    status: Literal["found", "missing"]
    match: AssetMatch | None = None
    generation_need: GenerationNeed | None = None
    candidates: list[AssetMatch] = Field(default_factory=list)
