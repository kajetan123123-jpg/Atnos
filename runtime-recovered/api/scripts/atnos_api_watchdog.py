from __future__ import annotations

import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
API_RUNNER = ROOT / "scripts" / "run_atnos_api.py"
PID_FILE = ROOT / ".codex_live_api_8000.pid"
WATCHDOG_LOCK_DIR = ROOT / ".codex_live_api_8000.watchdog.lock"
STDOUT_LOG = ROOT / ".codex_live_api_8000.log"
STDERR_LOG = ROOT / ".codex_live_api_8000.err.log"
HEALTH_URL = "http://127.0.0.1:8000/health/ready"


def api_environment() -> dict[str, str]:
    env = os.environ.copy()
    src_path = str(ROOT / "src")
    existing_pythonpath = env.get("PYTHONPATH", "")
    paths = [path for path in existing_pythonpath.split(os.pathsep) if path]
    if src_path not in paths:
        paths.insert(0, src_path)
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def is_healthy(timeout_s: float = 3.0) -> bool:
    try:
        with urlopen(HEALTH_URL, timeout=timeout_s) as response:
            if not (200 <= int(response.status) < 300):
                return False
            raw = response.read().decode("utf-8", errors="replace")
    except (OSError, URLError, TimeoutError, KeyboardInterrupt):
        return False
    try:
        import json

        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return True
    if isinstance(payload, dict) and "ready" in payload:
        return bool(payload.get("ready"))
    return True


def read_pid() -> int | None:
    try:
        value = PID_FILE.read_text(encoding="utf-8").strip()
        return int(value) if value else None
    except (OSError, ValueError):
        return None


def write_pid(pid: int) -> None:
    PID_FILE.write_text(str(pid), encoding="utf-8")


def listener_pid() -> int | None:
    if os.name != "nt":
        return None
    result = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        local_address = parts[1]
        state = parts[3].upper()
        pid_text = parts[4]
        if local_address.endswith(":8000") and state == "LISTENING":
            try:
                return int(pid_text)
            except ValueError:
                return None
    return None


