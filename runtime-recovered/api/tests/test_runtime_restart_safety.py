from __future__ import annotations
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import psutil
import pytest

from atnos.services.comfyui_runtime_service import ComfyUiRuntimeService
from atnos.services.runtime_stop_safety import gpu_stop_allowed
from test_support import build_test_settings

ROOT = Path(__file__).resolve().parents[3]

def load_guard():
    spec = importlib.util.spec_from_file_location('runtime_process_safety_test', ROOT / 'scripts/runtime_process_safety.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('entry,cwd,expected', [
    ('scripts/run_atnos_api.py', 'runtime-recovered/api', 'api'),
    ('../App-other/runtime-recovered/api/scripts/run_atnos_api.py', '.', None),
    ('E:/Other/run_atnos_api.py', '.', None),
    ('-c', '.', None),
    ('frontend/serve_spa.py', '.', 'web'),
])
def test_exact_entrypoint_ownership(entry, cwd, expected):
    guard = load_guard()
    python = str(ROOT / 'runtime-recovered/api/.venv/Scripts/python.exe')
    assert guard.classify(python, [python, entry, str(ROOT / 'runtime-recovered/api/scripts/run_atnos_api.py')], str(ROOT / cwd)) == expected


def test_venv_child_with_base_interpreter_requires_the_same_entrypoint():
    guard = load_guard()
    exe = str(ROOT.parent / 'Tools/Python314/python.exe')
    argv = [str(ROOT / 'runtime-recovered/api/.venv/Scripts/python.exe'), 'scripts/run_atnos_api.py']
    assert guard.classify(exe, argv, ROOT / 'runtime-recovered/api') == 'api'
    assert guard.classify(exe, argv, ROOT.parent / 'App-other/runtime-recovered/api') is None


def test_orphan_remotion_node_and_encoder_remain_restart_blockers():
    guard = load_guard()
    node = str(ROOT.parent / 'Tools/NodeJS/node.exe')
    cwd = ROOT / 'runtime-recovered/remotion-renderer'
    wrapper = str(cwd / 'node_modules/.pnpm/tsx@4/node_modules/tsx/dist/cli.mjs')
    parent = [node, wrapper, 'scripts/render-browser.ts', '--manifest', 'movie.json']
    child = [node, '--require', 'preflight.cjs', '--import', 'file:///loader.mjs',
             'scripts/render-browser.ts', '--manifest', 'movie.json']
    assert guard.classify(node, parent, cwd) == 'render'
    assert guard.classify(node, child, cwd) == 'render'
    assert guard.remotion_entry(parent, cwd) == guard.remotion_entry(child, cwd)
    assert guard.classify(node, [node, 'unrelated.js', 'scripts/render-browser.ts'], cwd) is None
    assert guard.classify(node, parent, ROOT.parent / 'OtherApp') is None
    encoder = str(ROOT.parent / 'Tools/FFmpeg/bin/ffmpeg.exe')
    assert guard.classify(encoder, [encoder, '-i', 'pipe:0', str(guard.DATA / 'production/job/movie.mp4')], cwd) == 'render'
    assert guard.classify(encoder, [encoder, '-i', 'input.mp4', str(ROOT.parent / 'OtherApp/movie.mp4')], cwd) is None


def test_shared_python_does_not_claim_another_app_render_script():
    guard = load_guard()
    exe = str(ROOT.parent / 'Tools/Python314/python.exe')
    assert guard.classify(exe, [exe, 'render.py'], ROOT.parent / 'OtherApp') is None


def test_activity_groups_worker_parent_and_child_but_keeps_orders_separate(tmp_path, monkeypatch):
    guard = load_guard()
    guard.DATA = tmp_path

    class FakeProcess:
        def __init__(self, pid, order):
            self.pid = pid
            self._argv = [
                str(ROOT / 'runtime-recovered/api/.venv/Scripts/python.exe'),
                'scripts/run_production_order.py', 'run', order,
            ]

        def oneshot(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def exe(self):
            return self._argv[0]

        def cmdline(self):
            return list(self._argv)

        def cwd(self):
            return str(ROOT / 'runtime-recovered/api')

        def create_time(self):
            return float(self.pid)

    processes = [FakeProcess(101, 'order-a'), FakeProcess(102, 'order-a'), FakeProcess(103, 'order-b')]
    monkeypatch.setattr(guard.psutil, 'process_iter', lambda: iter(processes))
    assert guard.work_blockers() == ['production_worker', 'production_worker']


def test_activity_treats_reused_gpu_pid_as_unknown(tmp_path):
    guard = load_guard()
    guard.DATA = tmp_path
    lock = tmp_path / 'runtime/gpu-runtime.lock'
    lock.mkdir(parents=True)
    process = psutil.Process()
    (lock / 'owner.json').write_text(json.dumps({
        'process_id': process.pid,
        'process_started_at_epoch': process.create_time() - 10,
    }), encoding='utf-8')
    assert 'gpu_lease_unknown' in guard.work_blockers()


def test_foreign_process_and_reused_identity_cannot_be_stopped():
    guard = load_guard()
    current = psutil.Process()
    with pytest.raises(RuntimeError, match='identity changed|not an owned'):
        guard.stop_owned(current.pid, current.create_time())
    with pytest.raises(RuntimeError, match='identity changed|not an owned'):
        guard.stop_owned(current.pid, current.create_time() - 100)
    assert current.is_running()


@pytest.mark.parametrize('queue', [(1, 0), (0, 1), (None, None)])
def test_comfy_active_pending_and_unknown_queue_never_signals(queue, tmp_path, monkeypatch):
    service = ComfyUiRuntimeService(build_test_settings(tmp_path))
    monkeypatch.setattr(service, '_queue_counts', lambda: queue)
    monkeypatch.setattr(service, '_owned_runtime_processes', lambda: pytest.fail('Must not inspect/stop candidates before idle is established'))
    assert service._terminate_runtime_processes() is False


@pytest.mark.parametrize('payload', [{}, [], {'queue_running': [], 'queue_pending': 'bad'}])
def test_comfy_invalid_queue_is_unknown(payload, tmp_path, monkeypatch):
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, url): return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)
    monkeypatch.setattr('atnos.services.comfyui_runtime_service.httpx.Client', Client)
    assert ComfyUiRuntimeService(build_test_settings(tmp_path))._queue_counts() == (None, None)


