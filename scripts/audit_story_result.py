"""Read-only verification and readable export of an existing story preview."""
import argparse
import json
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('run_name')
args = parser.parse_args()
if args.run_name not in {'story-children_stories-v1', 'story-children_stories-v2', 'story-children_stories-v3', 'story-horror_stories-v3', 'story-horror_stories-v4', 'story-horror_stories-v5', 'story-horror_stories-v6', 'story-horror_stories-v7', 'story-horror_stories-v8'}:
    raise SystemExit('Unsupported retained story run')
root = Path('E:/Atnos/App/reports/readiness-20260909') / args.run_name
job_id = json.loads((root / 'order.json').read_text(encoding='utf-8'))['id']
os.environ.update(ATNOS_ENV_FILE='E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/config/.env',
                  TEMP='E:/Atnos/Temp', TMP='E:/Atnos/Temp')
from atnos.core.config import get_settings
from atnos.production.fiction_script import StoryScript, script_issues
from atnos.production.fiction_messages import story_issue_message
from atnos.production.order_store import ProductionOrderStore, checkpoint_resolved
from atnos.production.project_profile import content_hash

store = ProductionOrderStore(get_settings())
state = store.get(job_id)
artifact_dir = Path(state['artifact_dir'])
rows = state['checkpoints']
row = next((r for r in rows if r['stage'] == 'script' and r['status'] == 'done'), None)
result = {key: state[key] for key in ('id', 'status', 'worker_live', 'current_step', 'error', 'provider_calls', 'reserved_cost_units', 'profile_hash')}
result.update(scope='Script and continuity only; no generated video, audio, measured duration or publication.',
              failed_attempts=[{'stage': e['stage'], **e['detail']} for e in state['history'] if e['event'] == 'stage_failed'],
              all_files_match=all(store._valid_files(artifact_dir, r['files_json'] or '[]') for r in rows if r['status'] == 'done'),
              all_checkpoints_resolved=all(checkpoint_resolved(r, rows) for r in rows), result_confirmed=False)
if row:
    payload = row['output']
    script = StoryScript.model_validate({key: payload[key] for key in StoryScript.model_fields})
    issues = script_issues(script, state['profile']['production'], state['request'])
    qa = payload['qa']
    reviewed = next((r for r in rows if r['stage'] == f"script_review/{qa['revision']}"), None)
    result.update(title=script.title, word_count=payload['word_count'], script_issues=issues,
                  review_matches=qa['script_hash'] == content_hash(script.model_dump(mode='json')) and bool(reviewed and reviewed['output'] == qa),
                  human_review='not_performed')
    result['result_confirmed'] = (state['status'] == 'script_ready' and not state['worker_live'] and result['all_files_match']
        and result['all_checkpoints_resolved'] and result['review_matches'] and not issues and state['reserved_cost_units'] == 0)
    lines = ['# ' + script.title, '', script.description, '',
             ('Scenariusz po angielsku. Aktualna kontrola automatyczna zaliczona; obraz, głos i czas filmu nie zostały jeszcze sprawdzone.'
              if result['result_confirmed'] else
              'Scenariusz po angielsku. Aktualna kontrola wymaga poprawek; historyczny status zlecenia pozostawiono bez zmian.'), '']
    if issues:
        lines.extend(['## Wykryte problemy', ''])
        lines.extend(['- ' + story_issue_message(issue) for issue in issues])
        lines.append('')
    for index, segment in enumerate(script.segments, 1):
        lines.extend([f'## Scena {index}: {segment.on_screen}', '', segment.text, '', '*Obraz:* ' + segment.visual_intent, ''])
    lines.extend(['## Postaci', ''])
    lines.extend(['- **' + character.name + '**: ' + character.appearance for character in script.characters])
    (root / 'STORY.md').write_text('\n'.join(lines), encoding='utf-8')
(root / 'result-inspection.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result))
