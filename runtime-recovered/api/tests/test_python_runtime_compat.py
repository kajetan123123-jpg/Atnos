from __future__ import annotations

import platform

from atnos.core.python_runtime_compat import (
    apply_runtime_compatibility_workarounds,
    should_patch_platform_machine,
)


def test_platform_machine_workaround_is_idempotent_and_env_driven(monkeypatch) -> None:
    monkeypatch.setenv("PROCESSOR_ARCHITECTURE", "AMD64")
    monkeypatch.delenv("PROCESSOR_ARCHITEW6432", raising=False)

    patched_once = apply_runtime_compatibility_workarounds()
    patched_twice = apply_runtime_compatibility_workarounds()

    if should_patch_platform_machine():
        assert patched_once is True
        assert patched_twice is True
        assert platform.machine() == "AMD64"
    else:
        assert patched_once is False
        assert patched_twice is False


def test_platform_machine_prefers_wow64_architecture(monkeypatch) -> None:
    monkeypatch.setenv("PROCESSOR_ARCHITECTURE", "x86")
    monkeypatch.setenv("PROCESSOR_ARCHITEW6432", "AMD64")

    apply_runtime_compatibility_workarounds()

    if should_patch_platform_machine():
        assert platform.machine() == "AMD64"
