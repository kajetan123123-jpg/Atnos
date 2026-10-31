"""Real cover render probe from retained licensed footage; no order or budget mutations."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.update(TEMP='E:/Atnos/Temp', TMP='E:/Atnos/Temp', PYTHONIOENCODING='utf-8')
from atnos.production import cover_visuals
from atnos.production.order_store import file_hash
from atnos.production.request_fidelity import script_issues
from atnos.production.steps.order_script import EditorialScript

source = Path('E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos/data/production/wealth_blueprint/prod-27f6f8e1e708')
output = Path('E:/Atnos/App/reports/readiness-20260909/cover-visuals-live')
if output.exists():
    raise RuntimeError('Probe already exists; do not overwrite its evidence')
script = json.loads((source / 'script.json').read_text(encoding='utf-8'))
plan = json.loads((source / 'scene_plan.json').read_text(encoding='utf-8'))
assets = {row['asset_id']: json.loads((source / 'assets' / f"{row['asset_id']}.json").read_text(encoding='utf-8'))
          for row in plan['scenes'] if row['asset_id']}
context = SimpleNamespace(artifact_dir=source, artifacts={'script': script, 'visuals': {**plan, 'assets': assets}})
selected = cover_visuals.choices(context)[0]
paths, evidence = cover_visuals.render(output, headline=script['segments'][0]['on_screen'],
    detail=script['segments'][-1]['on_screen'], visual=selected)
original = EditorialScript.model_validate({key: script[key] for key in ('title', 'description', 'segments', 'claims')})
result = {'scope': 'New renderer using real retained footage; no production counters changed and no publishing',
    'source': selected, 'outputs': [{'path': str(path), 'sha256': file_hash(path)} for path in [*paths, *evidence]],
    'old_script_new_validation': script_issues(original, {'topic': 'Build a weekly savings habit that lasts'})}
(output / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps({'outputs': [str(path) for path in paths], 'caught_old_script_issues': len(result['old_script_new_validation'])}))