def managed_api_pids() -> list[int]:
    if os.name != "nt":
        return []
    script = (
        "$ErrorActionPreference='SilentlyContinue'; "
        "Get-CimInstance Win32_Process | "
        "Where-Object { "
        "$_.Name -like 'python*' -and "
        "($_.CommandLine -like '*uvicorn*' -or $_.CommandLine -like '*run_atnos_api.py*') -and "
        "$_.CommandLine -like '*atnos.api.main:create_app*' -and "
        "$_.CommandLine -like '*--port*' -and "
        "$_.CommandLine -like '*8000*' "
        "} | ForEach-Object { $_.ProcessId }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    pids: list[int] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid = int(line)
        except ValueError:
            continue
        if pid > 0 and pid != os.getpid():
            pids.append(pid)
    return sorted(set(pids))


def process_parent_pid(pid: int) -> int | None:
    if os.name != "nt" or pid <= 0:
        return None
    script = (
        "$ErrorActionPreference='SilentlyContinue'; "
        f"Get-CimInstance Win32_Process -Filter \"ProcessId = {pid}\" | "
        "ForEach-Object { $_.ParentProcessId }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            return int(line)
        except ValueError:
            return None
    return None


def process_family_pids(leaf_pid: int | None, candidates: list[int]) -> set[int]:
    if leaf_pid is None:
        return set()
    candidate_set = set(candidates)
    family = {leaf_pid}
    current = leaf_pid
    while True:
        parent = process_parent_pid(current)
        if parent is None or parent not in candidate_set or parent in family:
            break
        family.add(parent)
        current = parent
    return family


def process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0 and f'"{pid}"' in result.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def stop_pid(pid: int, *, grace_s: float = 4.0, tree: bool = False) -> None:
    """Legacy watchdog cannot authorize termination by PID or port."""
    return None


def start_api() -> int:
    if not PYTHON.exists():
        raise RuntimeError(f"Python runtime not found: {PYTHON}")
    if not API_RUNNER.exists():
        raise RuntimeError(f"API runner not found: {API_RUNNER}")
    STDOUT_LOG.parent.mkdir(parents=True, exist_ok=True)
    stdout = STDOUT_LOG.open("ab")
    stderr = STDERR_LOG.open("ab")
    process = subprocess.Popen(
        [
            str(PYTHON),
            str(API_RUNNER),
            "--app",
            "atnos.api.main:create_app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ],
        cwd=str(ROOT),
        stdout=stdout,
        stderr=stderr,
        stdin=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        env=api_environment(),
    )
    PID_FILE.write_text(str(process.pid), encoding="utf-8")
    return int(process.pid)


def start_and_wait(*, startup_wait_s: float) -> dict[str, object]:
    new_pid = start_api()
    deadline = time.monotonic() + startup_wait_s
    while time.monotonic() < deadline:
        if is_healthy():
            actual_pid = listener_pid() or new_pid
            write_pid(actual_pid)
            return {"status": "restarted", "pid": actual_pid, "restarted": True}
        time.sleep(1.0)
    return {"status": "started_but_unhealthy", "pid": new_pid, "restarted": True}


def ensure_api(*, startup_wait_s: float = 20.0) -> dict[str, object]:
    if is_healthy():
        return {"status": "healthy", "pid": listener_pid(), "restarted": False}
    # One launcher owns startup/mutex/identity. Never restart models or kill a
    # listener from this legacy compatibility entrypoint.
    launcher = Path(__file__).resolve().parents[3] / "Launch-Atnos.ps1"
    result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(launcher), "-ServicesOnly", "-NoDesktopShell", "-NoErrorDialog"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=240, check=False)
    return {"status": "delegated" if result.returncode == 0 else "deferred", "restarted": False}


def run_loop(interval_s: float, startup_wait_s: float) -> None:
    with watchdog_lock():
        while True:
            result = ensure_api(startup_wait_s=startup_wait_s)
            print(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} | "
                f"status={result['status']} pid={result.get('pid')} restarted={result['restarted']}",
                flush=True,
            )
            time.sleep(interval_s)


@contextmanager
def watchdog_lock():
    lock_acquired = False
    try:
        try:
            WATCHDOG_LOCK_DIR.mkdir()
            lock_acquired = True
            (WATCHDOG_LOCK_DIR / "pid").write_text(str(os.getpid()), encoding="utf-8")
        except FileExistsError:
            lock_pid = _read_lock_pid()
            if lock_pid is not None and process_exists(lock_pid):
                print("Another Atnos API watchdog loop is already running; exiting.", flush=True)
                raise SystemExit(0)
            _remove_lock_dir()
            try:
                WATCHDOG_LOCK_DIR.mkdir()
            except FileExistsError:
                print("Another Atnos API watchdog loop acquired the lock first; exiting.", flush=True)
                raise SystemExit(0)
            lock_acquired = True
            (WATCHDOG_LOCK_DIR / "pid").write_text(str(os.getpid()), encoding="utf-8")
        yield
    finally:
        if lock_acquired:
            _remove_lock_dir()


def _read_lock_pid() -> int | None:
    try:
        return int((WATCHDOG_LOCK_DIR / "pid").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _remove_lock_dir() -> None:
    try:
        pid_file = WATCHDOG_LOCK_DIR / "pid"
        if pid_file.exists():
            pid_file.unlink()
        WATCHDOG_LOCK_DIR.rmdir()
    except OSError:
        pass


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Atnos API health watchdog for local runtime.")
    parser.add_argument("--once", action="store_true", help="Run one health check/restart cycle and exit.")
    parser.add_argument("--interval", type=float, default=60.0, help="Loop interval in seconds.")
    parser.add_argument("--startup-wait", type=float, default=25.0, help="Seconds to wait for API health after restart.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.once:
        with watchdog_lock():
            print(ensure_api(startup_wait_s=args.startup_wait), flush=True)
        return 0
    run_loop(interval_s=max(5.0, args.interval), startup_wait_s=args.startup_wait)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
