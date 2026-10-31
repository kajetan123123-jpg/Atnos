from datetime import datetime, timezone
import json
import os
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from atnos.services import production_gpu_residency_service as residency
from atnos.services.comfyui_runtime_service import ComfyUiRuntimeService


@pytest.fixture
def service(tmp_path, monkeypatch):
    settings = SimpleNamespace(ollama_base_url='http://127.0.0.1:11436', runtime_dir=tmp_path,
                               data_dir=tmp_path, comfyui_base_url='http://127.0.0.1:8188')
    service = residency.ProductionGpuResidencyService(settings)
    monkeypatch.setattr(residency.GpuRuntimeLockService, 'snapshot', lambda self:
                        {'locked': True, 'owner_alive': True, 'process_id': os.getpid()})
    return service


def _transport(monkeypatch, handler):
    client = httpx.Client
    monkeypatch.setattr(residency.httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(handler)))


def test_foreign_shared_endpoint_never_queried_or_stopped(service, monkeypatch):
    service.settings.ollama_base_url = 'http://127.0.0.1:11434'
    monkeypatch.setattr(residency.httpx, 'Client', lambda **kw: pytest.fail('Foreign endpoint preserved'))
    monkeypatch.setattr(ComfyUiRuntimeService, 'stop_server', lambda *a, **kw: pytest.fail('No stop'))
    assert service.prepare_for_comfy()['status'] == 'unmanaged_endpoint_preserved'
    assert service.prepare_for_llm()['status'] == 'unmanaged_endpoint_preserved'


def test_legacy_ollama_11435_is_unmanaged(service, monkeypatch):
    service.settings.ollama_base_url = 'http://127.0.0.1:11435'
    monkeypatch.setattr(residency.httpx, 'Client', lambda **kw: pytest.fail('Legacy endpoint preserved'))
    monkeypatch.setattr(ComfyUiRuntimeService, 'stop_server', lambda *a, **kw: pytest.fail('No stop'))
    assert service.prepare_for_comfy()['status'] == 'unmanaged_endpoint_preserved'
    assert service.prepare_for_llm()['status'] == 'unmanaged_endpoint_preserved'


@pytest.mark.parametrize('state', [{'locked': False}, {'locked': True, 'owner_alive': False},
                                  {'locked': True, 'owner_alive': True, 'process_id': -1}])
def test_other_missing_or_stale_gpu_lease_never_controls_models(service, monkeypatch, state):
    monkeypatch.setattr(residency.GpuRuntimeLockService, 'snapshot', lambda self: state)
    for operation in [service.prepare_for_llm, service.prepare_for_comfy]:
        with pytest.raises(residency.ProductionGpuHandoffBlocked, match='live GPU lease'):
            operation()


def test_offline_ollama_does_not_require_start_for_images(service, monkeypatch):
    def offline(request):
        raise httpx.ConnectError('Offline', request=request)
    _transport(monkeypatch, offline)
    monkeypatch.setattr(service, '_verify_ollama_owner', lambda endpoint: pytest.fail('No live owner to validate'))
    assert service.prepare_for_comfy()['status'] == 'ollama_offline'


@pytest.mark.parametrize('payload', [{'models': [{}]}, {'models': None}, {}, {'models': 'invalid'}])
def test_nonempty_or_unproven_residency_blocks_without_any_post(service, monkeypatch, payload):
    requests = []
    def response(request):
        requests.append(request.method)
        return httpx.Response(200, json=payload)
    _transport(monkeypatch, response)
    monkeypatch.setattr(service, '_verify_ollama_owner', lambda endpoint: None)
    with pytest.raises(residency.ProductionGpuHandoffBlocked):
        service.prepare_for_comfy(wait_timeout_s=0)
    assert requests == ['GET']


def test_empty_live_runtime_requires_identity_then_allows_comfy(service, monkeypatch):
    _transport(monkeypatch, lambda request: httpx.Response(200, json={'models': []}))
    verified = []
    monkeypatch.setattr(service, '_verify_ollama_owner', lambda endpoint: verified.append(endpoint))
    assert service.prepare_for_comfy()['status'] == 'empty_verified_ollama'
    assert verified == ['http://127.0.0.1:11436']


def test_waits_for_completed_request_self_expiry_without_forced_unload(service, monkeypatch):
    responses = iter([{'models': [{'name': 'self-expiring-model'}]}, {'models': []}])
    methods, delays = [], []
    def response(request):
        methods.append(request.method)
        return httpx.Response(200, json=next(responses))
    _transport(monkeypatch, response)
    monkeypatch.setattr(service, '_verify_ollama_owner', lambda endpoint: None)
    monkeypatch.setattr(residency, 'sleep', lambda delay: delays.append(delay))
    assert service.prepare_for_comfy()['status'] == 'empty_verified_ollama'
    assert methods == ['GET', 'GET'] and delays == [.2]


@pytest.mark.parametrize('stopped', [True, False])
def test_llm_handoff_obeys_existing_managed_idle_comfy_proof(service, monkeypatch, stopped):
    monkeypatch.setattr(ComfyUiRuntimeService, 'stop_server', lambda *a, **kwargs:
                        {'stopped': stopped, 'status': 'stopped' if stopped else 'busy'})
    if stopped:
        assert service.prepare_for_llm()['stopped']
    else:
        with pytest.raises(residency.ProductionGpuHandoffBlocked, match='deferred'):
            service.prepare_for_llm()


@pytest.mark.parametrize('damage', [None, 'pid', 'ctime', 'executable', 'listener', 'endpoint'])
def test_exact_canonical_identity_required_for_live_residency(service, monkeypatch, damage):
    executable = str(Path('E:/Atnos/Storage/Tools/Ollama/ollama.exe').resolve())
    marker = {'pid': 42, 'created_utc': datetime.fromtimestamp(100, timezone.utc).isoformat(),
              'executable': executable, 'endpoint': 'http://127.0.0.1:11436'}
    if damage == 'pid':
        marker['pid'] = 43
    if damage == 'ctime':
        marker['created_utc'] = datetime.fromtimestamp(99, timezone.utc).isoformat()
    if damage == 'executable':
        marker['executable'] = 'C:/foreign/ollama.exe'
    if damage == 'endpoint':
        marker['endpoint'] = 'http://127.0.0.1:11434'
    (service.settings.runtime_dir / 'ollama-owner-11436.json').write_text(json.dumps(marker))
    monkeypatch.setattr(residency.psutil, 'Process', lambda pid:
                        SimpleNamespace(pid=pid, exe=lambda: executable, create_time=lambda: 100))
    monkeypatch.setattr(residency.psutil, 'net_connections', lambda **kw:
                        [SimpleNamespace(status=residency.psutil.CONN_LISTEN,
                         laddr=SimpleNamespace(port=11436), pid=43 if damage == 'listener' else 42)])
    if damage:
        with pytest.raises(residency.ProductionGpuHandoffBlocked, match='ownership'):
            service._verify_ollama_owner('http://127.0.0.1:11436')
    else:
        service._verify_ollama_owner('http://127.0.0.1:11436')
