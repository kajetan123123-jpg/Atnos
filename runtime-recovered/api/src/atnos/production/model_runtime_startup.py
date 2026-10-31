"""Lazy model-only startup through the ownership-aware desktop launcher."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
from urllib.parse import urlsplit

import httpx

from atnos.production.order_store import ProductionBlocked


def _launch_models_only(settings):
    endpoint = urlsplit(settings.ollama_base_url)
    if (os.name != 'nt' or endpoint.scheme != 'http'
            or endpoint.hostname not in {'127.0.0.1', 'localhost', '::1'}
            or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
            or endpoint.path not in {'', '/'}):
        raise ProductionBlocked('Model startup requires the configured local Windows Ollama endpoint')
    root = Path(settings.app_root or 'E:/Atnos/App').resolve()
    env_file = Path(os.environ.get('ATNOS_ENV_FILE') or
                    'E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/config/.env').resolve()
    launcher = root / 'Launch-Atnos.ps1'
    if root.drive.casefold() != 'e:' or env_file.drive.casefold() != 'e:' or not launcher.is_file() or not env_file.is_file():
        raise ProductionBlocked('Owned model launcher and configuration must exist on E:')
    configured = None
    for line in env_file.read_text('utf-8-sig').splitlines():
        if line.strip().startswith('OLLAMA_BASE_URL='):
            configured = line.split('=', 1)[1].strip().strip('\"\'').rstrip('/')
    if configured != settings.ollama_base_url.rstrip('/'):
        raise ProductionBlocked('Model launcher endpoint differs from the production endpoint')
    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run(
        [str(powershell), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
         '-File', str(launcher), '-ModelsOnly', '-NoErrorDialog'],
        cwd=root, env={**os.environ, 'ATNOS_ENV_FILE': str(env_file)},
        # Detached Windows services can inherit pipe handles and keep
        # communicate() waiting for EOF even after the launcher has exited.
        # Diagnostics already belong to the launcher's own logs on E:.
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        # Launcher diagnostics stay in its E: log, not in an unredacted API error.
        raise ProductionBlocked('Owned model-only launcher failed; inspect the launcher log on E:')


async def ensure_model_endpoint(executor, client, *, allow_start=True):
    """Start an absent local model service; never restart a responding service."""
    settings = executor.store.settings
    url = settings.ollama_base_url.rstrip('/') + '/api/tags'
    try:
        response = await client.get(url)
        response.raise_for_status()
        return response
    except httpx.ConnectError:
        if not allow_start:
            raise
        request = executor.context.artifacts['production_order']
        from atnos.production.order_autonomy import policy
        enabled = policy(settings, executor.context.job_id)
        if request.get('delivery_mode') != 'local_only' or not enabled or not enabled['enabled']:
            raise
    await asyncio.to_thread(_launch_models_only, settings)
    # A competing/foreign owner is preserved by the launcher. Readiness must
    # still come from the endpoint and the normal frozen-model preflight.
    for attempt in range(30):
        try:
            response = await client.get(url)
            response.raise_for_status()
            return response
        except httpx.ConnectError:
            if attempt == 29:
                raise ProductionBlocked('Local model startup did not become ready; no service was restarted') from None
            await asyncio.sleep(1)
