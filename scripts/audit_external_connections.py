"""Read-only checks against the services already configured for Atnos."""
import json
import os
import time
from pathlib import Path
import httpx

ROOT = Path('E:/Atnos/App/reports/readiness-20260909')
os.environ['ATNOS_ENV_FILE'] = 'E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/config/.env'
from atnos.core.config import get_settings

s = get_settings()
rows = []
try:
    from atnos.production.project_profile import load_project_profile
    channel_voice = load_project_profile(s, 'wealth_blueprint')['production']['voice']['voice_id']
except (OSError, ValueError, KeyError):
    channel_voice = None


def check(name, url, headers, summarize):
    start = time.monotonic()
    row = {'service': name, 'scope': 'read-only authenticated API probe'}
    try:
        response = httpx.get(url, headers=headers, timeout=20, follow_redirects=False)
        row.update(http_status=response.status_code, reachable=True, authenticated=response.is_success)
        if response.is_success:
            row['details'] = summarize(response.json())
        else:
            body = response.json()
            error = body.get('error', body.get('detail', {}))
            if isinstance(error, dict):
                # Codes only: provider errors may echo keys or account identifiers.
                row['error_code'] = error.get('code', error.get('status', 'request_rejected'))
    except Exception as exc:
        row.update(reachable=False, authenticated=False, error_type=type(exc).__name__)
    row['seconds'] = round(time.monotonic()-start, 2)
    rows.append(row)


if s.openai_api_key:
    check('OpenAI', 'https://api.openai.com/v1/models', {'Authorization': 'Bearer '+s.openai_api_key},
          lambda body: {'model_count': len(body.get('data', [])), 'scope_limit': 'Model listing only; generation not yet tested.'})
else:
    rows.append({'service': 'OpenAI', 'status': 'not_configured'})
if s.elevenlabs_api_key:
    check('ElevenLabs', 'https://api.elevenlabs.io/v1/voices', {'xi-api-key': s.elevenlabs_api_key},
          lambda body: {'voice_count': len(body.get('voices', [])),
                        'configured_voice_visible': any(v.get('voice_id') == channel_voice for v in body.get('voices', [])) if channel_voice else None,
                        'voice_configuration_source': 'wealth_blueprint production profile',
                        'scope_limit': 'Voice listing only; paid synthesis not tested.'})
else:
    rows.append({'service': 'ElevenLabs', 'status': 'not_configured'})

for provider in ['ANTHROPIC_API_KEY', 'PEXELS_API_KEY', 'PIXABAY_API_KEY']:
    rows.append({'service': provider.removesuffix('_API_KEY'), 'configured_in_runtime_environment': bool(os.environ.get(provider)),
                 'scope_limit': 'Encrypted account vault remains blocked by Windows ACL; no credentials inferred from configuration.'})

(ROOT / 'external-connections.json').write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding='utf-8')
print(json.dumps(rows, indent=2, ensure_ascii=False))
