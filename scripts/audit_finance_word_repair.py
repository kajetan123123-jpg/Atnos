"""Compare one bounded local repair and review on the retained canonical form failure."""
import argparse
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import time

parser = argparse.ArgumentParser()
parser.add_argument('--model', choices=['qwen3:14b', 'qwen3:8b'], required=True)
args = parser.parse_args()
os.environ.update(ATNOS_ENV_FILE='E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/config/.env',
                  TEMP='E:/Atnos/Temp', TMP='E:/Atnos/Temp')
from atnos.core.config import get_settings
from atnos.production.order_store import ProductionOrderStore, file_hash
from atnos.production.steps.order_script import EditorialScript, repair, review, SCRIPT_REPAIR_PROMPT_VERSION
from atnos.production.steps.common import write_json
from atnos.services.gpu_runtime_lock_service import GpuRuntimeLockService

root = Path('E:/Atnos/App/reports/readiness-20260909/canonical-form-20260914') / ('word-repair-'+args.model.replace(':','-')+f'-v{SCRIPT_REPAIR_PROMPT_VERSION}')
if root.exists():
    raise SystemExit('Inspect the existing bounded attempt; do not repeat it or overwrite evidence.')
settings = get_settings()
store = ProductionOrderStore(settings)
source_id = 'prod-e61dbb50836c'
source = store.get(source_id)
assert not source['worker_live']
original = Path(source['artifact_dir'])
script = EditorialScript.model_validate_json((original/'script_draft.json').read_text(encoding='utf-8'))
issues = json.loads((original/'script/review_0.json').read_text(encoding='utf-8'))['issues']
before_files = {str(path.relative_to(original)): file_hash(path) for path in original.rglob('*') if path.is_file()}
root.mkdir()
context = replace(store.context(source_id), artifact_dir=root)
context.artifacts['script_calculations'] = json.loads((original/'script_calculations.json').read_text(encoding='utf-8'))
context.artifacts['project_profile']['production']['script_model']['generator'] = args.model
result = {'status':'started', 'source_job':source_id, 'source_script_sha256':file_hash(original/'script_draft.json'),
          'generator':args.model, 'reviewer':context.artifacts['project_profile']['production']['script_model']['reviewer'],
          'repair_prompt_version':SCRIPT_REPAIR_PROMPT_VERSION, 'max_model_calls':2, 'paid_api_calls':0,
          'scope':'Diagnostic repair and review of an unchanged failed order; not a new film.'}
write_json(root/'inputs.json', {'script':script.model_dump(mode='json'), 'issues':issues, 'budget':context.artifacts['project_profile']['production']['word_budget']})
write_json(root/'result.json', result)

async def main():
    lock = GpuRuntimeLockService(settings)
    lease = lock.acquire(owner='canonical-finance-word-repair', timeout_s=0)
    started = time.monotonic()
    try:
        fixed = await repair(settings, context, script, issues)
        result['repair_seconds'] = round(time.monotonic()-started, 2)
        write_json(root/'fixed-script.json', fixed.model_dump(mode='json'))
        verdict = await review(settings, context, fixed)
        write_json(root/'review.json', verdict)
        result.update(status='passed' if verdict['status']=='passed' else 'needs_editorial_repair',
                      word_count=sum(len(s.text.split()) for s in fixed.segments), review=verdict)
    except Exception as exc:
        result.update(status='failed', error_type=type(exc).__name__, error=str(exc))
    finally:
        result['total_seconds'] = round(time.monotonic()-started, 2)
        result['model_calls_recorded'] = len([p for p in (root/'model_calls').glob('*.json') if not p.name.endswith('.raw.json')])
        result['source_files_unchanged'] = before_files == {str(p.relative_to(original)): file_hash(p) for p in original.rglob('*') if p.is_file()}
        result['source_order_unchanged'] = all(store.get(source_id)[k]==source[k] for k in ('status','provider_calls','reserved_cost_units','profile_hash','error'))
        write_json(root/'result.json', result)
        lock.release(lease)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        assert result['source_order_unchanged'] and result['source_files_unchanged']

asyncio.run(main())
