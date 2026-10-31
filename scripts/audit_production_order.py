"""Inspect or execute one local-only order through the real durable production pipeline."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--execute', action='store_true')
parser.add_argument('--resume', action='store_true')
parser.add_argument('--run-name', default='full-production')
parser.add_argument('--edited-script', type=Path, help='Explicit editorial test input; requires --resume and remains subject to QA.')
args = parser.parse_args()
if not args.run_name or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in args.run_name):
    parser.error('--run-name must contain only lowercase letters, numbers and hyphens')
REPORT = Path('E:/Atnos/App/reports/readiness-20260909') / args.run_name
REPORT.mkdir(parents=True, exist_ok=True)
os.environ.update({
    'ATNOS_ENV_FILE': 'E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/config/.env',
    'TEMP': 'E:/Atnos/Temp', 'TMP': 'E:/Atnos/Temp', 'PYTHONIOENCODING': 'utf-8',
    'HF_HOME': 'E:/Atnos/Storage/ToolCaches/huggingface',
    'TORCH_HOME': 'E:/Atnos/Storage/ToolCaches/torch',
    'XDG_CACHE_HOME': 'E:/Atnos/Storage/ToolCaches',
})
os.environ['PATH'] = 'E:/Atnos/Tools/FFmpeg/bin' + os.pathsep + os.environ.get('PATH', '')
from atnos.core.config import get_settings
from atnos.intelligence.storage import connect
from atnos.production.order_execution import run_order
from atnos.production.order_store import ProductionOrderStore
from atnos.production.project_profile import ProductionOrder, load_project_profile

settings = get_settings().model_copy(update={'runtime_dir': REPORT / 'runtime'})
if settings.data_dir.resolve() != Path('E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/data').resolve():
    raise RuntimeError('Unexpected canonical data root')
profile = load_project_profile(settings, 'wealth_blueprint')
config = profile['production']
with connect(settings) as db:
    account_exists = bool(db.execute('SELECT 1 FROM intelligence_accounts WHERE account_id=?',
                                    (profile['account_id'],)).fetchone())
inspection = {'checked_at': datetime.now(timezone.utc).isoformat(), 'account_exists': account_exists,
              'required_inputs': [{'path': value, 'exists': (settings.data_dir/value).is_file()}
                  for value in [config['reference_profile'], *config['assets']['manifests']]],
              'cost_limit_usd': config['max_cost_usd'], 'provider_call_limit': config['max_provider_calls'],
              'elevenlabs_configured': bool(settings.elevenlabs_api_key), 'model': config['script_model']['generator']}
(REPORT / 'inspection.json').write_text(json.dumps(inspection, indent=2), encoding='utf-8')
print(json.dumps(inspection), flush=True)
if not args.execute:
    raise SystemExit(0)
if not account_exists or not all(item['exists'] for item in inspection['required_inputs']):
    raise SystemExit('Production inputs are incomplete; no order created.')
store = ProductionOrderStore(settings)
identity_file = REPORT / 'order.json'
if identity_file.exists():
    job_id = json.loads(identity_file.read_text(encoding='utf-8'))['id']
    if not args.resume:
        raise SystemExit('Order already exists; inspect before explicit --resume.')
else:
    order = ProductionOrder(project='wealth_blueprint', delivery_mode='local_only',
        topic='Build a weekly savings habit that lasts',
        direction='English educational Short. Explain checking essential expenses, selecting an affordable regular transfer, and reviewing the habit. Avoid specific products, interest rates, tax claims or guaranteed outcomes.',
        optional_notes='Local end-to-end readiness sample. Keep the advice practical and the visuals directly relevant. Do not publish or send the result.')
    request_key = ('readiness-full-production-20260911-v1' if args.run_name == 'full-production'
                   else 'readiness-' + args.run_name)
    created = store.create(order, request_key=request_key)
    job_id = created['id']
    identity_file.write_text(json.dumps({'id': job_id, 'scope': 'Real durable production stages; local-only, no publishing. Desktop/browser initiation remains to verify.'}), encoding='utf-8')


def snapshot():
    state = store.get(job_id)
    row = {key: state.get(key) for key in ('id', 'status', 'current_step', 'worker_live', 'can_resume',
                                         'reserved_cost_units', 'provider_calls', 'error', 'artifact_dir')}
    row['checked_at'] = datetime.now(timezone.utc).isoformat()
    row['stages'] = [{'stage': item['stage'], 'status': item['status']} for item in state['checkpoints']]
    (REPORT / 'status.json').write_text(json.dumps(row, indent=2, ensure_ascii=False), encoding='utf-8')
    return row


async def main():
    if args.edited_script:
        from atnos.production.editorial_revision import admit
        from atnos.services.gpu_runtime_lock_service import GpuRuntimeLockService
        source = args.edited_script.resolve()
        if not args.resume or not source.is_relative_to(REPORT.resolve()) or not source.is_file():
            raise ValueError('Editorial audit requires --resume and an existing source inside this report directory.')
        archive = REPORT / 'before-editorial-status.json'
        if not archive.exists():
            archive.write_text(json.dumps(snapshot(), indent=2), encoding='utf-8')
        gpu_lock = GpuRuntimeLockService(settings)
        lease = gpu_lock.acquire(owner='readiness-explicit-editorial-review', timeout_s=0)
        try:
            accepted = await admit(settings, job_id, source, approved=True)
        finally:
            gpu_lock.release(lease)
        (REPORT / 'editorial-admission.json').write_text(json.dumps(accepted, indent=2), encoding='utf-8')
        print(json.dumps(accepted), flush=True)
        if accepted['status'] not in {'accepted', 'already_accepted'}:
            raise RuntimeError('Edited integration script did not pass independent QA.')
    task = asyncio.create_task(run_order(settings, job_id))
    previous = None
    try:
        while not task.done():
            done, _ = await asyncio.wait([task], timeout=10)
            row = snapshot()
            marker = (row['status'], row['current_step'], row['provider_calls'])
            if marker != previous:
                print(json.dumps(row, ensure_ascii=False), flush=True)
                previous = marker
        await task
    finally:
        print(json.dumps(snapshot(), ensure_ascii=False), flush=True)


asyncio.run(main())
