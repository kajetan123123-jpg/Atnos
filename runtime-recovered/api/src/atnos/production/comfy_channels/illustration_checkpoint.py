"""Verified, opt-in WB illustration checkpoint; never an appearance approval.

The original author publishes a full SDXL checkpoint (UNET, both encoders,
and VAE).  Read its small safetensors header without importing Torch or
memory-mapping model weights on the Windows ROCm host.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import struct
from typing import Any

from atnos.core.config import Settings

CANDIDATE_ID = "dreamshaper-xl-alpha2"
TRIAL_ENV = "ATNOS_WB_ILLUSTRATION_CANDIDATE"
REPOSITORY = "Lykon/DreamShaper"
REVISION = "228d79cb20811466f5c5710aa91f05dabd0b8a14"
SOURCE_FILE = "DreamShaperXL1.0Alpha2_fixedVae_half_00001_.safetensors"
MODEL_SHA256 = "0f1b80cfe81b9c3bde7fdcbf6898897b2811b27be1df684583c3d85cbc9b1fa4"
MODEL_BYTES = 6_938_068_556
CHECKPOINT_FILE = "dreamshaper_sdxl_alpha2_wb_candidate.safetensors"
REQUIRED_TENSORS = (
    "model.diffusion_model.input_blocks.0.0.weight",
    "conditioner.embedders.0.transformer.text_model.embeddings.token_embedding.weight",
    "conditioner.embedders.1.model.token_embedding.weight",
    "first_stage_model.encoder.conv_in.weight",
)


class IllustrationCheckpointError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_full_sdxl_checkpoint(path: Path, *, expected_sha256: str = MODEL_SHA256,
                                expected_bytes: int = MODEL_BYTES) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size != expected_bytes:
        raise IllustrationCheckpointError("WB illustration checkpoint missing or truncated")
    with path.open("rb") as stream:
        prefix = stream.read(8)
        if len(prefix) != 8:
            raise IllustrationCheckpointError("Incomplete safetensors header")
        header_size = struct.unpack("<Q", prefix)[0]
        if header_size < 2 or header_size > min(16 * 1024 * 1024, expected_bytes - 8):
            raise IllustrationCheckpointError("Invalid safetensors header size")
        try:
            header = json.loads(stream.read(header_size))
        except (ValueError, UnicodeDecodeError) as exc:
            raise IllustrationCheckpointError("Unreadable safetensors header") from exc
    if not isinstance(header, dict) or any(key not in header for key in REQUIRED_TENSORS):
        raise IllustrationCheckpointError("Not a full SDXL checkpoint: UNET, two encoders and VAE required")
    tensors = {key: value for key, value in header.items() if key != "__metadata__"}
    payload_bytes = expected_bytes - 8 - header_size
    for name, tensor in tensors.items():
        offsets = tensor.get("data_offsets") if isinstance(tensor, dict) else None
        if (not isinstance(offsets, list) or len(offsets) != 2
                or any(type(value) is not int for value in offsets)
                or not 0 <= offsets[0] <= offsets[1] <= payload_bytes):
            raise IllustrationCheckpointError(f"Invalid tensor extent: {name}")
    actual_sha = sha256_file(path)
    if actual_sha != expected_sha256:
        raise IllustrationCheckpointError("WB illustration checkpoint SHA256 mismatch")
    return {"sha256": actual_sha, "size_bytes": expected_bytes,
            "header_bytes": header_size, "tensor_count": len(tensors),
            "components": ["unet", "clip_l", "clip_g", "vae"]}


def receipt_path(settings: Settings) -> Path:
    app = Path(settings.app_root or r"E:\Atnos\App")
    return app / "config" / "wb_illustration_candidates" / f"{CANDIDATE_ID}.json"


def _e_path(path: Path) -> None:
    # The application runs on Windows; tests may use an isolated Posix tmpdir.
    if os.name == "nt" and path.resolve().drive.casefold() != "e:":
        raise IllustrationCheckpointError("WB illustration assets must stay on E:")


def load_opt_in_candidate(settings: Settings, channel: str) -> dict[str, Any] | None:
    selected = os.environ.get(TRIAL_ENV, "").strip()
    if channel != "wealth_blueprint" or not selected:
        return None
    if selected != CANDIDATE_ID:
        raise IllustrationCheckpointError("Unknown WB illustration trial candidate")
    receipt = receipt_path(settings)
    _e_path(receipt)
    try:
        raw = receipt.read_bytes()
        payload = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise IllustrationCheckpointError("Verified WB illustration installation receipt unavailable") from exc
    source = payload.get("source") or {}
    path = Path(str(payload.get("checkpoint_path") or ""))
    _e_path(path)
    from atnos.production.comfy_channels.profiles import shared_models_root
    expected_path = (shared_models_root(settings) / "checkpoints" / CHECKPOINT_FILE).resolve()
    if (payload.get("schema_version") != "wb-illustration-checkpoint-1"
            or payload.get("candidate_id") != CANDIDATE_ID
            or payload.get("appearance_approval") != "pending"
            or payload.get("user_approved") is not False
            or source.get("repository") != REPOSITORY
            or source.get("revision") != REVISION
            or source.get("filename") != SOURCE_FILE
            or path.name != CHECKPOINT_FILE
            or path.resolve() != expected_path
            or payload.get("verification", {}).get("sha256") != MODEL_SHA256
            or payload.get("verification", {}).get("size_bytes") != MODEL_BYTES
            or payload.get("license", {}).get("commercial_images") is not True):
        raise IllustrationCheckpointError("WB illustration installation receipt integrity failed")
    verify_full_sdxl_checkpoint(path)
    return {"candidate_id": CANDIDATE_ID, "checkpoint": f"checkpoints/{CHECKPOINT_FILE}",
            "checkpoint_sha256": MODEL_SHA256, "receipt_path": str(receipt),
            "receipt_sha256": hashlib.sha256(raw).hexdigest(),
            "appearance_approval": "pending", "user_approved": False}
