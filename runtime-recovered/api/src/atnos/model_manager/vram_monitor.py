from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from typing import Any


async def get_vram_info() -> dict[str, float | str]:
    rocm_json = await asyncio.to_thread(_probe_rocm_json)
    if rocm_json is not None:
        return rocm_json

    nvidia = await asyncio.to_thread(_probe_nvidia_smi)
    if nvidia is not None:
        return nvidia

    rocm_text = await asyncio.to_thread(_probe_rocm_text)
    if rocm_text is not None:
        return rocm_text

    return {
        "total_gb": 16.0,
        "used_gb": 0.0,
        "free_gb": 16.0,
        "utilization_pct": 0.0,
        "source": "assumed",
    }


async def check_vram_sufficient(required_gb: float) -> bool:
    if required_gb <= 0:
        return True
    info = await get_vram_info()
    return float(info["free_gb"]) >= required_gb * 1.1


async def wait_for_vram(
    required_gb: float,
    *,
    timeout_s: float = 120,
    check_interval_s: float = 5,
) -> bool:
    elapsed = 0.0
    while elapsed < timeout_s:
        if await check_vram_sufficient(required_gb):
            return True
        await asyncio.sleep(check_interval_s)
        elapsed += check_interval_s
    return False


def _probe_rocm_json() -> dict[str, float | str] | None:
    if shutil.which("rocm-smi") is None:
        return None
    try:
        proc = subprocess.run(
            ["rocm-smi", "--showmeminfo", "vram", "--json"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except Exception:
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    for value in payload.values():
        if not isinstance(value, dict):
            continue
        total = _first_number(value, "VRAM Total Memory")
        used = _first_number(value, "VRAM Total Used Memory")
        if total is not None and used is not None:
            return _format_vram(total_bytes=total, used_bytes=used, source="rocm-smi")
    return None


def _probe_nvidia_smi() -> dict[str, float | str] | None:
    if shutil.which("nvidia-smi") is None:
        return None
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.total,memory.used,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except Exception:
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    first_line = proc.stdout.strip().splitlines()[0]
    try:
        values = [float(item.strip()) for item in first_line.split(",")[:3]]
    except ValueError:
        return None
    if len(values) != 3:
        return None
    total_mb, used_mb, free_mb = values
    total_gb = total_mb / 1024
    used_gb = used_mb / 1024
    free_gb = free_mb / 1024
    return {
        "total_gb": round(total_gb, 2),
        "used_gb": round(used_gb, 2),
        "free_gb": round(free_gb, 2),
        "utilization_pct": round((used_gb / total_gb) * 100, 1) if total_gb else 0.0,
        "source": "nvidia-smi",
    }


def _probe_rocm_text() -> dict[str, float | str] | None:
    if shutil.which("rocm-smi") is None:
        return None
    try:
        proc = subprocess.run(
            ["rocm-smi", "--showmeminfo", "vram"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    text = proc.stdout
    total_match = re.search(r"VRAM Total Memory.*?:\s*([0-9.]+)", text, re.IGNORECASE)
    used_match = re.search(r"VRAM Total Used Memory.*?:\s*([0-9.]+)", text, re.IGNORECASE)
    if total_match is None or used_match is None:
        return None
    total = float(total_match.group(1))
    used = float(used_match.group(1))
    return _format_vram(total_bytes=total, used_bytes=used, source="rocm-smi")


def _first_number(payload: dict[str, Any], key_fragment: str) -> float | None:
    for key, value in payload.items():
        if key_fragment.lower() in str(key).lower():
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def _format_vram(*, total_bytes: float, used_bytes: float, source: str) -> dict[str, float | str]:
    total_gb = total_bytes / (1024**3)
    used_gb = used_bytes / (1024**3)
    free_gb = max(0.0, total_gb - used_gb)
    return {
        "total_gb": round(total_gb, 2),
        "used_gb": round(used_gb, 2),
        "free_gb": round(free_gb, 2),
        "utilization_pct": round((used_gb / total_gb) * 100, 1) if total_gb else 0.0,
        "source": source,
    }
