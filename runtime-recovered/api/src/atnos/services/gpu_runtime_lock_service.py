from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from time import monotonic, sleep, time
from uuid import uuid4

import psutil

from atnos.core.config import Settings


@dataclass(frozen=True, slots=True)
class GpuRuntimeLease:
    owner: str
    token: str
    lock_dir: Path


class GpuRuntimeLockService:
    """Process-safe lock for heavy local GPU runtimes: Ollama, F5-TTS and ComfyUI."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.lock_dir = (settings.data_dir / "runtime" / "gpu-runtime.lock").resolve()

    def acquire(self, *, owner: str, timeout_s: float = 600.0, stale_after_s: float = 900.0) -> GpuRuntimeLease:
        token = uuid4().hex
        deadline = monotonic() + max(0.0, timeout_s)
        while True:
            try:
                self.lock_dir.mkdir(parents=True, exist_ok=False)
                payload = {
                    "owner": owner,
                    "token": token,
                    "created_at_epoch": time(),
                    "process_id": os.getpid(),
                    "process_started_at_epoch": psutil.Process(os.getpid()).create_time(),
                }
                (self.lock_dir / "owner.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                return GpuRuntimeLease(owner=owner, token=token, lock_dir=self.lock_dir)
            except FileExistsError:
                self._clear_stale_lock(stale_after_s=stale_after_s)
                if monotonic() >= deadline:
                    raise TimeoutError(f"GPU runtime lock is busy: {self._current_owner_summary()}")
                sleep(0.5)

    def release(self, lease: GpuRuntimeLease) -> None:
        if lease.lock_dir != self.lock_dir or not self.lock_dir.exists():
            return
        owner_path = self.lock_dir / "owner.json"
        try:
            payload = json.loads(owner_path.read_text(encoding="utf-8")) if owner_path.exists() else {}
        except (OSError, json.JSONDecodeError):
            payload = {}
        if payload.get("token") != lease.token:
            return
        shutil.rmtree(self.lock_dir, ignore_errors=True)

    def _clear_stale_lock(self, *, stale_after_s: float) -> None:
        owner_path = self.lock_dir / "owner.json"
        try:
            payload = json.loads(owner_path.read_text(encoding="utf-8")) if owner_path.exists() else {}
            created_at = float(payload.get("created_at_epoch") or 0)
        except (OSError, ValueError, json.JSONDecodeError):
            payload = {}
            created_at = 0.0
        if created_at <= 0:
            try:
                created_at = self.lock_dir.stat().st_mtime
            except OSError:
                created_at = 0.0
        owner_alive = self._owner_process_is_alive(payload)
        if owner_alive is True:
            # A long render is not a stale render. Age is only a fallback when
            # the owner process cannot be identified reliably.
            return
        if owner_alive is False:
            shutil.rmtree(self.lock_dir, ignore_errors=True)
            return
        if owner_alive is None:
            try:
                process_id = int(payload.get("process_id") or 0)
            except (TypeError, ValueError):
                process_id = 0
            if process_id > 0:
                # A recorded PID that cannot be inspected (for example due to
                # AccessDenied) is safer to preserve than to run two GPU jobs.
                return
        if created_at <= 0 or (time() - created_at) < stale_after_s:
            return
        shutil.rmtree(self.lock_dir, ignore_errors=True)

    @staticmethod
    def _owner_process_is_alive(payload: dict[str, object]) -> bool | None:
        try:
            process_id = int(payload.get("process_id") or 0)
        except (TypeError, ValueError):
            return None
        if process_id <= 0:
            return None
        try:
            process = psutil.Process(process_id)
            expected_started_at = float(payload.get("process_started_at_epoch") or 0)
            if expected_started_at > 0 and abs(process.create_time() - expected_started_at) > 1.0:
                return False
            return process.is_running()
        except psutil.AccessDenied:
            return None
        except (psutil.NoSuchProcess, ValueError):
            return False

    def _current_owner_summary(self) -> str:
        owner_path = self.lock_dir / "owner.json"
        try:
            payload = json.loads(owner_path.read_text(encoding="utf-8")) if owner_path.exists() else {}
        except (OSError, json.JSONDecodeError):
            return str(self.lock_dir)
        owner = str(payload.get("owner") or self.lock_dir)
        created_at = payload.get("created_at_epoch")
        return f"{owner} since {created_at}"

    def snapshot(self) -> dict[str, object]:
        """Structured GPU lock state for media observability (no secrets)."""
        owner_path = self.lock_dir / "owner.json"
        if not self.lock_dir.exists() or not owner_path.is_file():
            return {
                "locked": False,
                "owner": None,
                "process_id": None,
                "created_at_epoch": None,
                "duration_s": None,
                "owner_alive": None,
                "summary": "unlocked",
            }
        try:
            payload = json.loads(owner_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {
                "locked": True,
                "owner": "unknown",
                "process_id": None,
                "created_at_epoch": None,
                "duration_s": None,
                "owner_alive": None,
                "summary": "lock present but unreadable",
            }
        created = float(payload.get("created_at_epoch") or 0) or None
        duration = round(time() - created, 1) if created else None
        alive = self._owner_process_is_alive(payload)
        owner = str(payload.get("owner") or "unknown")
        return {
            "locked": True,
            "owner": owner,
            "process_id": payload.get("process_id"),
            "created_at_epoch": created,
            "duration_s": duration,
            "owner_alive": alive,
            "summary": f"{owner} for {duration}s" if duration is not None else owner,
        }
