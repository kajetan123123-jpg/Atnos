"""Run a bounded local image-quality comparison; never changes a production order."""
import json
import argparse
import os
from pathlib import Path

root = Path('E:/Atnos/App/reports/readiness-20260909')
parser = argparse.ArgumentParser()
parser.add_argument('--review-version', choices=['v1', 'v2'], default='v2')
args = parser.parse_args()
summary_path = root / ('story-illustrations-review-' + args.review_version + '.json')
os.environ.update(ATNOS_ENV_FILE='E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/config/.env',
                  TEMP='E:/Atnos/Temp', TMP='E:/Atnos/Temp')
import httpx
from atnos.core.config import get_settings
from atnos.production.fiction_script import StoryScript, script_issues
from atnos.production.order_store import ProductionOrderStore, file_hash
from atnos.production.steps.common import write_json
from atnos.production.story_image_review import review_illustration
from atnos.services.gpu_runtime_lock_service import GpuRuntimeLockService

settings = get_settings()
store = ProductionOrderStore(settings)
order = store.get('prod-5d5f9fb968cf')
row = next(r for r in order['checkpoints'] if r['stage'] == 'script' and r['status'] == 'done')
if not store._valid_files(Path(order['artifact_dir']), row['files_json']):
    raise SystemExit('Source story changed after QA')
script = StoryScript.model_validate({key: row['output'][key] for key in StoryScript.model_fields})
config = order['profile']['production']
if script_issues(script, config, order['request']):
    raise SystemExit('Source story no longer passes deterministic review')
lock = GpuRuntimeLockService(settings)
lease = lock.acquire(owner='readiness-story-image-review', timeout_s=0)
results = []
try:
    with httpx.Client(timeout=30) as client:
        queue = client.get(settings.comfyui_base_url.rstrip('/') + '/queue')
        queue.raise_for_status()
        if queue.json().get('queue_running') or queue.json().get('queue_pending'):
            raise SystemExit('ComfyUI is busy; no memory operation or model inference performed')
        freed = client.post(settings.comfyui_base_url.rstrip('/') + '/free', json={'unload_models': True, 'free_memory': True})
        freed.raise_for_status()
    # Six existing illustrations, at most two local model calls each. No new images or paid API calls.
    for version in ('v1', 'v2', 'v3'):
        for segment_id in ('s01', 's03'):
            directory = root / ('story-illustrations-' + version) / segment_id
            image_path = directory / 'illustration.png'
            state = json.loads((directory / 'request.json').read_text(encoding='utf-8'))
            if state['status'] != 'complete' or file_hash(image_path) != state['sha256']:
                raise ValueError('Generated image changed after the ComfyUI request')
            print(json.dumps({'version': version, 'segment': segment_id, 'status': 'reviewing'}), flush=True)
            result = review_illustration(settings, directory / ('quality-' + args.review_version), image_path=image_path, script=script,
                                         segment_id=segment_id, config=config, model='qwen3-vl:8b-instruct')
            results.append({'version': version, 'segment_id': segment_id, 'review': result})
            write_json(summary_path, {'source_order': order['id'], 'results': results,
                'maximum_model_calls': 12, 'paid_api_calls': 0, 'production_order_modified': False,
                'status': 'complete' if len(results) == 6 else 'in_progress'})
            print(json.dumps({'version': version, 'segment': segment_id, 'status': result['status'], 'blockers': result['blockers']}), flush=True)
except Exception as exc:
    write_json(summary_path, {'source_order': order['id'], 'results': results, 'status': 'blocked',
               'maximum_model_calls': 12, 'paid_api_calls': 0, 'production_order_modified': False,
               'error_type': type(exc).__name__, 'review_version': args.review_version})
    raise
finally:
    lock.release(lease)
