from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
TASK_NAME = "AtnosApiWatchdog"
WATCHDOG_CMD = ROOT / "scripts" / "start_atnos_api_watchdog.cmd"
HEALTH_URL = "http://127.0.0.1:8000/health"
WATCHDOG_LOCK_DIR = ROOT / ".codex_live_api_8000.watchdog.lock"
WATCHDOG_LOCK_PID = WATCHDOG_LOCK_DIR / "pid"
WATCHDOG_STDOUT_LOG = ROOT / ".codex_live_api_8000.watchdog.log"
WATCHDOG_STDERR_LOG = ROOT / ".codex_live_api_8000.watchdog.err.log"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
WATCHDOG_SCRIPT = ROOT / "scripts" / "atnos_api_watchdog.py"


def run_schtasks(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["schtasks", *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def install_task() -> int:
    if not WATCHDOG_CMD.exists():
        print(f"Watchdog launcher not found: {WATCHDOG_CMD}", file=sys.stderr)
        return 2
    action = f'"{WATCHDOG_CMD}"'
    result = run_schtasks(
        [
            "/Create",
            "/TN",
            TASK_NAME,
            "/SC",
            "ONLOGON",
            "/TR",
            action,
            "/RL",
            "LIMITED",
            "/F",
        ]
    )
    print_output(result)
    if result.returncode == 0:
        return 0
    print(
        "Task Scheduler install failed; starting a non-persistent watchdog without writing to C:.",
        file=sys.stderr,
    )
    process = launch_watchdog_loop()
    print(f"Started non-persistent watchdog: PID {process.pid}")
    return 0


def uninstall_task() -> int:
    result = run_schtasks(["/Delete", "/TN", TASK_NAME, "/F"])
    print_output(result)
    return 0 if result.returncode == 0 else result.returncode


def status_task() -> int:
    result = run_schtasks(["/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST"])
    if result.returncode == 0:
        print("Task Scheduler: installed")
        print_output(result)
    else:
        reason = (result.stderr or result.stdout).strip() or "not found"
        print(f"Task Scheduler: unavailable ({reason})")
    pids = watchdog_pids()
    print(f"Watchdog loop: {'running' if pids else 'not running'}")
    if pids:
        lock_pid = read_watchdog_lock_pid()
        family = process_family_pids(lock_pid, pids)
        extras = [pid for pid in pids if pid not in family]
        if lock_pid is not None:
            print(f"Watchdog lock PID: {lock_pid}")
        if family:
            print(f"Watchdog process family: {', '.join(str(pid) for pid in sorted(family))}")
        else:
            print(f"Watchdog PID(s): {', '.join(str(pid) for pid in pids)}")
        if extras:
            print(f"Watchdog duplicate PID(s): {', '.join(str(pid) for pid in extras)}")
    print(f"API health: {api_health_status()}")
    return result.returncode


def start_task() -> int:
    lock_pid = read_watchdog_lock_pid()
    pids = watchdog_pids()
    if lock_pid is not None and lock_pid in pids and process_exists(lock_pid):
        cleanup_watchdog_duplicates(lock_pid)
        print(f"Watchdog already running: PID {lock_pid}")
        return 0

    cleanup_watchdog_duplicates(None)
    result = run_schtasks(["/Run", "/TN", TASK_NAME])
    print_output(result)
    if result.returncode == 0:
        return 0
    process = launch_watchdog_loop()
    print(f"Started non-persistent watchdog: PID {process.pid}")
    return 0


def launch_watchdog_loop() -> subprocess.Popen[bytes]:
    if not PYTHON.exists():
        raise RuntimeError(f"Python runtime not found: {PYTHON}")
    if not WATCHDOG_SCRIPT.exists():
        raise RuntimeError(f"Watchdog script not found: {WATCHDOG_SCRIPT}")
    WATCHDOG_STDOUT_LOG.parent.mkdir(parents=True, exist_ok=True)
    stdout = WATCHDOG_STDOUT_LOG.open("ab")
    stderr = WATCHDOG_STDERR_LOG.open("ab")
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    return subprocess.Popen(
        [
            str(PYTHON),
            str(WATCHDOG_SCRIPT),
            "--interval",
            "60",
            "--startup-wait",
            "30",
        ],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=stderr,
        creationflags=creationflags,
        close_fds=True,
    )


def watchdog_pids() -> list[int]:
    script = (
        "$ErrorActionPreference='SilentlyContinue'; "
        "Get-CimInstance Win32_Process | "
        "Where-Object { "
        "$_.Name -like 'python*' -and "
        "$_.CommandLine -like '*atnos_api_watchdog.py*' "
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
            pids.append(int(line))
        except ValueError:
            continue
    return sorted(set(pids))


def read_watchdog_lock_pid() -> int | None:
    try:
        value = WATCHDOG_LOCK_PID.read_text(encoding="utf-8").strip()
        return int(value) if value else None
    except (OSError, ValueError):
        return None


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


def stop_pid(pid: int, *, grace_s: float = 3.0) -> None:
    # A PID file or basename match is not ownership evidence.
    import importlib.util
    import psutil
    guard_path = Path(__file__).resolve().parents[3] / "scripts/runtime_process_safety.py"
    spec = importlib.util.spec_from_file_location("atnos_watchdog_process_safety", guard_path)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    try:
        info = guard.identity(psutil.Process(pid))
        if info['role'] != 'legacy_watchdog':
            return
        guard.stop_owned(pid, info['created'])
    except (psutil.Error, OSError, RuntimeError):
        return


def cleanup_watchdog_duplicates(keep_pid: int | None) -> list[int]:
    pids = watchdog_pids()
    keep_family = process_family_pids(keep_pid, pids)
    stopped: list[int] = []
    for pid in pids:
        if pid in keep_family:
            continue
        stop_pid(pid)
        stopped.append(pid)
    return stopped


def api_health_status() -> str:
    try:
        with urlopen(HEALTH_URL, timeout=3) as response:
            return "ok" if 200 <= int(response.status) < 300 else f"http_{response.status}"
    except (OSError, URLError, TimeoutError):
        return "unreachable"


def print_output(result: subprocess.CompletedProcess[str]) -> None:
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install/manage Atnos API watchdog Windows scheduled task.")
    parser.add_argument("action", choices=["install", "uninstall", "status", "start"])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.action == "install":
        return install_task()
    if args.action == "uninstall":
        return uninstall_task()
    if args.action == "status":
        return status_task()
    if args.action == "start":
        return start_task()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
