from pathlib import Path
from types import SimpleNamespace

import pytest

from atnos.services import runtime_stop_safety as safety
from atnos.services.comfyui_runtime_service import ComfyUiRuntimeService, ComfyUiInstallState


@pytest.mark.parametrize('args,expected', [
    (['-s', 'main.py'], True),
    (['-B', '-s', 'main.py', '--port', '8188'], True),
    (['-c', 'print(1)', 'main.py'], False),
    (['other.py', 'main.py'], False),
    (['--unknown', 'main.py'], False),
    (['-X', 'main.py'], False),
    (['-s'], False),
])
def test_only_known_flags_before_exact_entrypoint(monkeypatch, tmp_path, args, expected):
    python = tmp_path / 'python.exe'
    process = SimpleNamespace(cmdline=lambda: [str(python), *args],
                              cwd=lambda: str(tmp_path), create_time=lambda: 123.0)
    monkeypatch.setattr(safety.psutil, 'process_iter', lambda: [process])
    actual = safety.matching_python_processes(python=python, script=tmp_path / 'main.py', cwd=tmp_path)
    assert bool(actual) is expected


def test_s_flag_process_prevents_second_start(monkeypatch, tmp_path):
    python, script = tmp_path / 'python.exe', tmp_path / 'main.py'
    state = ComfyUiInstallState(tmp_path, None, None, python, script, None, True)
    process = SimpleNamespace(cmdline=lambda: [str(python), '-s', 'main.py'],
                              cwd=lambda: str(tmp_path), create_time=lambda: 123.0)
    monkeypatch.setattr(safety.psutil, 'process_iter', lambda: [process])
    service = ComfyUiRuntimeService(SimpleNamespace(comfyui_base_url='http://127.0.0.1:8188'))
    monkeypatch.setattr(service, 'describe_install', lambda: state)
    launches = []
    monkeypatch.setattr(service, '_launch_python', lambda *a, **k: launches.append(1))
    assert service._launch_from_state(state, lowvram=False, disable_smart_memory=False) is False
    assert launches == []
