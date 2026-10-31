"""One real repair and review of a retained failure, without resuming its exhausted order."""
import asyncio
import argparse
from dataclasses import replace
import json
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--thinking', action='store_true')
parser.add_argument('--variant', choices=['initial', 'cast_cleanup', 'word_allocation', 'word_allocation_v2', 'word_allocation_v3', 'word_ranges', 'editorial_patch', 'ending_patch', 'ending_patch_v2'], default='initial')
args = parser.parse_args()
name = {'initial': 'story-scoped-repair-live', 'cast_cleanup': 'story-cast-repair-live',
        'word_allocation': 'story-allocated-repair-live', 'word_allocation_v2': 'story-allocated-repair-v2-live',
        'word_allocation_v3': 'story-allocated-repair-v3-live', 'word_ranges': 'story-word-ranges-live',
        'editorial_patch': 'story-editorial-patch-live', 'ending_patch': 'story-ending-patch-live',
        'ending_patch_v2': 'story-ending-patch-v2-live'}[args.variant]
ending_patch = args.variant.startswith('ending_patch')
if args.thinking:
    name = name.removesuffix('-live') + '-thinking'
root = Path('E:/Atnos/App/reports/readiness-20260909') / name
root.mkdir(parents=True, exist_ok=True)
record_path = root / 'result.json'
if record_path.exists():
    raise SystemExit('This bounded experiment already has a result or uncertain start; inspect its retained evidence.')
os.environ.update(ATNOS_ENV_FILE='E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/config/.env',
                  TEMP='E:/Atnos/Temp', TMP='E:/Atnos/Temp')
from atnos.core.config import get_settings
from atnos.production import fiction_script
from atnos.production.order_store import ProductionOrderStore, file_hash
from atnos.production.project_profile import content_hash
from atnos.production.steps.common import write_json
from atnos.services.gpu_runtime_lock_service import GpuRuntimeLockService

settings = get_settings()
store = ProductionOrderStore(settings)
source_id = 'prod-f27a1d73f865' if ending_patch else {'editorial_patch': 'prod-ea512c0e4efd'}.get(args.variant, 'prod-5879e22eb439')
source = store.get(source_id)
if source['worker_live']:
    raise SystemExit('Source production is still working')
stage = 'script' if ending_patch else 'script_draft'
draft = next(row for row in source['checkpoints'] if row['stage'] == stage and row['status'] == 'done')
review = next(row for row in source['checkpoints'] if row['stage'] == 'script_review/0' and row['status'] == 'done')
if any(not store._valid_files(Path(source['artifact_dir']), row['files_json']) for row in (draft, review)):
    raise SystemExit('Source evidence changed')
script = fiction_script.StoryScript.model_validate({key: draft['output'][key] for key in fiction_script.StoryScript.model_fields})
context = replace(store.context(source_id), artifact_dir=root)
context.artifacts['project_profile']['production']['script_model']['thinking'] = args.thinking
inputs = {'source_order': source_id, 'script': draft['output'], 'review': review['output'], 'profile': context.artifacts['project_profile'],
          'contract_version': fiction_script.CONTRACT_VERSION, 'production_order_modified': False,
          'maximum_model_calls': 3 if args.variant == 'editorial_patch' else 2, 'paid_api_calls': 0,
          'scope': 'Repair experiment only; not a new production or film.'}
write_json(root / 'inputs.json', inputs)
result = {'status': 'started', 'inputs_sha256': file_hash(root / 'inputs.json'), 'source_order': source_id}
write_json(record_path, result)


async def main():
    lock = GpuRuntimeLockService(settings)
    lease = lock.acquire(owner='readiness-scoped-story-repair', timeout_s=0)
    try:
        issues = review['output']['issues']
        if ending_patch:
            issues = [dict(code='editorial_payoff', segment_ids=['s06'], fields=['text', 'visual_intent', 'on_screen'],
                detail="The ending merely repeats a phone ringing in an empty room. Replace ONLY the final scene with a specific unsettling reveal that changes the meaning of the earlier voice saying 'You left the key on the table', while Elena still holds the key outside. Make the revealed fact concrete and immediately understandable. Preserve the first five scenes, both locations, the single visible character, and the complete 90-115 word budget. The still illustration must depict one clear moment of that reveal, not a montage.")]
            write_json(root / 'editorial-feedback.json', {'author': 'assistant_editorial_review', 'issues': issues,
                'note': 'Explicit local editing experiment; original production remains unchanged.'})
        if args.variant == 'editorial_patch':
            current_review = await fiction_script.review(settings, context, script)
            write_json(root / 'current-initial-review.json', current_review)
            issues = current_review['issues']
        fixed = await fiction_script.repair(settings, context, script, issues) if issues else script
        write_json(root / 'fixed-script.json', fixed.model_dump(mode='json'))
        verdict = await fiction_script.review(settings, context, fixed)
        write_json(root / 'review.json', verdict)
        result.update(status='passed' if verdict['status'] == 'passed' else 'needs_editorial_repair',
            word_count=sum(len(s.text.split()) for s in fixed.segments), script_hash=content_hash(fixed.model_dump(mode='json')),
            locations_unchanged=fixed.locations == script.locations, title_unchanged=fixed.title == script.title,
            description_unchanged=fixed.description == script.description, review=verdict)
        if ending_patch:
            result['first_five_scenes_unchanged'] = fixed.segments[:5] == script.segments[:5]
    except Exception as exc:
        result.update(status='failed', error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        traces = list((root / 'model_calls').glob('*.json'))
        result['model_calls_recorded'] = sum(not p.name.endswith('.raw.json') for p in traces)
        result['production_order_unchanged'] = all(store.get(source_id)[key] == source[key]
            for key in ('status', 'provider_calls', 'reserved_cost_units', 'profile_hash', 'error'))
        result['evidence_files'] = {str(path.relative_to(root)): file_hash(path) for path in traces}
        write_json(record_path, result)
        lock.release(lease)
        print(json.dumps(result), flush=True)


asyncio.run(main())
