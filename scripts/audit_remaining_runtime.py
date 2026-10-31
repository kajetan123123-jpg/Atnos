"""Read runtime configuration, paths and optional engine readiness without installing."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import re
from datetime import datetime, timezone

ROOT = Path('E:/Atnos/App/reports/readiness-20260909')
os.environ['ATNOS_ENV_FILE'] = 'E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/config/.env'
os.environ['TEMP'] = os.environ['TMP'] = 'E:/Atnos/Temp'
os.environ['HF_HOME'] = 'E:/Atnos/ToolCaches/huggingface'
os.environ['TORCH_HOME'] = 'E:/Atnos/ToolCaches/torch'
os.environ['XDG_CACHE_HOME'] = 'E:/Atnos/ToolCaches'
from atnos.core.config import get_settings
from atnos.services.f5_tts_runtime_service import F5TTSRuntimeService
from atnos.services.local_video_generation_service import LocalVideoGenerationService
from atnos.providers.registry import build_runtime_provider_registry

s = get_settings()
report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'scope': 'Read-only configuration and runtime probes, no generation or installation.'}
report['f5_tts'] = F5TTSRuntimeService(s).status().to_dict()
report['diffusers_video'] = asdict(LocalVideoGenerationService(s).ensure_startup_ready())
report['llm_adapters'] = [{'key': p.key, 'label': p.label, 'local': p.local_first,
                          'capabilities': [c.value for c in p.capabilities]}
                         for p in build_runtime_provider_registry(app_root=Path('E:/Atnos/App'), settings=s).list()]
report['configured_cloud_keys'] = {field: bool(getattr(s, field, None)) for field in
    ['openai_api_key','anthropic_api_key','gemini_api_key','elevenlabs_api_key','pexels_api_key','pixabay_api_key']}
paths = []
for field in type(s).model_fields:
    if not re.search(r'(?:dir|path|root|home|file|exe|python)$', field) or re.search(r'key|secret|token|password', field):
        continue
    value = getattr(s, field)
    if isinstance(value, (str, Path)) and re.match(r'^[A-Za-z]:[\\/]', str(value)):
        path = Path(value)
        paths.append({'setting': field, 'path': str(path), 'on_e': str(path).lower().startswith('e:'), 'exists': path.exists()})
report['configured_absolute_paths'] = paths
report['path_audit_limit'] = 'Only declared Settings paths; does not prove every app cache or child process writes exclusively to E.'
access = []
for path in [Path('E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/data/account-secrets/aes-256-gcm.master.key'),
             Path('E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/runtime/local-capability-session.json')]:
    row = {'path': str(path), 'exists': path.exists(), 'readable': False}
    try:
        with path.open('rb'):
            row['readable'] = True
    except OSError as exc:
        row['error_type'] = type(exc).__name__
    access.append(row)
report['profile_access'] = access
(ROOT / 'remaining-runtime.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, default=str))
