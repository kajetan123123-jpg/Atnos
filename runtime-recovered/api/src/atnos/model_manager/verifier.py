from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

from atnos.model_manager.registry import ModelSpec, build_model_registry


async def verify_model_integrity(spec: ModelSpec) -> dict[str, Any]:
    if not spec.local_path.exists() or not spec.local_path.is_file():
        return {
            "model_id": spec.id,
            "exists": False,
            "size_ok": False,
            "size_actual_gb": 0.0,
            "size_expected_gb": spec.size_gb,
            "hash_ok": None,
            "status": "missing",
        }

    size_bytes = spec.local_path.stat().st_size
    actual_gb = size_bytes / (1024**3)
    size_ok = _size_is_plausible(actual_gb=actual_gb, expected_gb=spec.size_gb)

    hash_ok = None
    if spec.sha256:
        computed = await asyncio.to_thread(_sha256_file, spec.local_path)
        hash_ok = computed.lower() == spec.sha256.lower()

    if not size_ok:
        status = "size_mismatch"
    elif hash_ok is False:
        status = "corrupted"
    else:
        status = "ok"

    return {
        "model_id": spec.id,
        "exists": True,
        "size_ok": size_ok,
        "size_actual_gb": round(actual_gb, 4),
        "size_expected_gb": spec.size_gb,
        "hash_ok": hash_ok,
        "status": status,
    }


async def verify_all_models(
    model_ids: list[str] | None = None,
    *,
    registry: dict[str, ModelSpec] | None = None,
) -> list[dict[str, Any]]:
    models = registry or build_model_registry()
    specs = [models[model_id] for model_id in (model_ids or list(models.keys())) if model_id in models]
    return await asyncio.gather(*(verify_model_integrity(spec) for spec in specs))


def _sha256_file(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            sha.update(chunk)
    return sha.hexdigest()


def _size_is_plausible(*, actual_gb: float, expected_gb: float) -> bool:
    if expected_gb <= 0:
        return actual_gb > 0
    tolerance = 0.10
    lower = expected_gb * (1 - tolerance)
    upper = expected_gb * (1 + tolerance)
    return lower <= actual_gb <= upper
