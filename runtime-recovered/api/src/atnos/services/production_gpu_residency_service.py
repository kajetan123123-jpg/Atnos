"""Safe residency handoff for the dedicated Atnos production model runtime.

Ollama's /ps does not prove that a resident model is idle. Production requests
therefore expire their own model after the response (keep_alive=0), and the
image side only observes an empty runtime. It never unloads another request.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
from time import monotonic, sleep
from urllib.parse import urlsplit

import httpx
import psutil

from atnos.services.gpu_runtime_lock_service import GpuRuntimeLockService


class ProductionGpuHandoffBlocked(RuntimeError):
    pass


class ProductionGpuResidencyService:
    def __init__(self, settings):
        self.settings = settings

    def _dedicated_endpoint(self):
        url = str(getattr(self.settings, 'ollama_base_url', '')).rstrip('/')
        endpoint = urlsplit(url)
        # 11434 and the legacy 11435 are outside Atnos' dedicated runtime.
        # Only the configured, canonical 11436 endpoint is controllable.
        if (endpoint.scheme != 'http' or endpoint.hostname not in {'127.0.0.1', 'localhost', '::1'}
                or endpoint.port != 11436 or endpoint.path or endpoint.query or endpoint.fragment
                or endpoint.username or endpoint.password):
            return None
        return url

    def _require_own_gpu_lease(self):
        state = GpuRuntimeLockService(self.settings).snapshot()
        if not (state.get('locked') and state.get('owner_alive') is True
                and state.get('process_id') == os.getpid()):
            raise ProductionGpuHandoffBlocked('GPU residency handoff requires the caller\'s live GPU lease')

    def _verify_ollama_owner(self, endpoint):
        try:
            path = self.settings.runtime_dir / 'ollama-owner-11436.json'
            marker = json.loads(path.read_text('utf-8-sig'))
            canonical = Path('E:/Atnos/Storage/Tools/Ollama/ollama.exe').resolve()
            process = psutil.Process(int(marker['pid']))
            recorded = datetime.fromisoformat(marker['created_utc'].replace('Z', '+00:00')).timestamp()
            if (marker['endpoint'] != endpoint or Path(marker['executable']).resolve() != canonical
                    or Path(process.exe()).resolve() != canonical
                    or abs(process.create_time() - recorded) >= .02):
                raise ValueError('Ollama identity mismatch')
            listeners = [connection for connection in psutil.net_connections(kind='tcp')
                         if connection.status == psutil.CONN_LISTEN and connection.laddr.port == 11436]
            if not listeners or any(connection.pid != process.pid for connection in listeners):
                raise ValueError('Ollama listener identity mismatch')
        except (OSError, ValueError, KeyError, TypeError, psutil.Error) as exc:
            raise ProductionGpuHandoffBlocked('Dedicated Ollama ownership cannot be verified; no model was unloaded') from exc

    def prepare_for_llm(self):
        if self._dedicated_endpoint() is None:
            return {'status': 'unmanaged_endpoint_preserved'}
        self._require_own_gpu_lease()
        from atnos.services.comfyui_runtime_service import ComfyUiRuntimeService
        result = ComfyUiRuntimeService(self.settings).stop_server(require_idle=True)
        if not result.get('stopped'):
            raise ProductionGpuHandoffBlocked('ComfyUI residency handoff deferred: ' + str(result.get('reason') or result.get('status')))
        return result

    def prepare_for_comfy(self, *, wait_timeout_s=10):
        endpoint = self._dedicated_endpoint()
        if endpoint is None:
            return {'status': 'unmanaged_endpoint_preserved'}
        self._require_own_gpu_lease()
        deadline = monotonic() + max(0, min(30, wait_timeout_s))
        while True:
            self._require_own_gpu_lease()
            try:
                with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
                    response = client.get(endpoint + '/api/ps')
                    response.raise_for_status()
            except httpx.ConnectError:
                # Image-only production does not require Ollama to be running.
                return {'status': 'ollama_offline', 'models': []}
            except httpx.HTTPError as exc:
                raise ProductionGpuHandoffBlocked('Cannot verify dedicated Ollama residency before ComfyUI') from exc
            self._verify_ollama_owner(endpoint)
            try:
                models = response.json()['models']
                if not isinstance(models, list):
                    raise ValueError('Invalid models list')
            except (KeyError, ValueError, TypeError) as exc:
                raise ProductionGpuHandoffBlocked('Invalid dedicated Ollama residency response') from exc
            if not models:
                return {'status': 'empty_verified_ollama', 'models': []}
            # Ollama expires a keep_alive=0 response asynchronously. Observe
            # that release briefly; never send a separate unload request.
            if monotonic() >= deadline:
                raise ProductionGpuHandoffBlocked('Dedicated Ollama has resident models; no request drain proof, no model was unloaded')
            sleep(.2)
