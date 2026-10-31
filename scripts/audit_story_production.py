"""Execute one real, local story preview using durable Atnos production orders."""
import argparse
import asyncio
import json
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('project', choices=['children_stories', 'horror_stories'])
parser.add_argument('--resume', action='store_true')
parser.add_argument('--version', choices=['v1', 'v2', 'v3', 'v4', 'v5', 'v6', 'v7', 'v8'], default='v1')
args = parser.parse_args()
root = Path('E:/Atnos/App/reports/readiness-20260909') / ('story-' + args.project + '-' + args.version)
root.mkdir(parents=True, exist_ok=True)
os.environ.update(ATNOS_ENV_FILE='E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/config/.env',
                  TEMP='E:/Atnos/Temp', TMP='E:/Atnos/Temp', PYTHONIOENCODING='utf-8')
from atnos.core.config import get_settings
from atnos.intelligence.storage import connect, utc_now_iso
from atnos.production.order_execution import run_order
from atnos.production.order_store import ProductionOrderStore
from atnos.production.project_profile import ProductionOrder

settings = get_settings().model_copy(update={'runtime_dir': root / 'runtime'})
assert settings.data_dir.resolve() == Path('E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/data').resolve()
# Separate local production identities; no existing YouTube account is repointed.
with connect(settings) as db:
    db.execute("INSERT OR IGNORE INTO intelligence_accounts(account_id,niches,region,last_requested_at) VALUES(?,'[]','US',?)",
               ('local-' + args.project, utc_now_iso()))
    db.commit()
store = ProductionOrderStore(settings)
identity = root / 'order.json'
if identity.exists():
    if not args.resume:
        raise SystemExit('Existing order: inspect and use --resume to continue it.')
    job_id = json.loads(identity.read_text(encoding='utf-8'))['id']
else:
    topic = ('A little fox returns a lost lantern to an owl before bedtime' if args.project == 'children_stories'
             else 'A phone call comes from my locked apartment while I stand outside holding the key')
    direction = ('A calm original English story for children aged four to seven. One fox and one owl, a small problem, kind help and a reassuring ending.'
                 if args.project == 'children_stories' else
                 'An original fictional English suspense story. Plant a clear clue, build tension and end with a coherent unsettling payoff. No claim of real events and no graphic gore.')
    state = store.create(ProductionOrder(project=args.project, topic=topic, direction=direction,
        production_scope='script_preview', delivery_mode='local_only'), request_key='readiness-story-' + args.project + '-' + args.version)
    job_id = state['id']
    identity.write_text(json.dumps({'id': job_id, 'project': args.project}), encoding='utf-8')


def snapshot():
    state = store.get(job_id)
    row = {key: state.get(key) for key in ('id', 'status', 'worker_live', 'current_step', 'error',
           'provider_calls', 'reserved_cost_units', 'artifact_dir', 'profile_hash')}
    (root / 'status.json').write_text(json.dumps(row, indent=2), encoding='utf-8')
    return row


async def main():
    task = asyncio.create_task(run_order(settings, job_id))
    marker = None
    try:
        while not task.done():
            await asyncio.wait([task], timeout=10)
            row = snapshot()
            latest = (row['status'], row['current_step'], row['provider_calls'])
            if latest != marker:
                print(json.dumps(row), flush=True)
                marker = latest
        await task
    finally:
        print(json.dumps(snapshot()), flush=True)


asyncio.run(main())
