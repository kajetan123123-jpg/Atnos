from __future__ import annotations

import importlib.util
from pathlib import Path


def load_manager_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "manage_atnos_watchdog_task.py"
    spec = importlib.util.spec_from_file_location("manage_atnos_watchdog_task_for_tests", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_start_task_is_idempotent_when_lock_pid_is_running(monkeypatch, capsys):
    manager = load_manager_module()
    cleaned = []
    schtasks_calls = []

    monkeypatch.setattr(manager, "read_watchdog_lock_pid", lambda: 200)
    monkeypatch.setattr(manager, "watchdog_pids", lambda: [100, 200])
    monkeypatch.setattr(manager, "process_exists", lambda pid: pid == 200)
    monkeypatch.setattr(manager, "cleanup_watchdog_duplicates", lambda keep_pid: cleaned.append(keep_pid))
    monkeypatch.setattr(manager, "run_schtasks", lambda args: schtasks_calls.append(args))

    result = manager.start_task()

    assert result == 0
    assert cleaned == [200]
    assert schtasks_calls == []
    assert "Watchdog already running: PID 200" in capsys.readouterr().out


def test_start_task_cleans_stale_watchdogs_before_direct_start(monkeypatch):
    manager = load_manager_module()
    cleaned = []
    class FakeProcess:
        pid = 1234

    monkeypatch.setattr(manager, "read_watchdog_lock_pid", lambda: None)
    monkeypatch.setattr(manager, "watchdog_pids", lambda: [])
    monkeypatch.setattr(manager, "cleanup_watchdog_duplicates", lambda keep_pid: cleaned.append(keep_pid))
    monkeypatch.setattr(
        manager,
        "run_schtasks",
        lambda args: manager.subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="not installed"),
    )
    monkeypatch.setattr(manager, "launch_watchdog_loop", lambda: FakeProcess())

    result = manager.start_task()

    assert result == 0
    assert cleaned == [None]


def test_cleanup_watchdog_duplicates_preserves_lock_family(monkeypatch):
    manager = load_manager_module()
    stopped = []

    monkeypatch.setattr(manager, "watchdog_pids", lambda: [100, 200, 300])
    monkeypatch.setattr(manager, "process_parent_pid", lambda pid: 100 if pid == 200 else None)
    monkeypatch.setattr(manager, "stop_pid", lambda pid: stopped.append(pid))

    result = manager.cleanup_watchdog_duplicates(200)

    assert result == [300]
    assert stopped == [300]
