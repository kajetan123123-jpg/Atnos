"""Prepare reusable descriptions of the existing licensed library, then verify cache hits."""
import json
import os
from pathlib import Path
import time
from datetime import datetime, timezone

os.environ.update({'ATNOS_ENV_FILE': 'E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/config/.env',
                   'TEMP': 'E:/Atnos/Temp', 'TMP': 'E:/Atnos/Temp', 'PYTHONIOENCODING': 'utf-8'})
os.environ['PATH'] = 'E:/Atnos/Tools/FFmpeg/bin' + os.pathsep + os.environ.get('PATH', '')
from atnos.core.config import get_settings
from atnos.production import asset_observation_cache as cache, visual_semantic_audit
from atnos.production.asset_observation import describe_frames
from atnos.production.order_store import file_hash
from atnos.production.project_profile import load_project_profile, VisualQAProfile
from atnos.production.steps.order_assets import library_manifest
from atnos.services.gpu_runtime_lock_service import GpuRuntimeLockService

settings = get_settings()
root = Path('E:/Atnos/App/reports/readiness-20260909/library-observation-cache')
if root.exists():
    raise SystemExit('Library audit already exists. Inspect it before any further generation.')
profile = load_project_profile(settings, 'wealth_blueprint')
config = VisualQAProfile.model_validate(profile['production']['visual_qa'])
revision = cache.model_revision(settings, config.model)
if not revision:
    raise SystemExit('Cannot verify the installed model digest/version.')
library = library_manifest(settings, profile)
if len(library['assets']) > 20:
    raise SystemExit('This audit allows at most twenty clips, one attempt per clip.')
lock = GpuRuntimeLockService(settings)
lease = lock.acquire(owner='readiness-library-observations', timeout_s=0)
root.mkdir()
report = {'started_at': datetime.now(timezone.utc).isoformat(), 'max_calls': 20, 'actual_calls': 0,
          'scope': 'Reusable library preparation on E; no production order budgets changed and no publishing.',
          'model_revision': revision, 'assets': []}
try:
    for asset in library['assets']:
        started = time.monotonic()
        row = {'id': asset['id'], 'source_sha256': asset['sha256'], 'status': 'running'}
        report['assets'].append(row)
        (root / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        try:
            source = (settings.data_dir / asset['path']).resolve()
            if not source.is_relative_to(settings.data_dir.resolve()) or file_hash(source) != asset['sha256']:
                raise ValueError('Source changed or escaped the library')
            key = cache.identity(asset['sha256'], config, revision)
            if not cache.load(settings, key):
                sheet = root / 'generated' / (asset['id'] + '.png')
                if visual_semantic_audit.extract_storyboard_contact_sheet(source, sheet, frame_count=6) is None:
                    raise ValueError('Could not extract sample frames')
                report['actual_calls'] += 1
                payload = describe_frames(settings, sheet, config)
                if file_hash(source) != asset['sha256'] or cache.model_revision(settings, config.model) != revision:
                    raise ValueError('Source or model changed during observation')
                cache.save(settings, key, sheet, payload)
            payload, files = cache.restore(settings, key, root / 'restored' / (asset['id'] + '.png'))
            row.update(status='cached_and_restored', cache_key=payload['cache']['key'],
                       copied_evidence_files=len(files), observation=payload['observation'])
        except Exception as exc:
            row.update(status='failed', error_type=type(exc).__name__, error=str(exc))
        row['seconds'] = round(time.monotonic() - started, 2)
        (root / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps({k:v for k,v in row.items() if k != 'observation'}), flush=True)
finally:
    lock.release(lease)
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    (root / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
