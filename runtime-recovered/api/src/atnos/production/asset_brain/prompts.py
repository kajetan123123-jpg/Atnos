from __future__ import annotations

from atnos.production.asset_brain.alex_consistency import AlexAnchors
from atnos.production.asset_brain.models import (
    WORKFLOW_IMAGE_ALEX,
    WORKFLOW_IMAGE_GENERIC,
    AssetGenerationRequest,
)

GENERIC_NEGATIVE = (
    "lowres, blurry, text, watermark, logo, photorealism, neon fintech, "
    "stock businessman, luxury flex, jpeg artifacts, deformed hands"
)

ALEX_NEGATIVE = (
    GENERIC_NEGATIVE
    + ", different face, different hairline, different clothing colors, suit, tie, "
    "jewelry, luxury accessories, photorealistic skin, anime, cartoon mascot, "
    "generic AI influencer, reinvented character"
)


def resolve_workflow_id(request: AssetGenerationRequest) -> str:
    if request.workflow_id:
        return request.workflow_id
    if request.require_alex_anchors or (request.type == "character" and _is_alex(request)):
        return WORKFLOW_IMAGE_ALEX
    return WORKFLOW_IMAGE_GENERIC


def build_prompt(request: AssetGenerationRequest, *, alex: AlexAnchors | None = None) -> tuple[str, str]:
    """Return (positive_prompt, negative_prompt). Image stills only."""
    if request.require_alex_anchors or (request.type == "character" and _is_alex(request)):
        if alex is None or not alex.ready:
            raise ValueError("Alex prompt builder requires loaded quality anchors")
        pose = request.pose or "standing"
        emotion = request.emotion or "neutral"
        direction = request.direction or "front"
        parts = [
            f"Wealth Blueprint host {alex.character_id} (Alex).",
            "Match the supplied quality reference identity exactly — same face, hairline, age, proportions, clothing.",
            alex.design_lock,
            f"Silhouette lock: {alex.silhouette}.",
            f"Pose: {pose}. Emotion/expression: {emotion}. Facing: {direction}.",
            f"Style: {alex.style}.",
            "Single character still frame for editorial motion design, full body including sneakers when pose allows.",
            "No baked-in text or logos.",
        ]
        if request.text.strip():
            parts.append(f"Scene intent: {request.text.strip()}")
        if request.tags:
            parts.append("Tags: " + ", ".join(request.tags))
        if request.transparent_background:
            parts.append("Clean cutout-friendly background, solid soft cream paper, easy to isolate.")
        return " ".join(parts), ALEX_NEGATIVE

    subject = request.type.replace("_", " ")
    parts = [
        f"Wealth Blueprint reusable {subject} asset.",
        "Premium hand-drawn editorial illustration, subtle 2.5D, paper texture, restrained navy cream teal gold palette.",
        "No photorealism, no neon fintech, no luxury flex.",
    ]
    if request.pose:
        parts.append(f"Pose/layout: {request.pose}.")
    if request.emotion:
        parts.append(f"Mood: {request.emotion}.")
    if request.direction:
        parts.append(f"Direction: {request.direction}.")
    if request.tags:
        parts.append("Tags: " + ", ".join(request.tags) + ".")
    if request.text.strip():
        parts.append(request.text.strip())
    if request.transparent_background:
        parts.append("Transparent or solid easy-cutout background.")
    return " ".join(parts), GENERIC_NEGATIVE


def _is_alex(request: AssetGenerationRequest) -> bool:
    return (request.character or "").strip().casefold() in {"alex", "wb_alex_01", "wb-alex-01"}
