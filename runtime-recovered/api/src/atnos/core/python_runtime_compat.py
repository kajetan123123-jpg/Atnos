from __future__ import annotations

import os
import platform
import sys

_PLATFORM_MACHINE_PATCHED = False


def should_patch_platform_machine() -> bool:
    return sys.platform.startswith("win") and sys.version_info >= (3, 14)


def _resolve_windows_machine() -> str:
    return (
        os.environ.get("PROCESSOR_ARCHITEW6432")
        or os.environ.get("PROCESSOR_ARCHITECTURE")
        or "AMD64"
    )


def apply_runtime_compatibility_workarounds() -> bool:
    global _PLATFORM_MACHINE_PATCHED

    if _PLATFORM_MACHINE_PATCHED or not should_patch_platform_machine():
        return _PLATFORM_MACHINE_PATCHED

    def _safe_platform_machine() -> str:
        return _resolve_windows_machine()

    platform.machine = _safe_platform_machine
    _PLATFORM_MACHINE_PATCHED = True
    return True
