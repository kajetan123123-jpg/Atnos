"""Read a retained readiness run and record its real result without resuming it."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--run-name', required=True)
args = parser.parse_args()
if not args.run_name or any(char not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for char in args.run_name):
    parser.error('Use a lowercase report directory name.')
report = Path('E:/Atnos/App/reports/readiness-20260909') / args.run_name
identity = json.loads((report / 'order.json').read_text(encoding='utf-8'))
os.environ.update({'ATNOS_ENV_FILE': 'E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/config/.env',
                   'TEMP': 'E:/Atnos/Temp', 'TMP': 'E:/Atnos/Temp', 'PYTHONIOENCODING': 'utf-8'})
os.environ['PATH'] = 'E:/Atnos/Tools/FFmpeg/bin' + os.pathsep + os.environ.get('PATH', '')

from atnos.core.config import get_settings
from atnos.production.order_store import ProductionOrderStore, checkpoint_fingerprints, file_hash
from atnos.production.qa_gate import probe_media
from atnos.production.request_fidelity import script_issues
from atnos.production.steps.order_script import EditorialScript

settings = get_settings().model_copy(update={'runtime_dir': report / 'runtime'})
state = ProductionOrderStore(settings).get(identity['id'])
root = Path(state['artifact_dir']).resolve()
if not root.is_relative_to(Path('E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/data/production').resolve()):
    raise ValueError('Unexpected production root')
rows = {row['stage']: row for row in state['checkpoints']}
result = {key: state[key] for key in ('id', 'status', 'current_step', 'worker_live', 'error',
    'provider_calls', 'max_provider_calls', 'reserved_cost_units', 'created_at', 'updated_at', 'profile_hash')}
result.update(checked_at=datetime.now(timezone.utc).isoformat(), artifact_dir=str(root),
              scope='Read-only result inspection; no inference, publishing, resumption or counter changes.',
              human_review='not_performed', stage_attempts={key: row['attempts'] for key, row in rows.items()})
starts = {}
durations = []
for event in state['history']:
    if event['event'] == 'stage_started':
        starts[event['stage']] = event
    elif event['event'] in {'stage_completed', 'stage_failed'} and event['stage'] in starts:
        start = starts.pop(event['stage'])
        durations.append({'stage': event['stage'], 'attempt': start['detail'].get('attempt'),
            'status': event['event'], 'seconds': round((datetime.fromisoformat(event['created_at'])
                - datetime.fromisoformat(start['created_at'])).total_seconds(), 3)})
result['stage_durations'] = durations
result['failures'] = [{'stage': event['stage'], 'detail': event['detail']} for event in state['history']
                       if event['event'] in {'stage_failed', 'blocked'}]
if rows.get('script', {}).get('status') == 'done':
    raw = rows['script']['output']
    script = EditorialScript.model_validate({key: raw[key] for key in ('title', 'description', 'segments', 'claims')})
    result['script'] = {'title': script.title, 'word_count': sum(len(s.text.split()) for s in script.segments),
        'request_fidelity_issues': script_issues(script, state['request']),
        'revision': max((int(key.rsplit('/', 1)[1]) for key in rows if key.startswith('script_review/')), default=0)}
result['quality'] = {stage: rows[stage]['output'] for stage in ('media_qa', 'semantic_qa')
                     if rows.get(stage, {}).get('status') == 'done'}
package = rows.get('package', {}).get('output', {})
checks = []
for entry in rows.get('package', {}).get('files', []):
    path = (root / entry['path']).resolve()
    valid = path.is_relative_to(root) and path.is_file() and file_hash(path) == entry['sha256']
    checks.append({'path': entry['path'], 'valid': valid})
result['package'] = {'exists': bool(package), 'file_count': len(checks),
    'all_hashes_match': bool(checks) and all(item['valid'] for item in checks),
    'invalid_files': [item['path'] for item in checks if not item['valid']],
    'deliverables': package.get('deliverables', {}), 'publish': package.get('publish'),
    'checkpoints_match': bool(package) and package.get('checkpoint_fingerprints') == checkpoint_fingerprints(state['checkpoints'])}
video = root / 'assets/final_short.mp4'
if video.is_file():
    result['video'] = {'path': str(video), 'sha256': file_hash(video), 'probe': probe_media(video)}
result['result_confirmed'] = (state['status'] == 'ready' and not state['worker_live']
    and result['package']['all_hashes_match'] and result['package']['checkpoints_match'] and package.get('publish') is False
    and not result.get('script', {}).get('request_fidelity_issues', ['no script']))
(report / 'result-inspection.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({key: result[key] for key in ('id', 'status', 'current_step', 'provider_calls',
    'reserved_cost_units', 'result_confirmed', 'package')}, ensure_ascii=False))
