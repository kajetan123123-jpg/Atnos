"""One explicitly editorial prompt experiment; not evidence of automatic story rendering."""
import json
import os
from pathlib import Path

root = Path('E:/Atnos/App/reports/readiness-20260909/story-illustrations-v4/s03')
os.environ.update(ATNOS_ENV_FILE='E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/config/.env',
                  TEMP='E:/Atnos/Temp', TMP='E:/Atnos/Temp')
import httpx
from atnos.core.config import get_settings
from atnos.production.fiction_script import StoryScript, script_issues
from atnos.production.order_store import ProductionOrderStore
from atnos.production.story_images import generate_illustration
from atnos.production.story_image_review import review_illustration
from atnos.production.steps.common import write_json
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
    raise SystemExit('Source story failed current deterministic QA')
prompt = (
    "Watercolor children's storybook. (Anthropomorphic little red fox walking upright on two hind legs:1.3), "
    '(carrying a small brass camping lantern in both front paws:1.5), (a single green leaf worn as a cap:1.3), '
    'white tail tip. Full body, lantern clearly visible in front of chest. Quiet mossy forest path, distant hollow tree, '
    'warm evening light, gentle expression. No text.'
)
lock = GpuRuntimeLockService(settings)
lease = lock.acquire(owner='readiness-story-composition-probe', timeout_s=0)
try:
    with httpx.Client(timeout=30) as client:
        response = client.get(settings.ollama_base_url.rstrip('/') + '/api/ps')
        response.raise_for_status()
        for model in response.json().get('models', []):
            unloaded = client.post(settings.ollama_base_url.rstrip('/') + '/api/generate', json={'model': model['name'], 'keep_alive': 0})
            unloaded.raise_for_status()
        root.mkdir(parents=True, exist_ok=True)
        write_json(root / 'probe.json', {'source_order': order['id'], 'segment_id': 's03',
            'prompt_origin': 'assistant_editorial_experiment', 'automatic_prompt_pipeline': False,
            'original_story_modified': False, 'maximum_image_requests': 1, 'maximum_review_calls': 2, 'paid_api_calls': 0})
        generated = generate_illustration(settings, root, prompt=prompt,
            checkpoint='Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors', seed=20260913)
        print(json.dumps({'image_status': generated['status']}), flush=True)
        queue = client.get(settings.comfyui_base_url.rstrip('/') + '/queue')
        queue.raise_for_status()
        if queue.json().get('queue_running') or queue.json().get('queue_pending'):
            raise SystemExit('ComfyUI is busy; image retained, review not started')
        freed = client.post(settings.comfyui_base_url.rstrip('/') + '/free', json={'unload_models': True, 'free_memory': True})
        freed.raise_for_status()
    result = review_illustration(settings, root / 'quality-v2', image_path=root / 'illustration.png', script=script,
        segment_id='s03', config=config, model='qwen3-vl:8b-instruct')
    print(json.dumps({'quality_status': result['status'], 'blockers': result['blockers']}), flush=True)
finally:
    lock.release(lease)
