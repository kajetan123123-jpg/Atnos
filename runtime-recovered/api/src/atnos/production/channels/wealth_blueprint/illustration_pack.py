"""Versioned, hash-bound WB layers. Candidate preview is explicit and never approval."""
import json
from pathlib import Path
from PIL import Image
from atnos.production.project_manifest import AssetProvenance, SceneAsset, QAResult, QAAction, file_sha256, deterministic_hash

PACK_PATH = Path(__file__).resolve().parents[5] / 'config' / 'asset_libraries' / 'wealth_blueprint' / 'pilot_a_layers.json'
ROLES = ('background', 'income', 'savings', 'goal', 'costs')

def load_layers(*, candidate_preview=False, pack_path=None):
    path = Path(pack_path or PACK_PATH)
    if not path.is_file(): raise ValueError('WAITING_FOR_ASSETS: missing WB pilot_a_layers.json')
    pack = json.loads(path.read_text(encoding='utf-8'))
    if pack.get('channel') != 'wealth_blueprint': raise ValueError('WB illustration pack channel mismatch')
    assets = []
    for role in ROLES:
        row = pack.get('layers', {}).get(role)
        if not row: raise ValueError(f'WAITING_FOR_ASSETS: missing WB layer {role}')
        source = Path(row['path'])
        sha = file_sha256(source)
        if not source.is_absolute() or source.drive.upper() != 'E:': raise ValueError('WB layer must be on E:')
        if not sha or sha != row.get('sha256'): raise ValueError('WB layer changed after review: ' + role)
        if row.get('classification') != 'production_candidate' or row.get('visual_review') != 'technical_pass':
            raise ValueError('WB reference/rejected/unreviewed layer forbidden: ' + role)
        approval = row.get('user_approval') or {}
        if not candidate_preview and not (approval.get('sha256') == sha and approval.get('evidence') and approval.get('by') == 'user'):
            raise ValueError('WAITING_FOR_USER_ASSET_REVIEW: ' + role)
        with Image.open(source) as im:
            if role != 'background' and ('A' not in im.getbands() or im.getchannel('A').getextrema()[0] != 0):
                raise ValueError('WB animated layer requires transparent background: ' + role)
            width, height = im.size
        assets.append(SceneAsset(asset_id='wb-layer-'+role, role='selected_image', path=str(source), exists=True,
            provenance=AssetProvenance(provider='atnos_comfy_channels', model=row['model'], workflow=row['workflow'],
                prompt=row['prompt'], seed=row['seed'], input_hash=deterministic_hash(row), artifact_hash=sha,
                generation_parameters={'layer_role':role, 'production_role': 'background' if role=='background' else 'object',
                    'width':width,'height':height,'version':pack['version'], 'fit':'contain', 'preserve_alpha':role!='background',
                    'user_approved':bool(approval), 'candidate_preview':candidate_preview,'metadata_path':row['metadata_path']}),
            qa=QAResult(status='passed', action=QAAction.PASS, reasons=['Technical layer check only; user appearance approval tracked separately.'])))
    return assets
