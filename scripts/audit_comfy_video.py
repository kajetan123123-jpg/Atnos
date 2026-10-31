"""Render one short LTX clip with Atnos's workflow and retain exact prompt evidence."""
import argparse
import json
import os
from pathlib import Path
import time
import httpx

ROOT = Path('E:/Atnos/App/reports/readiness-20260909')
os.environ['ATNOS_ENV_FILE'] = 'E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/config/.env'
os.environ['TEMP'] = os.environ['TMP'] = 'E:/Atnos/Temp'
from atnos.core.config import get_settings
from atnos.production.steps import video_generator as video

parser = argparse.ArgumentParser()
parser.add_argument('--run-name', default='', choices=['', 'resumed'])
arguments = parser.parse_args()
run_root = ROOT / 'ltx-resumed' if arguments.run_name else ROOT
run_root.mkdir(parents=True, exist_ok=True)
target = run_root / 'comfy-video.json'
if target.exists():
    print(target.read_text(encoding='utf-8'))
    raise SystemExit('Probe already recorded; inspect its existing prompt before any new render.')
s = get_settings()
base = s.comfyui_base_url.rstrip('/')
started = time.monotonic()
report = {'status': 'checking', 'backend': 'ltx', 'scope': 'One second, existing Atnos LTX workflow; no fallback clip.'}


def save():
    report['seconds'] = round(time.monotonic()-started, 2)
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')


save()
try:
    with httpx.Client(timeout=20) as client:
        queue = client.get(base+'/queue')
        queue.raise_for_status()
        if queue.json().get('queue_running') or queue.json().get('queue_pending'):
            raise RuntimeError('ComfyUI queue is busy; no test submitted.')
        workflow = video._default_ltx_workflow()
        info_response = client.get(base+'/object_info')
        info_response.raise_for_status()
        info = info_response.json()
        missing = sorted(set(video._workflow_class_types(workflow)) - set(info))
        mismatches = video._workflow_runtime_choice_mismatches(workflow, info)
        if missing or mismatches:
            report.update(status='blocked', missing_nodes=missing, unavailable_choices=mismatches)
        else:
            prompt = video._inject_scene(workflow, {'duration_s': 1, 'visual_prompt': 'A small friendly green dragon turns its head in a sunny forest clearing, illustrated storybook style', 'motion_style': 'Gentle head movement, fixed camera, no text'}, backend='ltx')
            prompt['7']['inputs']['filename_prefix'] = 'atnos_readiness_20260910_ltx'
            (run_root / 'comfy-video-workflow.json').write_text(json.dumps(prompt, indent=2), encoding='utf-8')
            response = client.post(base+'/prompt', json={'prompt': prompt})
            response.raise_for_status()
            prompt_id = response.json()['prompt_id']
            report.update(status='running', prompt_id=prompt_id)
            save()
            while time.monotonic()-started < 300:
                response = client.get(base+'/history/'+prompt_id)
                response.raise_for_status()
                history = response.json().get(prompt_id)
                if history:
                    (run_root / 'comfy-video-history.json').write_text(json.dumps(history, indent=2), encoding='utf-8')
                    if history.get('status', {}).get('status_str') == 'error':
                        report.update(status='failed', error='ComfyUI execution failed; see retained history.')
                        break
                    outputs = history.get('outputs', {})
                    candidates = [item for output in outputs.values() for key in ['images','videos','gifs']
                                  for item in output.get(key, []) if str(item.get('filename','')).lower().endswith('.mp4')]
                    if candidates:
                        item = candidates[0]
                        media = client.get(base+'/view', params={key: item[key] for key in ['filename','subfolder','type'] if key in item})
                        media.raise_for_status()
                        path = run_root / 'comfy-ltx-sample.mp4'
                        path.write_bytes(media.content)
                        report.update(status='executed', output=str(path), bytes=path.stat().st_size, exact_prompt_output=True)
                        break
                time.sleep(1)
            else:
                report.update(status='observation_deadline', note='Recheck this prompt ID; timeout does not prove the ComfyUI job stopped.')
except Exception as exc:
    report.update(status='error', error_type=type(exc).__name__, error=str(exc)[:600])
save()
print(json.dumps(report, ensure_ascii=False))
