"""Shared, fail-closed checks for local model lifecycle operations."""
import os
from pathlib import Path
import psutil

from atnos.services.gpu_runtime_lock_service import GpuRuntimeLockService


def gpu_stop_allowed(settings) -> bool:
    lock_dir = settings.data_dir / "runtime/gpu-runtime.lock"
    if not lock_dir.exists():
        return True
    if not (lock_dir / "owner.json").is_file():
        return False
    try:
        snapshot = GpuRuntimeLockService(settings).snapshot()
        return snapshot["owner_alive"] is False or (
            snapshot["owner_alive"] is True and snapshot["process_id"] == os.getpid()
        )
    except (OSError, ValueError, TypeError):
        return False


def matching_python_processes(*, python: Path | None, script: Path | None, cwd: Path | None,
                              module: str | None = None):
    """Resolve only the entrypoint argument. A path in later args is not proof."""
    if python is None or cwd is None:
        return []
    matches = []
    for process in psutil.process_iter():
        try:
            argv = process.cmdline()
            if len(argv) < 2 or Path(argv[0]).resolve() != python.resolve():
                continue
            working_dir = Path(process.cwd()).resolve()
            if working_dir != cwd.resolve():
                continue
            # Our Comfy launcher uses python -s main.py. Only known, argument-free
            # interpreter switches may precede the entrypoint. Never scan later
            # arguments for a path: python -c ... main.py is not ownership.
            entry = 1
            while entry < len(argv) and argv[entry] in {'-s', '-u', '-B', '-E', '-I', '-S', '-O', '-OO'}:
                entry += 1
            if script and entry < len(argv) and not argv[entry].startswith('-'):
                matched = (working_dir / argv[entry]).resolve() == script.resolve()
            else:
                matched = bool(module and argv[entry:entry + 2] == ['-m', module])
            if matched:
                process.create_time()  # materialize psutil's PID-reuse identity
                matches.append(process)
        except (OSError, psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return matches
