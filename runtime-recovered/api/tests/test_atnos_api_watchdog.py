from __future__ import annotations

import importlib.util
from pathlib import Path


def load_watchdog_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "atnos_api_watchdog.py"
    spec = importlib.util.spec_from_file_location("atnos_api_watchdog_for_tests", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_api_environment_prepends_src_to_pythonpath(monkeypatch):
    watchdog = load_watchdog_module()
    monkeypatch.setenv("PYTHONPATH", "existing_path")

    env = watchdog.api_environment()
    paths = env["PYTHONPATH"].split(watchdog.os.pathsep)

    assert paths[0] == str(watchdog.ROOT / "src")
    assert "existing_path" in paths
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONUNBUFFERED"] == "1"


def test_start_api_passes_runtime_environment(monkeypatch, tmp_path):
    watchdog = load_watchdog_module()
    fake_python = tmp_path / "python.exe"
    fake_python.write_text("", encoding="utf-8")
    fake_runner = tmp_path / "run_atnos_api.py"
    fake_runner.write_text("", encoding="utf-8")
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    pid_file = tmp_path / "api.pid"
    captured = {}

    class FakeProcess:
        pid = 4242

    def fake_popen(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(watchdog, "PYTHON", fake_python)
    monkeypatch.setattr(watchdog, "API_RUNNER", fake_runner)
    monkeypatch.setattr(watchdog, "STDOUT_LOG", stdout_log)
    monkeypatch.setattr(watchdog, "STDERR_LOG", stderr_log)
    monkeypatch.setattr(watchdog, "PID_FILE", pid_file)
    monkeypatch.setattr(watchdog.subprocess, "Popen", fake_popen)

    pid = watchdog.start_api()

    assert pid == 4242
    assert captured["kwargs"]["env"]["PYTHONPATH"].split(watchdog.os.pathsep)[0] == str(watchdog.ROOT / "src")
    assert "atnos.api.main:create_app" in captured["args"][0]
    assert pid_file.read_text(encoding="utf-8") == "4242"


def test_windows_process_exists_uses_tasklist(monkeypatch):
    watchdog = load_watchdog_module()

    def fake_run(*_args, **_kwargs):
        return watchdog.subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='"python.exe","123","Console","1","10,000 K"\n',
            stderr="",
        )

    monkeypatch.setattr(watchdog.os, "name", "nt")
    monkeypatch.setattr(watchdog.subprocess, "run", fake_run)

    assert watchdog.process_exists(123) is True
    assert watchdog.process_exists(456) is False


def test_legacy_watchdog_delegates_without_killing_any_pid(monkeypatch):
    from types import SimpleNamespace
    watchdog = load_watchdog_module()
    calls = []
    monkeypatch.setattr(watchdog, "is_healthy", lambda: False)
    monkeypatch.setattr(watchdog, "stop_pid", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Stop forbidden")))
    monkeypatch.setattr(watchdog.subprocess, "run", lambda command, **kwargs: calls.append(command) or SimpleNamespace(returncode=0))
    result = watchdog.ensure_api()
    assert result['status'] == 'delegated' and result['restarted'] is False
    assert len(calls) == 1 and '-ServicesOnly' in calls[0] and '-StartModels' not in calls[0]
    assert Path(calls[0][calls[0].index('-File')+1]) == Path(__file__).parents[3] / 'Launch-Atnos.ps1'


def test_legacy_watchdog_preserves_healthy_family_even_with_duplicates(monkeypatch):
    watchdog = load_watchdog_module()
    monkeypatch.setattr(watchdog, 'is_healthy', lambda: True)
    monkeypatch.setattr(watchdog, 'listener_pid', lambda: 222)
    monkeypatch.setattr(watchdog, 'managed_api_pids', lambda: [111, 222, 333])
    monkeypatch.setattr(watchdog.subprocess, 'run', lambda *a, **k: (_ for _ in ()).throw(AssertionError('No restart')))
    assert watchdog.ensure_api() == {'status':'healthy','pid':222,'restarted':False}


def test_legacy_pid_alone_never_authorizes_a_signal(monkeypatch):
    watchdog = load_watchdog_module()
    monkeypatch.setattr(watchdog.subprocess, 'run', lambda *a, **k: (_ for _ in ()).throw(AssertionError('No taskkill')))
    monkeypatch.setattr(watchdog.os, 'kill', lambda *a, **k: (_ for _ in ()).throw(AssertionError('No signal')))
    watchdog.stop_pid(12345, tree=True)
