"""Reusable pixel observations, never approval of a clip for a particular scene."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from uuid import uuid4

import httpx

from atnos.production.order_store import file_hash
from atnos.production.project_profile import content_hash


def model_revision(settings, model: str) -> dict | None:
    """A model name alone is mutable and cannot identify reusable inference."""
    try:
        with httpx.Client(timeout=5) as client:
            tags = client.get(settings.ollama_base_url.rstrip('/') + '/api/tags')
            tags.raise_for_status()
            version = client.get(settings.ollama_base_url.rstrip('/') + '/api/version')
            version.raise_for_status()
        names = {model, model + ':latest'}
        rows = [row for row in tags.json()['models'] if row.get('name') in names]
        if len(rows) != 1 or not re.fullmatch(r'(?:sha256:)?[a-f0-9]{64}', str(rows[0].get('digest', ''))):
            return None
        server = version.json().get('version')
        if not isinstance(server, str) or not server.strip():
            return None
        return {'name': rows[0]['name'], 'digest': rows[0]['digest'], 'ollama_version': server}
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        return None


def identity(source_sha256: str, config, revision: dict) -> dict:
    from atnos.production import asset_observation, visual_semantic_audit
    return {'source_sha256': source_sha256, 'config': config.model_dump(mode='json'), 'model_revision': revision,
            'frame_count': 6, 'observation_code': file_hash(Path(asset_observation.__file__)),
            'extraction_code': file_hash(Path(visual_semantic_audit.__file__)), 'cache_contract': 1}


def cache_path(settings, key: dict) -> Path:
    root = settings.data_dir.resolve()
    path = (root / 'library-observations' / content_hash(key)).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Observation cache escapes Atnos data')
    return path


def _validate_payload(payload, trace, sheet: Path, key):
    from atnos.production.asset_observation import AssetObservation
    parsed = AssetObservation.model_validate(payload['observation']).model_dump(mode='json')
    raw = trace['response']
    if (trace.get('status') != 'response_received' or trace.get('contract') != 'AssetObservation'
            or raw.get('done_reason') == 'length' or trace.get('model') != key['config']['model']
            or payload.get('model') != key['config']['model'] or payload.get('status') != 'observed'):
        raise ValueError('Observation evidence does not match its model/contract')
    if AssetObservation.model_validate_json(raw['message']['content']).model_dump(mode='json') != parsed:
        raise ValueError('Observation does not match its original model response')
    digest = file_hash(sheet)
    if payload['audit_input']['source_sha256'] != digest or trace['audit_input']['source_sha256'] != digest:
        raise ValueError('Observation contact sheet changed')


def load(settings, key: dict) -> dict | None:
    path = cache_path(settings, key)
    try:
        encoded = (path / 'manifest.json').read_bytes()
        manifest = json.loads(encoded)
        if manifest['identity'] != key or set(manifest['files']) != {'frames.png', 'model.json', 'observation.json'}:
            return None
        for name, expected in manifest['files'].items():
            candidate = (path / name).resolve()
            if not candidate.is_relative_to(path) or file_hash(candidate) != expected:
                return None
        payload = json.loads((path / 'observation.json').read_text(encoding='utf-8'))
        trace = json.loads((path / 'model.json').read_text(encoding='utf-8'))
        _validate_payload(payload, trace, path / 'frames.png', key)
        return {'identity': key, 'path': path, 'payload': payload, 'manifest': manifest,
                'manifest_sha256': hashlib.sha256(encoded).hexdigest()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def save(settings, key: dict, sheet: Path, payload: dict) -> None:
    trace = Path(payload['model_call_path']).resolve()
    if trace.parent != sheet.resolve().parent:
        raise ValueError('Model trace must accompany its contact sheet')
    _validate_payload(payload, json.loads(trace.read_text(encoding='utf-8')), sheet, key)
    path = cache_path(settings, key)
    path.mkdir(parents=True, exist_ok=True)
    # The manifest is replaced last. An interrupted write is a cache miss, not evidence.
    for name, source in (('frames.png', sheet), ('model.json', trace)):
        temporary = path / f'{uuid4().hex}.tmp'
        shutil.copy2(source, temporary)
        os.replace(temporary, path / name)
    temporary = path / f'{uuid4().hex}.tmp'
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    os.replace(temporary, path / 'observation.json')
    manifest = {'identity': key, 'files': {name: file_hash(path / name)
                for name in ('frames.png', 'model.json', 'observation.json')}}
    temporary = path / f'{uuid4().hex}.tmp'
    temporary.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    os.replace(temporary, path / 'manifest.json')


def restore(settings, key: dict, sheet: Path) -> tuple[dict, list[Path]]:
    # Recheck just before use, including every hash. Never infer again on an uncounted cache hit.
    entry = load(settings, key)
    if entry is None:
        raise ValueError('Cached observation changed before copying its evidence')
    sheet.parent.mkdir(parents=True, exist_ok=True)
    trace = sheet.with_name(sheet.stem + '.cached-model.json')
    proof = sheet.with_name(sheet.stem + '.cache-manifest.json')
    original = sheet.with_name(sheet.stem + '.cached-observation.json')
    for source_name, destination in (('frames.png', sheet), ('model.json', trace), ('observation.json', original), ('manifest.json', proof)):
        shutil.copy2(entry['path'] / source_name, destination)
        expected = entry['manifest_sha256'] if source_name == 'manifest.json' else entry['manifest']['files'][source_name]
        if file_hash(destination) != expected:
            raise ValueError('Cached observation evidence changed during copy')
    payload = copy.deepcopy(entry['payload'])
    payload.update(model_call_path=str(trace), source_sha256=key['source_sha256'],
                   cache={'hit': True, 'key': content_hash(key), 'model_revision': key['model_revision']})
    return payload, [sheet, trace, original, proof]
