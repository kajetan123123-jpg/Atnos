import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import subprocess

import httpx
import pytest

from atnos.production import model_runtime_startup as startup
from atnos.production.order_store import ProductionBlocked


@pytest.fixture
def executor(monkeypatch):
    monkeypatch.setattr('atnos.production.order_autonomy.policy', lambda *_: {'enabled': True})
    return SimpleNamespace(store=SimpleNamespace(settings=SimpleNamespace(ollama_base_url='http://127.0.0.1:11436')),
        context=SimpleNamespace(job_id='test-only', artifacts={'production_order': {'delivery_mode': 'local_only'}}))


def ready():
    return httpx.Response(200, json={'models': [{'name': 'existing'}]}, request=httpx.Request('GET', 'http://127.0.0.1:11436/api/tags'))


def test_responding_model_service_is_never_started_or_restarted(executor, monkeypatch):
    monkeypatch.setattr(startup, '_launch_models_only', lambda *_: pytest.fail('Responding service was restarted'))
    client=SimpleNamespace(get=AsyncMock(return_value=ready()))
    assert asyncio.run(startup.ensure_model_endpoint(executor,client)).json()['models'][0]['name']=='existing'
    assert client.get.await_count==1


def test_absent_model_starts_once_then_reads_readiness(executor, monkeypatch):
    starts=[]
    monkeypatch.setattr(startup, '_launch_models_only', lambda *_: starts.append('models_only'))
    client=SimpleNamespace(get=AsyncMock(side_effect=[httpx.ConnectError('absent'), ready()]))
    assert asyncio.run(startup.ensure_model_endpoint(executor,client)).is_success
    assert starts==['models_only']


@pytest.mark.parametrize('mode',['read_only','publication','disabled'])
def test_readiness_probe_or_unenrolled_order_cannot_launch(executor,monkeypatch,mode):
    monkeypatch.setattr(startup,'_launch_models_only',lambda *_:pytest.fail('Unauthorized model startup'))
    if mode=='publication': executor.context.artifacts['production_order']['delivery_mode']='project_policy'
    if mode=='disabled': monkeypatch.setattr('atnos.production.order_autonomy.policy',lambda *_:None)
    client=SimpleNamespace(get=AsyncMock(side_effect=httpx.ConnectError('absent')))
    with pytest.raises(httpx.ConnectError):
        asyncio.run(startup.ensure_model_endpoint(executor,client,allow_start=mode!='read_only'))


def test_responding_http_error_is_preserved_without_restart(executor,monkeypatch):
    monkeypatch.setattr(startup,'_launch_models_only',lambda *_:pytest.fail('HTTP error restarted service'))
    response=httpx.Response(503,request=httpx.Request('GET','http://127.0.0.1:11436/api/tags'))
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(startup.ensure_model_endpoint(executor,SimpleNamespace(get=AsyncMock(return_value=response))))


def test_model_start_readiness_deadline_does_not_launch_again(executor,monkeypatch):
    starts=[]
    monkeypatch.setattr(startup,'_launch_models_only',lambda *_:starts.append(1))
    monkeypatch.setattr(startup.asyncio,'sleep',AsyncMock())
    client=SimpleNamespace(get=AsyncMock(side_effect=httpx.ConnectError('absent')))
    with pytest.raises(ProductionBlocked,match='did not become ready'):
        asyncio.run(startup.ensure_model_endpoint(executor,client))
    assert starts==[1] and client.get.await_count==31


def test_models_only_launcher_branch_cannot_touch_application_services():
    root=Path(__file__).resolve().parents[3]
    fixture=Path(__file__).parent/'fixtures/models_only_launcher.ps1'
    result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(fixture),'-appRoot',str(root)],
        capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    assert result.returncode==0,result.stdout+result.stderr
    assert 'PASS: models only; no application services or capability changes.' in result.stdout


def test_model_launcher_does_not_capture_pipes_inherited_by_detached_service(tmp_path, monkeypatch):
    # A long-lived detached Windows child may retain the launcher's inheritable
    # pipe handles after PowerShell exits, so communicate() must not await EOF.
    root = tmp_path / 'app'
    root.mkdir()
    (root / 'Launch-Atnos.ps1').write_text('# fixture', encoding='utf-8')
    env_file = tmp_path / 'runtime.env'
    env_file.write_text('OLLAMA_BASE_URL=http://127.0.0.1:11436\n', encoding='utf-8')
    monkeypatch.setenv('ATNOS_ENV_FILE', str(env_file))
    calls = []
    def execute(argv, **kwargs):
        # Never let pytest assertion introspection serialize the inherited
        # operator environment (which may contain unrelated credentials).
        calls.append((argv, {key:value for key,value in kwargs.items() if key != 'env'}))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(startup.subprocess, 'run', execute)
    startup._launch_models_only(SimpleNamespace(app_root=root, ollama_base_url='http://127.0.0.1:11436'))
    assert len(calls) == 1
    argv, options = calls[0]
    assert '-ModelsOnly' in argv and '-RestartBackend' not in argv
    assert options.get('stdout') == subprocess.DEVNULL
    assert options.get('stderr') == subprocess.DEVNULL
    assert not options.get('capture_output', False)
    assert options['timeout'] == 60
