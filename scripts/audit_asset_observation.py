"""One local vision probe on the retained clip that exceeded the response contract."""
import json
import os
from pathlib import Path
import shutil
import time
from datetime import datetime, timezone

os.environ.update({'ATNOS_ENV_FILE': 'E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/config/.env',
                   'TEMP': 'E:/Atnos/Temp', 'TMP': 'E:/Atnos/Temp', 'PYTHONIOENCODING': 'utf-8'})
import httpx
from atnos.core.config import get_settings
from atnos.production.asset_observation import describe_frames
from atnos.production.project_profile import VisualQAProfile
from atnos.services.gpu_runtime_lock_service import GpuRuntimeLockService

settings = get_settings()
root = Path('E:/Atnos/App/reports/readiness-20260909/asset-observation-fixed-live')
if root.exists():
    raise SystemExit('Probe exists; inspect it before making any further calls.')
httpx.get(settings.ollama_base_url.rstrip('/') + '/api/tags', timeout=40).raise_for_status()
source = settings.data_dir / 'production/wealth_blueprint/prod-d8bef36142f2/asset_observations/pexels-30014172.png'
lock = GpuRuntimeLockService(settings)
lease = lock.acquire(owner='readiness-observation-bounds', timeout_s=0)
root.mkdir()
sheet = root / source.name
shutil.copy2(source, sheet)
started = time.monotonic()
result = {'started_at': datetime.now(timezone.utc).isoformat(), 'max_calls': 1,
          'scope': 'Actual failed contact sheet through the updated adapter; no order or checkpoint mutation.'}
try:
    result.update(status='validated', result=describe_frames(settings, sheet, VisualQAProfile(),
                  retry_feedback='readable_text contained 10 items; the maximum is 8.'))
except Exception as exc:
    result.update(status='failed', error_type=type(exc).__name__, error=str(exc))
finally:
    lock.release(lease)
    result['seconds'] = round(time.monotonic() - started, 2)
    (root / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
