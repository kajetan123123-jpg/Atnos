"""Generate two real continuity samples from an approved story, preserving its order."""
import argparse
import json
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--version', choices=['v1', 'v2', 'v3'], default='v1')
parser.add_argument('--checkpoint', choices=['sd_xl_base_1.0_0.9vae.safetensors', 'Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors'], default='sd_xl_base_1.0_0.9vae.safetensors')
args = parser.parse_args()
root = Path('E:/Atnos/App/reports/readiness-20260909') / ('story-illustrations-' + args.version)
root.mkdir(parents=True, exist_ok=True)
os.environ.update(ATNOS_ENV_FILE='E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/config/.env',
    TEMP='E:/Atnos/Temp', TMP='E:/Atnos/Temp', HF_HOME='E:/Atnos/Storage/ToolCaches/huggingface',
    TORCH_HOME='E:/Atnos/Storage/ToolCaches/torch', XDG_CACHE_HOME='E:/Atnos/Storage/ToolCaches')
import httpx
from atnos.core.config import get_settings
from atnos.production.fiction_script import StoryScript, script_issues
from atnos.production.order_store import ProductionOrderStore
from atnos.production.story_images import generate_illustration, illustration_prompt
from atnos.services.gpu_runtime_lock_service import GpuRuntimeLockService

settings = get_settings().model_copy(update={'runtime_dir': root / 'runtime'})
store = ProductionOrderStore(settings)
order = store.get('prod-5d5f9fb968cf')
row = next(r for r in order['checkpoints'] if r['stage'] == 'script' and r['status'] == 'done')
if not store._valid_files(Path(order['artifact_dir']), row['files_json']):
    raise SystemExit('The source story file changed after QA')
payload = row['output']
script = StoryScript.model_validate({key: payload[key] for key in StoryScript.model_fields})
config = order['profile']['production']
if script_issues(script, config, order['request']):
    raise SystemExit('Source story no longer passes its deterministic review')
lock = GpuRuntimeLockService(settings)
lease = lock.acquire(owner='readiness-story-illustrations', timeout_s=0)
try:
    with httpx.Client(timeout=30) as client:
        response = client.get(settings.ollama_base_url.rstrip('/') + '/api/ps')
        response.raise_for_status()
        for model in response.json().get('models', []):
            unloaded = client.post(settings.ollama_base_url.rstrip('/') + '/api/generate', json={'model': model['name'], 'keep_alive': 0})
            unloaded.raise_for_status()
    for segment_id in ('s01', 's03'):
        print(json.dumps({'stage': 'illustration', 'segment_id': segment_id, 'status': 'starting'}), flush=True)
        result = generate_illustration(settings, root / segment_id, prompt=illustration_prompt(script, segment_id, config),
                                      checkpoint=args.checkpoint, seed=20260913)
        print(json.dumps(result), flush=True)
finally:
    lock.release(lease)
