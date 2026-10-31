from __future__ import annotations

import ctypes
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from time import monotonic

import httpx
import psutil
from sqlalchemy.orm import Session

from atnos.core.config import Settings
from atnos.repositories.hardware_snapshot_repository import HardwareSnapshotRepository


class HardwareFeatureUnavailableError(RuntimeError):
    def __init__(self, feature_name: str, detail: str | None = None) -> None:
        self.feature_name = feature_name
        self.detail = (detail or "").strip() or None
        message = f"Feature '{feature_name}' is unavailable on this hardware."
        if self.detail:
            message = f"{message} {self.detail}"
        super().__init__(message)


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


@dataclass(frozen=True, slots=True)
class HardwareSnapshotView:
    free_vram_mb: int | None
    used_vram_mb: int | None
    total_vram_mb: int | None
    free_ram_mb: int | None
    total_ram_mb: int | None
    free_disk_gb: int | None
    disk_path: str | None
    status: str
    notes: str | None
    used_ram_mb: int | None = None
    cpu_percent: float | None = None
    gpu_name: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class HardwareGuardService:
    LACK_GPU_ENV = "LACK_GPU"

    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.snapshots = HardwareSnapshotRepository(session)

    @classmethod
    def is_gpu_forced_unavailable(cls) -> bool:
        return str(os.environ.get(cls.LACK_GPU_ENV, "")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    @classmethod
    def gpu_unavailable_reason(cls) -> str | None:
        if cls.is_gpu_forced_unavailable():
            return (
                f"GPU-dependent features are disabled because {cls.LACK_GPU_ENV}=1 was set "
                "for this runtime or test."
            )
        return None

    @classmethod
    def require_gpu(cls, feature_name: str, *, detail: str | None = None) -> None:
        reason = cls.gpu_unavailable_reason()
        if reason is None:
            return
        if detail:
            reason = f"{reason} {detail.strip()}"
        raise HardwareFeatureUnavailableError(feature_name, reason)

    def capture(
        self,
        *,
        snapshot_type: str = "runtime",
        vram_floor_gb: int = 4,
        persist: bool = True,
    ) -> HardwareSnapshotView:
        total_ram_mb, free_ram_mb, used_ram_mb = self._memory_snapshot()
        cpu_percent = self._cpu_snapshot()
        free_disk_gb = self._disk_snapshot(self.settings.app_root)
        total_vram_mb, used_vram_mb, free_vram_mb, gpu_name, note = self._vram_snapshot()
        status = "ready"
        notes: list[str] = []
        if note:
            notes.append(note)
        if free_vram_mb is not None and free_vram_mb < max(1, vram_floor_gb) * 1024:
            notes.append(f"Advisory only: free VRAM below {vram_floor_gb} GB floor.")
        if free_ram_mb is not None and free_ram_mb < 1024:
            notes.append("Advisory only: free RAM below 1 GB.")
        if free_disk_gb is not None and free_disk_gb < 2:
            notes.append("Advisory only: free disk below 2 GB.")

        if persist:
            self.snapshots.create(
                snapshot_type=snapshot_type,
                free_vram_mb=free_vram_mb,
                used_vram_mb=used_vram_mb,
                total_vram_mb=total_vram_mb,
                free_ram_mb=free_ram_mb,
                total_ram_mb=total_ram_mb,
                free_disk_gb=free_disk_gb,
                disk_path=str(self.settings.app_root),
                status=status,
                notes=" ".join(notes) or None,
            )
        return HardwareSnapshotView(
            free_vram_mb=free_vram_mb,
            used_vram_mb=used_vram_mb,
            total_vram_mb=total_vram_mb,
            free_ram_mb=free_ram_mb,
            total_ram_mb=total_ram_mb,
            free_disk_gb=free_disk_gb,
            disk_path=str(self.settings.app_root),
            status=status,
            notes=" ".join(notes) or None,
            used_ram_mb=used_ram_mb,
            cpu_percent=cpu_percent,
            gpu_name=gpu_name,
        )

    def latest(self) -> HardwareSnapshotView | None:
        item = self.snapshots.latest()
        if item is None:
            return None
        used_ram_mb = self._derive_used_ram_mb(item.total_ram_mb, item.free_ram_mb)
        return HardwareSnapshotView(
            free_vram_mb=item.free_vram_mb,
            used_vram_mb=item.used_vram_mb,
            total_vram_mb=item.total_vram_mb,
            free_ram_mb=item.free_ram_mb,
            total_ram_mb=item.total_ram_mb,
            free_disk_gb=item.free_disk_gb,
            disk_path=item.disk_path,
            status=item.status,
            notes=item.notes,
            used_ram_mb=used_ram_mb,
        )

    def release_gpu_pressure(self) -> dict[str, object]:
        # Observing the configured server must not execute a PATH CLI. On
        # Windows that CLI can auto-start the desktop app and its descendants
        # can inherit stdout pipes, leaving subprocess.communicate() blocked
        # even after the original CLI has exited.
        base_url = str(self.settings.ollama_base_url or "").strip().rstrip("/")
        try:
            endpoint = httpx.URL(base_url)
            if (endpoint.scheme not in {"http", "https"} or not endpoint.host
                    or endpoint.query or endpoint.fragment or endpoint.userinfo):
                raise ValueError("Invalid configured Ollama endpoint")
        except (httpx.InvalidURL, ValueError):
            return {
                "status": "failed", "message": "Configured Ollama endpoint is invalid; residency was not verified.",
                "stopped_models": [], "verified_released": False,
            }

        try:
            deadline = monotonic() + 6.0
            body = bytearray()
            with httpx.Client(
                timeout=httpx.Timeout(2.0), trust_env=False, follow_redirects=False,
            ) as client:
                with client.stream("GET", base_url + "/api/ps") as response:
                    response.raise_for_status()
                    for chunk in response.iter_bytes():
                        if monotonic() > deadline:
                            raise TimeoutError("Ollama residency probe exceeded its budget")
                        body.extend(chunk)
                        if len(body) > 262144:
                            raise ValueError("Ollama residency response is too large")
            payload = json.loads(body)
            rows = payload.get("models") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                raise ValueError("Invalid Ollama residency response")
            models: list[str] = []
            for row in rows:
                name = (row.get("name") or row.get("model")) if isinstance(row, dict) else None
                if not isinstance(name, str) or not name.strip():
                    raise ValueError("Invalid Ollama resident model")
                if name.strip() not in models:
                    models.append(name.strip())
        except (httpx.HTTPError, ValueError, TypeError, TimeoutError) as exc:
            return {
                "status": "failed",
                "message": f"Configured Ollama residency probe failed ({type(exc).__name__}); no model was unloaded.",
                "stopped_models": [], "verified_released": False,
            }

        if not models:
            return {
                "status": "noop",
                "message": "No active Ollama models were running.",
                "stopped_models": [],
                "active_models_before": [],
                "active_models_after": [],
                "verified_released": True,
            }

        # A resident model is not evidence of an idle model. Ollama's model
        # listing does not expose in-flight requests or exclude other clients;
        # even our GPU lease cannot prove that an external app is finished.
        # Preserve the runtime until an identity-bound drain protocol exists.
        return {
            "status": "deferred",
            "message": "Ollama models are resident; automatic unload is deferred because request ownership and idle state cannot be verified.",
            "stopped_models": [],
            "active_models_before": models,
            "active_models_after": None,
            "verified_released": False,
            "reason": "ollama_drain_protocol_unavailable",
        }

    @staticmethod
    def _memory_snapshot() -> tuple[int | None, int | None, int | None]:
        try:
            memory = psutil.virtual_memory()
            total_mb = int(memory.total / (1024 * 1024))
            free_mb = int(memory.available / (1024 * 1024))
            used_mb = HardwareGuardService._derive_used_ram_mb(total_mb, free_mb)
            return total_mb, free_mb, used_mb
        except Exception:
            pass

        try:
            memory = _MemoryStatusEx()
            memory.dwLength = ctypes.sizeof(_MemoryStatusEx)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory))
            total_mb = int(memory.ullTotalPhys / (1024 * 1024))
            free_mb = int(memory.ullAvailPhys / (1024 * 1024))
            used_mb = HardwareGuardService._derive_used_ram_mb(total_mb, free_mb)
            return total_mb, free_mb, used_mb
        except Exception:
            return None, None, None

    @staticmethod
    def _cpu_snapshot() -> float | None:
        try:
            return round(float(psutil.cpu_percent(interval=0.1)), 1)
        except Exception:
            return None

    @staticmethod
    def _disk_snapshot(root: Path) -> int | None:
        try:
            usage = shutil.disk_usage(root)
            return int(usage.free / (1024 * 1024 * 1024))
        except Exception:
            return None

    @staticmethod
    def _vram_snapshot() -> tuple[int | None, int | None, int | None, str | None, str | None]:
        registry_rows = HardwareGuardService._registry_gpu_rows()
        wmi_rows = HardwareGuardService._wmi_gpu_rows()
        gpu = HardwareGuardService._select_primary_gpu(registry_rows, wmi_rows)
        gpu_name = gpu["name"] if gpu is not None else None
        total_vram_mb = HardwareGuardService._to_mb(gpu["total_bytes"]) if gpu is not None else None
        used_vram_mb = HardwareGuardService._gpu_dedicated_usage_mb()

        notes: list[str] = []
        if total_vram_mb is None:
            notes.append("GPU total VRAM unavailable on this runtime.")
        if used_vram_mb is None:
            notes.append("Live dedicated VRAM usage unavailable on this runtime.")

        if total_vram_mb is not None and used_vram_mb is not None:
            used_vram_mb = min(used_vram_mb, total_vram_mb)
            free_vram_mb = max(0, total_vram_mb - used_vram_mb)
        else:
            free_vram_mb = None

        return total_vram_mb, used_vram_mb, free_vram_mb, gpu_name, " ".join(notes) or None

    @staticmethod
    def _wmi_gpu_rows() -> list[dict[str, object]]:
        try:
            payload = HardwareGuardService._run_powershell_json(
                "Get-CimInstance Win32_VideoController | "
                "Select-Object Name,PNPDeviceID,VideoProcessor,AdapterRAM | "
                "ConvertTo-Json -Compress",
                timeout=10,
            )
            rows: list[dict[str, object]] = []
            for item in HardwareGuardService._coerce_powershell_list(payload):
                rows.append(
                    {
                        "name": HardwareGuardService._normalize_optional_text(item.get("Name")),
                        "device_id": HardwareGuardService._normalize_device_id(item.get("PNPDeviceID")),
                        "video_processor": HardwareGuardService._normalize_optional_text(
                            item.get("VideoProcessor")
                        ),
                        "total_bytes": HardwareGuardService._coerce_positive_int(
                            item.get("AdapterRAM")
                        ),
                    }
                )
            return rows
        except Exception:
            return []

    @staticmethod
    def _registry_gpu_rows() -> list[dict[str, object]]:
        try:
            payload = HardwareGuardService._run_powershell_json(
                "Get-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Video\\*\\0000' | "
                "Select-Object DriverDesc,MatchingDeviceId,"
                "@{Name='QwMemorySize';Expression={$_.'HardwareInformation.qwMemorySize'}},"
                "@{Name='MemorySize';Expression={$_.'HardwareInformation.MemorySize'}} | "
                "ConvertTo-Json -Compress",
                timeout=12,
            )
            rows: list[dict[str, object]] = []
            for item in HardwareGuardService._coerce_powershell_list(payload):
                total_bytes = max(
                    (
                        value
                        for value in (
                            HardwareGuardService._coerce_positive_int(item.get("QwMemorySize")),
                            HardwareGuardService._coerce_positive_int(item.get("MemorySize")),
                        )
                        if value is not None
                    ),
                    default=None,
                )
                rows.append(
                    {
                        "name": HardwareGuardService._normalize_optional_text(item.get("DriverDesc")),
                        "device_id": HardwareGuardService._normalize_device_id(
                            item.get("MatchingDeviceId")
                        ),
                        "total_bytes": total_bytes,
                    }
                )
            return rows
        except Exception:
            return []

    @staticmethod
    def _gpu_dedicated_usage_mb() -> int | None:
        try:
            payload = HardwareGuardService._run_powershell_json(
                "Get-Counter '\\GPU Adapter Memory(*)\\Dedicated Usage' | "
                "Select-Object -ExpandProperty CounterSamples | "
                "Select-Object InstanceName,CookedValue | ConvertTo-Json -Compress",
                timeout=12,
            )
            usage_bytes = max(
                (
                    value
                    for item in HardwareGuardService._coerce_powershell_list(payload)
                    for value in [HardwareGuardService._coerce_positive_int(item.get("CookedValue"))]
                    if value is not None
                ),
                default=None,
            )
            return HardwareGuardService._to_mb(usage_bytes)
        except Exception:
            return None

    @staticmethod
    def _select_primary_gpu(
        registry_rows: list[dict[str, object]],
        wmi_rows: list[dict[str, object]],
    ) -> dict[str, object] | None:
        wmi_by_device: dict[str, dict[str, object]] = {}
        candidates: list[dict[str, object]] = []

        for row in wmi_rows:
            device_id = str(row.get("device_id") or "")
            if device_id:
                wmi_by_device[device_id] = row
            if HardwareGuardService._is_virtual_adapter(
                row.get("name"),
                row.get("device_id"),
                row.get("video_processor"),
            ):
                continue
            total_bytes = HardwareGuardService._coerce_positive_int(row.get("total_bytes"))
            candidates.append(
                {
                    "name": row.get("name"),
                    "device_id": row.get("device_id"),
                    "total_bytes": total_bytes,
                    "priority": 1,
                }
            )

        for row in registry_rows:
            if HardwareGuardService._is_virtual_adapter(row.get("name"), row.get("device_id")):
                continue
            device_id = str(row.get("device_id") or "")
            matched_wmi = wmi_by_device.get(device_id)
            total_bytes = HardwareGuardService._coerce_positive_int(row.get("total_bytes"))
            if total_bytes is None and matched_wmi is not None:
                total_bytes = HardwareGuardService._coerce_positive_int(
                    matched_wmi.get("total_bytes")
                )
            candidates.append(
                {
                    "name": row.get("name") or (matched_wmi.get("name") if matched_wmi is not None else None),
                    "device_id": row.get("device_id") or (matched_wmi.get("device_id") if matched_wmi is not None else None),
                    "total_bytes": total_bytes,
                    "priority": 2,
                }
            )

        filtered = [item for item in candidates if item.get("total_bytes") is not None]
        if not filtered:
            return None
        return max(
            filtered,
            key=lambda item: (
                int(item.get("priority") or 0),
                int(item.get("total_bytes") or 0),
            ),
        )

    @staticmethod
    def _is_virtual_adapter(*parts: object) -> bool:
        haystack = " ".join(str(part or "") for part in parts).strip().lower()
        if not haystack:
            return True
        return any(
            marker in haystack
            for marker in (
                "virtual display",
                "root\\display",
                "mttvdd",
                "basic render",
                "indirect display",
                "displaylink",
                "parsec",
            )
        )

    @staticmethod
    def _run_powershell_json(command: str, *, timeout: int) -> object | None:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        return json.loads(result.stdout)

    @staticmethod
    def _coerce_powershell_list(payload: object) -> list[dict[str, object]]:
        if payload is None:
            return []
        if isinstance(payload, dict):
            return [payload]
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    @staticmethod
    def _normalize_optional_text(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        return cleaned or None

    @staticmethod
    def _normalize_device_id(value: object) -> str | None:
        normalized = HardwareGuardService._normalize_optional_text(value)
        if normalized is None:
            return None
        parts = [part for part in normalized.upper().split("\\") if part]
        if len(parts) >= 2:
            return "\\".join(parts[:2])
        return normalized.upper()

    @staticmethod
    def _coerce_positive_int(value: object) -> int | None:
        if value is None:
            return None
        try:
            parsed = int(float(value))
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @staticmethod
    def _to_mb(value_bytes: int | None) -> int | None:
        if value_bytes is None:
            return None
        return max(1, int(value_bytes / (1024 * 1024)))

    @staticmethod
    def _derive_used_ram_mb(total_ram_mb: int | None, free_ram_mb: int | None) -> int | None:
        if total_ram_mb is None or free_ram_mb is None:
            return None
        return max(0, int(total_ram_mb) - int(free_ram_mb))

    @staticmethod
    def _parse_ollama_ps_output(output: str) -> list[str]:
        lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
        if not lines:
            return []
        parsed: list[str] = []
        for line in lines[1:]:
            if re.match(r"^-+$", line):
                continue
            name = line.split()[0].strip()
            if name and name.lower() not in {"name", "model"}:
                parsed.append(name)
        unique: list[str] = []
        seen: set[str] = set()
        for item in parsed:
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        return unique
