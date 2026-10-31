"""Local Brain storage policy — weights outside git; reports under App/reports."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

ATNOS_ROOT = Path(r"E:\Atnos")
APP_ROOT = ATNOS_ROOT / "App"
MODELS_ROOT = ATNOS_ROOT / "Models"
MODEL_CACHE_ROOT = ATNOS_ROOT / "ModelCache"
REPORTS_ROOT = APP_ROOT / "reports" / "local_brain"

FAMILY_DIRS = ("qwen", "deepseek", "other")

# Leave headroom on E: after a download (bytes).
DEFAULT_MIN_REMAINING_FREE_BYTES = 40 * 1024 * 1024 * 1024  # 40 GiB


@dataclass(frozen=True, slots=True)
class DiskProbe:
    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int

    def to_dict(self) -> dict[str, int | str | float]:
        return {
            "path": self.path,
            "total_bytes": self.total_bytes,
            "used_bytes": self.used_bytes,
            "free_bytes": self.free_bytes,
            "total_gb": round(self.total_bytes / (1024**3), 2),
            "free_gb": round(self.free_bytes / (1024**3), 2),
        }


@dataclass(frozen=True, slots=True)
class DownloadGateResult:
    allowed: bool
    reason: str
    required_size_bytes: int
    free_bytes: int
    minimum_remaining_free_bytes: int
    target_dir: str

    def to_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "required_size_bytes": self.required_size_bytes,
            "free_bytes": self.free_bytes,
            "minimum_remaining_free_bytes": self.minimum_remaining_free_bytes,
            "target_dir": self.target_dir,
        }


def ensure_storage_layout() -> dict[str, str]:
    """Create Model / cache / report directories on E: (idempotent)."""
    created: dict[str, str] = {}
    for path in (
        MODELS_ROOT,
        *(MODELS_ROOT / family for family in FAMILY_DIRS),
        MODEL_CACHE_ROOT,
        REPORTS_ROOT,
    ):
        path.mkdir(parents=True, exist_ok=True)
        created[str(path)] = "ok"
    # Keep weights out of git via marker note (not a .gitignore inside Models — App git doesn't track E:\Atnos\Models).
    readme = MODELS_ROOT / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Atnos Models\n\n"
            "Large model weights live here, **outside** the App git tree.\n\n"
            "- `qwen/`, `deepseek/`, `other/` — staged weights\n"
            "- Cache: `E:\\Atnos\\ModelCache`\n"
            "- Benchmark reports: `E:\\Atnos\\App\\reports\\local_brain`\n",
            encoding="utf-8",
        )
    return created


def probe_disk(path: Path | None = None) -> DiskProbe:
    target = Path(path) if path is not None else MODELS_ROOT
    target.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(str(target))
    return DiskProbe(
        path=str(target),
        total_bytes=int(usage.total),
        used_bytes=int(usage.used),
        free_bytes=int(usage.free),
    )


def family_dir(family: str) -> Path:
    key = family.strip().lower()
    if key not in FAMILY_DIRS:
        key = "other"
    path = MODELS_ROOT / key
    path.mkdir(parents=True, exist_ok=True)
    return path


def gate_download(
    *,
    required_size_bytes: int,
    family: str = "other",
    minimum_remaining_free_bytes: int = DEFAULT_MIN_REMAINING_FREE_BYTES,
) -> DownloadGateResult:
    """Pre-download check — never assume E: has enough space."""
    if required_size_bytes < 0:
        raise ValueError("required_size_bytes must be >= 0")
    disk = probe_disk(MODELS_ROOT)
    target = family_dir(family)
    need = required_size_bytes + minimum_remaining_free_bytes
    if disk.free_bytes < need:
        return DownloadGateResult(
            allowed=False,
            reason=(
                f"Insufficient free space on {disk.path}: free={disk.free_bytes} "
                f"required={required_size_bytes}+reserve={minimum_remaining_free_bytes}"
            ),
            required_size_bytes=required_size_bytes,
            free_bytes=disk.free_bytes,
            minimum_remaining_free_bytes=minimum_remaining_free_bytes,
            target_dir=str(target),
        )
    return DownloadGateResult(
        allowed=True,
        reason="ok",
        required_size_bytes=required_size_bytes,
        free_bytes=disk.free_bytes,
        minimum_remaining_free_bytes=minimum_remaining_free_bytes,
        target_dir=str(target),
    )
