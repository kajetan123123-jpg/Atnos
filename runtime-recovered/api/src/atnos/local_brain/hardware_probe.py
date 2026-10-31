"""Read-only hardware / runtime availability probe for Local Brain benchmarks."""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from atnos.local_brain.storage import MODELS_ROOT, ensure_storage_layout, probe_disk


@dataclass(slots=True)
class HardwareProbeResult:
    timestamp: str
    os: str
    cpu: str | None
    ram_total_bytes: int | None
    ram_available_bytes: int | None
    gpu_name: str | None
    vram_total_bytes: int | None
    vram_free_bytes: int | None
    gpu_driver_version: str | None
    disk: dict[str, Any]
    runtimes: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_ps(command: str) -> str:
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    return (completed.stdout or "").strip()


def _probe_ram() -> tuple[int | None, int | None]:
    try:
        import psutil  # type: ignore

        vm = psutil.virtual_memory()
        return int(vm.total), int(vm.available)
    except Exception:
        pass
    out = _run_ps(
        "(Get-CimInstance Win32_OperatingSystem | "
        "Select-Object TotalVisibleMemorySize,FreePhysicalMemory | ConvertTo-Json -Compress)"
    )
    if not out:
        return None, None
    try:
        payload = json.loads(out)
        # Values are KiB
        total = int(payload.get("TotalVisibleMemorySize", 0)) * 1024
        free = int(payload.get("FreePhysicalMemory", 0)) * 1024
        return total or None, free or None
    except Exception:
        return None, None


def _probe_gpu() -> tuple[str | None, int | None, str | None, list[str]]:
    notes: list[str] = []
    out = _run_ps(
        "Get-CimInstance Win32_VideoController | "
        "Select-Object Name,AdapterRAM,DriverVersion | ConvertTo-Json -Compress"
    )
    if not out:
        return None, None, None, ["gpu_wmi_empty"]
    try:
        payload = json.loads(out)
        if isinstance(payload, list):
            # Prefer AMD Radeon entry when multiple adapters exist.
            amd = next((item for item in payload if "radeon" in str(item.get("Name", "")).lower()), None)
            payload = amd or payload[0]
        name = str(payload.get("Name") or "") or None
        driver = str(payload.get("DriverVersion") or "") or None
        adapter_ram = payload.get("AdapterRAM")
        vram = int(adapter_ram) if adapter_ram not in (None, "") else None
        # WMI AdapterRAM is often wrong/capped for >4GB GPUs — annotate.
        if name and "7600 XT" in name:
            notes.append("wmi_adapter_ram_unreliable_for_16gb; using declared_16gib_when_missing")
            if vram is None or vram < 8 * 1024**3:
                vram = 16 * 1024**3
                notes.append("vram_total_bytes_set_from_known_sku_rx7600xt_16gb")
        return name, vram, driver, notes
    except Exception as exc:
        return None, None, None, [f"gpu_parse_error:{exc}"]


def _probe_cpu() -> str | None:
    out = _run_ps("(Get-CimInstance Win32_Processor).Name")
    if out:
        return out.splitlines()[0].strip()
    return platform.processor() or None


def _tcp_open(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_json(url: str, timeout: float = 1.5) -> Any | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 — localhost probe
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def _which_many(names: list[str]) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _detect_runtimes() -> dict[str, Any]:
    """Detect availability without installing anything."""
    ollama_bin = _which_many(["ollama", "ollama.exe"])
    ollama_up = _tcp_open("127.0.0.1", 11434)
    ollama_tags = _http_json("http://127.0.0.1:11434/api/tags") if ollama_up else None
    installed = []
    if isinstance(ollama_tags, dict):
        for item in ollama_tags.get("models") or []:
            if isinstance(item, dict) and item.get("name"):
                installed.append(str(item["name"]))

    llama_bin = _which_many(
        [
            "llama-server",
            "llama-server.exe",
            "llama-cli",
            "llama-cli.exe",
        ]
    )
    # Common Atnos / Tools locations (read-only presence check).
    # Do not treat unrelated tools (e.g. whisper.cpp main.exe) as llama.cpp.
    candidate_roots = [
        Path(r"E:\Atnos\Tools"),
        Path(r"E:\Atnos\App\data\tool-caches"),
        Path(os.environ.get("LLAMA_CPP_DIR", "")),
    ]
    found_builds: list[str] = []
    for root in candidate_roots:
        if not root or not str(root) or not root.exists():
            continue
        for pattern in ("**/llama-server.exe", "**/llama-cli.exe", "**/llama.cpp/**/main.exe"):
            for hit in root.glob(pattern):
                path_l = str(hit).lower()
                if "whisper" in path_l:
                    continue
                found_builds.append(str(hit))
                if len(found_builds) >= 8:
                    break
            if len(found_builds) >= 8:
                break

    hip_env = bool(os.environ.get("HIP_PATH") or os.environ.get("ROCM_PATH"))
    vulkan_icd = bool(os.environ.get("VK_ICD_FILENAMES") or os.environ.get("VULKAN_SDK"))
    llama_present = bool(llama_bin or found_builds)

    return {
        "ollama": {
            "binary": ollama_bin,
            "port_11434_open": ollama_up,
            "installed_models": installed,
            "status": "reachable" if ollama_up else ("binary_only" if ollama_bin else "not_detected"),
        },
        "llama_cpp": {
            "binary_on_path": llama_bin,
            "found_builds": found_builds,
            "hip_rocm_env_present": hip_env,
            "vulkan_env_present": vulkan_icd,
            "note": (
                "HIP/ROCm vs Vulkan not auto-benchmarked here; "
                "prefer HIP builds for AMD when available. No install performed."
            ),
            "status": "present" if llama_present else "not_detected",
        },
    }


def collect_hardware_probe(*, vram_free_bytes: int | None = None) -> HardwareProbeResult:
    ensure_storage_layout()
    ram_total, ram_avail = _probe_ram()
    gpu_name, vram_total, driver, notes = _probe_gpu()
    disk = probe_disk(MODELS_ROOT).to_dict()
    # Free VRAM is not reliably available via WMI; leave None unless caller supplies.
    if vram_free_bytes is None:
        notes.append("vram_free_bytes_unknown_without_vendor_tool")
    return HardwareProbeResult(
        timestamp=_utcnow(),
        os=f"{platform.system()} {platform.release()} ({platform.version()})",
        cpu=_probe_cpu(),
        ram_total_bytes=ram_total,
        ram_available_bytes=ram_avail,
        gpu_name=gpu_name,
        vram_total_bytes=vram_total,
        vram_free_bytes=vram_free_bytes,
        gpu_driver_version=driver,
        disk=disk,
        runtimes=_detect_runtimes(),
        notes=notes,
    )


def write_hardware_probe(path: Path | None = None) -> Path:
    from atnos.local_brain.storage import REPORTS_ROOT

    REPORTS_ROOT.mkdir(parents=True, exist_ok=True)
    target = path or (REPORTS_ROOT / "hardware_probe_latest.json")
    payload = collect_hardware_probe().to_dict()
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target
