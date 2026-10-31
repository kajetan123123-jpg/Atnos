"""Two single-call local diagnostics on retained failed scripts; no order mutation."""
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time

os.environ.update({
    'ATNOS_ENV_FILE': 'E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/config/.env',
    'TEMP': 'E:/Atnos/Temp', 'TMP': 'E:/Atnos/Temp', 'PYTHONIOENCODING': 'utf-8',
})
from atnos.core.config import get_settings
from atnos.production.order_store import ProductionOrderStore
from atnos.production.steps.order_script import EditorialScript, repair, script_issues, SCRIPT_REPAIR_PROMPT_VERSION
from atnos.services.gpu_runtime_lock_service import GpuRuntimeLockService

root = Path('E:/Atnos/App/reports/readiness-20260909/script-repairs-live')
if root.exists():
    raise SystemExit('Diagnostic already exists; inspect it before any additional model calls.')
settings = get_settings()
store = ProductionOrderStore(settings)
lock = GpuRuntimeLockService(settings)
lease = lock.acquire(owner='readiness-script-repairs', timeout_s=0)
root.mkdir()


async def main():
    results = []
    for name, job in [('length', 'prod-6f8c3d78644b'), ('unsupported-amount', 'prod-d8bef36142f2')]:
        context = store.context(job)
        source = context.artifact_dir
        script = EditorialScript.model_validate_json((source / 'script_draft.json').read_text(encoding='utf-8'))
        issues = json.loads((source / 'script/review_0.json').read_text(encoding='utf-8'))['issues']
        context.artifacts['script_calculations'] = json.loads((source / 'script_calculations.json').read_text(encoding='utf-8'))
        context.artifact_dir = root / name
        context.artifact_dir.mkdir()
        started = time.monotonic()
        row = {'case': name, 'source_job': job, 'source_sha256': hashlib.sha256((source / 'script_draft.json').read_bytes()).hexdigest(),
               'started_at': datetime.now(timezone.utc).isoformat(), 'status': 'running', 'max_model_calls': 1,
               'repair_prompt_version': SCRIPT_REPAIR_PROMPT_VERSION,
               'scope': 'Diagnostic on a copy; original order attempts, checkpoints and budgets are unchanged.'}
        results.append(row)
        (root / 'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
        try:
            fixed = await repair(settings, context, script, issues)
            row.update(status='validated', word_count=sum(len(s.text.split()) for s in fixed.segments),
                       remaining_deterministic_issues=script_issues(fixed, context.artifacts['project_profile']),
                       semantic_review='not_performed')
            (context.artifact_dir / 'corrected.json').write_text(fixed.model_dump_json(indent=2), encoding='utf-8')
        except Exception as exc:
            row.update(status='failed', error_type=type(exc).__name__, error=str(exc))
        row['seconds'] = round(time.monotonic() - started, 2)
        (root / 'results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
        print(json.dumps(row), flush=True)


try:
    asyncio.run(main())
finally:
    lock.release(lease)
