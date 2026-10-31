"""Export a verified, completed Atnos trial without publishing it."""
from __future__ import annotations
import json
from pathlib import Path
import shutil

from atnos.core.config import get_settings
from atnos.production.order_store import ProductionBlocked, checkpoint_fingerprints, file_hash


def final_delivery_root() -> Path:
    settings = get_settings()
    root = Path(getattr(settings, "final_delivery_root", None) or "E:/Rolki").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def export_brand_trial(store, job_id, destination):
    state = store.get(job_id)
    if state['status'] != 'ready' or state['request'].get('delivery_mode') != 'local_only':
        raise ProductionBlocked('Only completed local trials can be exported')
    root = store.context(job_id).artifact_dir
    rows = state['checkpoints']
    package = next(r for r in rows if r['stage'] == 'package')['output']
    if not state['profile'].get('brand_kit') or package.get('scope') != 'user_authorized_single_pose_trial':
        raise ProductionBlocked('This exporter requires an authorized single-pose brand trial')
    if package['checkpoint_fingerprints'] != checkpoint_fingerprints(rows):
        raise ProductionBlocked('Production evidence changed after packaging')
    for row in rows:
        if row['status'] != 'done' or not store._valid_files(root, json.dumps(row['files'])):
            raise ProductionBlocked('Production evidence is incomplete or changed: '+row['stage'])
    destination = Path(destination).resolve()
    allowed = final_delivery_root()
    if not destination.is_relative_to(allowed):
        raise ValueError(f'Trial deliveries must stay inside {allowed}')
    source_doc = Path(__file__).resolve().parents[3]/'config/brand-kits/wealth-blueprint-alex-01/TRIAL-RULES.md'
    sources = {
        'Wealth-Blueprint-Alex-01-Trial.mp4': root/'short.mp4',
        'Wealth-Blueprint-Alex-01-Trial.srt': root/'captions.srt',
        'Poster.png': root/'decoded-frames/s01.png',
        'Zasady-wizualne.md': source_doc,
        'production-package.json': root/'package.json',
        'supervisor-review.json': root/'supervisor-review.json',
        'media-qa.json': root/'media_qa.json',
        'voice-qa.json': root/'voice.json',
    }
    # The frozen contract is part of package.json; avoid relying on a mutable profile path.
    for folder in ('image_requests', 'image_revisions'):
        for source in (root/folder).glob('*.json'):
            sources['provenance/'+folder+'/'+source.name] = source
    for source in (root/'decoded-frames').glob('*.png'):
        sources['scenes/'+source.name] = source
    sources['provenance/alex-single-pose.json'] = root/'brand_assets/alex_single_pose.json'
    sources['provenance/authorization.json'] = root/'single-pose-authorization.json'
    # Preflight the entire set before writing any delivery bytes.
    for name, source in sources.items():
        if not source.is_file() or not source.stat().st_size:
            raise FileNotFoundError(source)
        target = destination/name
        if target.exists() and file_hash(target) != file_hash(source):
            raise FileExistsError('Use a new delivery directory to preserve the previous trial: '+str(target))
    files = []
    for name, source in sources.items():
        target = destination/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        expected = file_hash(source)
        if file_hash(target) != expected:
            raise OSError('Delivery copy failed verification: '+str(target))
        files.append({'path': name, 'sha256': expected, 'bytes': target.stat().st_size})
    manifest = {'job_id': job_id, 'status': 'exported', 'scope': package['scope'],
        'output_file': str(destination/'Wealth-Blueprint-Alex-01-Trial.mp4'),
        'producer': 'Atnos production-order worker', 'publish': False,
        'duration_seconds': 45, 'language': 'en', 'provider_call_reservations': state['provider_calls'],
        'paid_API_reserved_usd': state['reserved_cost_units']/1e6,
        'external_imagegen_cost': 'unavailable', 'files': files}
    (destination/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return manifest
