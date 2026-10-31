from __future__ import annotations

import hashlib
import json
from pathlib import Path

from atnos.production.asset_brain.models import AssetGenerationRequest


def request_fingerprint(
    request: AssetGenerationRequest,
    *,
    reference_hashes: dict[str, str] | None = None,
) -> str:
    """Stable cache key so the same asset request is never regenerated."""
    payload = {
        "channel": request.channel,
        "type": request.type,
        "character": (request.character or "").casefold(),
        "pose": (request.pose or "").casefold(),
        "emotion": (request.emotion or "").casefold(),
        "direction": (request.direction or "").casefold(),
        "tags": sorted(request.tags),
        "text": " ".join(request.text.casefold().split()),
        "width": request.width,
        "height": request.height,
        "transparent_background": request.transparent_background,
        "workflow_id": request.workflow_id
        or ("wb_alex_character_v1" if request.require_alex_anchors else "wb_asset_image_v1"),
        "seed": request.seed,
        "media_kind": request.media_kind,
        "reference_hashes": {
            key: reference_hashes[key]
            for key in sorted((reference_hashes or {}).keys())
        },
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def fingerprint_seed(fingerprint: str) -> int:
    """Deterministic Comfy seed derived from the request fingerprint."""
    return int(fingerprint[:8], 16) % 2_147_483_647


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
