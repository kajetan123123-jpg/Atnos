from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_comfy_launcher_uses_managed_only_before_portable_fallback() -> None:
    launcher = (ROOT / "Start-ComfyUI.ps1").read_text(encoding="utf-8")

    assert "mode=MANAGED_ONLY" in launcher
    assert "ComfyUI-Installs\\Atnos" in launcher
    assert "--disable-all-custom-nodes" in launcher
    assert "--lowvram" in launcher
    assert "--reserve-vram" in launcher
    assert "comfy-shared-model-paths.yaml" in launcher
    assert "Test-ComfyFullReady" in launcher
    assert "comfyui-startup.log" in launcher
    assert "comfyui-managed.instance.json" in launcher
    assert "AllowDesktopGui" in launcher
    # Desktop GUI must not be the default start path
    desktop_block = launcher.index("if ($AllowDesktopGui)")
    managed_start = launcher.index("Starting managed python")
    assert managed_start > 0
    assert "Start-Process -FilePath $desktopExe" in launcher[desktop_block : desktop_block + 400]
    portable_guard = launcher.index("if (-not $PortableFallback)")
    assert portable_guard > managed_start


def test_comfy_launcher_requires_explicit_switch_for_legacy_portable_runtime() -> None:
    launcher = (ROOT / "Start-ComfyUI.ps1").read_text(encoding="utf-8")

    assert "[switch]$PortableFallback" in launcher
    assert "if (-not $PortableFallback)" in launcher
    assert "The explicit portable fallback is incomplete." in launcher


def test_comfy_stop_rejects_force_unknown():
    import subprocess
    result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'Stop-ComfyUI.ps1'), '-ForceUnknown'], capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert result.returncode != 0
    assert 'Stopping unknown processes is disabled' in result.stderr
