import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from atnos.services import runtime_stop_safety
from atnos.services.comfyui_runtime_service import ComfyUiRuntimeService, ComfyUiInstallState


@pytest.fixture
def owned(tmp_path, monkeypatch):
    install = tmp_path / 'install'
    main = install / 'ComfyUI/main.py'
    python = install / 'standalone-env/python.exe'
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    state = ComfyUiInstallState(install, None, None, install / 'ComfyUI/.venv/Scripts/python.exe', main, None, True)
    process = SimpleNamespace(pid=12404, create_time=lambda: 123.456,
                              cwd=lambda: str(main.parent),
                              cmdline=lambda: [str(python), '-s', 'main.py', '--listen', '127.0.0.1', '--port', '8188'])
    settings = SimpleNamespace(comfyui_base_url='http://127.0.0.1:8188', runtime_dir=runtime)
    service = ComfyUiRuntimeService(settings)
    monkeypatch.setattr(service, 'describe_install', lambda: state)
    monkeypatch.setattr(runtime_stop_safety.psutil, 'process_iter', lambda: [process])
    marker = {'pid': process.pid, 'process_started_at_epoch': process.create_time(), 'port': 8188,
              'executable': str(python), 'marker': 'atnos-managed-comfy'}
    path = runtime / 'comfyui-managed.instance.json'
    path.write_text(json.dumps(marker))
    return service, process, marker, path


def test_known_standalone_interpreter_and_main_cwd_match_owned_identity(owned):
    service, process, _, _ = owned
    assert service._owned_runtime_processes() == [process]


@pytest.mark.parametrize('damage', ['foreign_pid', 'changed_creation', 'wrong_port', 'missing_creation', 'foreign_executable'])
def test_incomplete_changed_or_foreign_instance_never_authorizes_stop(owned, damage):
    service, _, marker, path = owned
    if damage == 'foreign_pid':
        marker['pid'] += 1
    elif damage == 'changed_creation':
        marker['process_started_at_epoch'] -= 10
    elif damage == 'wrong_port':
        marker['port'] = 8189
    elif damage == 'missing_creation':
        del marker['process_started_at_epoch']
    else:
        marker['executable'] = 'C:/foreign/python.exe'
    path.write_text(json.dumps(marker))
    assert service._owned_runtime_processes() == []


def test_correct_identity_with_other_command_port_is_foreign(owned):
    service, process, _, _ = owned
    original = process.cmdline()
    process.cmdline = lambda: [*original[:-1], '8189']
    assert service._owned_runtime_processes() == []


@pytest.mark.parametrize('queue', [(1, 0), (0, 1), (None, None)])
def test_busy_or_unknown_queue_never_signals_even_a_known_owner(owned, monkeypatch, queue):
    service, process, _, _ = owned
    monkeypatch.setattr(service, '_queue_counts', lambda: queue)
    process.terminate = lambda: pytest.fail('Busy/unknown queue must prevent process termination')
    assert service._terminate_runtime_processes() is False


@pytest.mark.parametrize('damage', [None, 'foreign_child', 'changed_launcher_creation', 'wrong_child_port', 'foreign_listener'])
def test_launch_wrapper_identity_is_bound_to_its_known_child_listener(owned, monkeypatch, damage):
    service, child, marker, path = owned
    wrapper = SimpleNamespace(pid=100, create_time=lambda: 90.123,
        cmdline=lambda: [str(service.describe_install().python_executable), '-s', 'main.py', '--port', '8188'],
        children=lambda **kwargs: [child])
    marker.update(pid=100, process_started_at_epoch=90.123,
                  executable=str(service.describe_install().python_executable))
    if damage == 'foreign_child':
        wrapper.children = lambda **kwargs: []
    if damage == 'changed_launcher_creation':
        marker['process_started_at_epoch'] -= 1
    if damage == 'wrong_child_port':
        child.cmdline = lambda: [marker['executable'], '-s', 'main.py', '--port', '8189']
    path.write_text(json.dumps(marker))
    before = path.read_bytes()
    monkeypatch.setattr(service, '_matching_install_processes', lambda: [wrapper, child])
    monkeypatch.setattr(runtime_stop_safety.psutil, 'Process', lambda pid: wrapper)
    monkeypatch.setattr(runtime_stop_safety.psutil, 'net_connections', lambda **kwargs:
        [SimpleNamespace(status='LISTEN', laddr=SimpleNamespace(port=8188),
                         pid=999 if damage == 'foreign_listener' else child.pid)])
    if damage:
        assert not service._reconcile_owned_listener()
        assert path.read_bytes() == before
    else:
        assert service._reconcile_owned_listener()
        current = json.loads(path.read_text())
        assert current['pid'] == child.pid and current['process_started_at_epoch'] == child.create_time()
        assert current['launcher_pid'] == wrapper.pid and current['status'] == 'READY'