def test_queue_timeout_is_not_idle(tmp_path, monkeypatch):
    class Client:
        def __init__(self, **kwargs): raise httpx.ConnectTimeout('fixture')
    monkeypatch.setattr('atnos.services.comfyui_runtime_service.httpx.Client', Client)
    assert ComfyUiRuntimeService(build_test_settings(tmp_path))._queue_counts() == (None, None)


def test_incomplete_gpu_lease_blocks_stop(tmp_path):
    settings = build_test_settings(tmp_path)
    (settings.data_dir / 'runtime/gpu-runtime.lock').mkdir(parents=True)
    assert gpu_stop_allowed(settings) is False


def test_api_runner_drains_inflight_response_and_ignores_wrong_pid(tmp_path):
    # Real Uvicorn/runner on a fresh loopback port, no real app, providers or DB.
    app = tmp_path / 'drain_fixture.py'
    app.write_text('''import asyncio
from pathlib import Path
def create_app():
    async def app(scope, receive, send):
        if scope['type'] == 'lifespan':
            while True:
                event = await receive()
                if event['type'] == 'lifespan.startup': await send({'type':'lifespan.startup.complete'})
                else:
                    await send({'type':'lifespan.shutdown.complete'})
                    return
        else:
            await send({'type':'http.response.start','status':200,'headers':[]})
            if scope['path'] == '/slow':
                Path(__file__).with_suffix('.started').touch()
                await asyncio.sleep(2)
            await send({'type':'http.response.body','body':b'complete syllable and response'})
    return app
''', encoding='utf-8')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, DATA_DIR=str(tmp_path), TEMP=str(tmp_path), TMP=str(tmp_path),
               PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(tmp_path))
    log = (tmp_path / 'api.log').open('wb')
    proc = subprocess.Popen([sys.executable, '-B', str(ROOT / 'runtime-recovered/api/scripts/run_atnos_api.py'),
        '--app', 'drain_fixture:create_app', '--port', str(port)], env=env, stdout=log, stderr=log,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    children = []
    try:
        url = f'http://127.0.0.1:{port}'
        deadline = time.monotonic() + 20
        while True:
            try:
                if httpx.get(url, timeout=.5).status_code == 200: break
            except httpx.HTTPError: pass
            assert proc.poll() is None
            assert time.monotonic() < deadline
            time.sleep(.1)
        parent = psutil.Process(proc.pid)
        children = parent.children(recursive=True)
        runner = children[-1] if children else parent
        target = tmp_path / 'runtime/api-shutdown-request.json'
        target.parent.mkdir()
        target.write_text(json.dumps({'pid': -1, 'created': runner.create_time(), 'expires': time.time()+30}))
        time.sleep(.6)
        assert httpx.get(url, timeout=2).status_code == 200
        with ThreadPoolExecutor(max_workers=1) as pool:
            response = pool.submit(httpx.get, url + '/slow', timeout=10)
            deadline = time.monotonic() + 5
            while not app.with_suffix('.started').exists():
                assert time.monotonic() < deadline
                time.sleep(.02)
            target.write_text(json.dumps({'pid': runner.pid, 'created': runner.create_time(), 'expires': time.time()+30}))
            assert response.result(timeout=10).text == 'complete syllable and response'
        assert proc.wait(timeout=15) == 0
    finally:
        # Only handles/identity objects from this fixture's own process family.
        for child in children:
            if child.is_running(): child.kill()
        if proc.poll() is None: proc.kill()
        proc.wait(timeout=10)
        log.close()
