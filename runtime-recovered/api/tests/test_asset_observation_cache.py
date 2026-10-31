import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from atnos.production import asset_observation as observation, asset_observation_cache as cache
from atnos.production.order_execution import StageExecutor
from atnos.production.order_store import file_hash
from atnos.production.project_profile import ProductionOrder, VisualQAProfile
from test_production_order_store import store


@pytest.fixture
def runtime(store, monkeypatch):
    source = store.settings.data_dir / 'clip.mp4'
    source.write_bytes(b'original fixture clip')
    revision = {'name': 'qwen3-vl:8b-instruct', 'digest': 'a' * 64, 'ollama_version': 'fixture'}
    calls = []
    def sheet(path, target, **kwargs):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
        return target
    def describe(settings, path, config, **kwargs):
        calls.append(path)
        value = {'summary': 'A person checks a household budget on paper.', 'visible_objects': ['paper'],
                 'visible_actions': ['holding paper'], 'readable_text': [], 'limitations': 'Only six sampled frames are available.'}
        evidence = {'source_sha256': file_hash(path)}
        trace = path.with_suffix('.model.json')
        trace.write_text(json.dumps({'status': 'response_received', 'contract': 'AssetObservation', 'model': config.model,
            'audit_input': evidence, 'response': {'message': {'content': json.dumps(value)}, 'done_reason': 'stop'}}))
        return {'status': 'observed', 'model': config.model, 'observation': value, 'audit_input': evidence,
                'model_call_path': str(trace), 'sampling': 'six frames; not exhaustive'}
    monkeypatch.setattr(cache, 'model_revision', lambda *_: copy.deepcopy(revision))
    monkeypatch.setattr(observation.visual_semantic_audit, 'extract_storyboard_contact_sheet', sheet)
    monkeypatch.setattr(observation, 'describe_frames', describe)
    def run():
        state = store.create(ProductionOrder(topic='A weekly household budget'))
        library = {'assets': [{'id': 'clip', 'path': 'clip.mp4', 'sha256': file_hash(source), 'description': 'Provider description'}]}
        with store.lease(state['id']) as token:
            result = asyncio.run(observation.observe_library(StageExecutor(store, state['id'], token), library))
        return store.get(state['id']), result
    return SimpleNamespace(store=store, source=source, revision=revision, calls=calls, run=run)


def test_second_order_reuses_observation_with_owned_evidence_and_no_inference_charge(runtime):
    first, original = runtime.run()
    second, reused = runtime.run()
    assert len(runtime.calls) == 1
    assert first['provider_calls'] == 1 and second['provider_calls'] == 0
    assert reused['assets'][0]['observation']['cache']['hit'] is True
    assert reused['assets'][0]['visual_review'] == 'observed_not_approved_for_scene'
    assert original['assets'][0]['description'] == reused['assets'][0]['description']
    root = Path(second['artifact_dir'])
    files = second['checkpoints'][0]['files']
    assert len(files) == 5
    for row in files:
        assert file_hash(root / row['path']) == row['sha256']
    Path(original['assets'][0]['observation']['model_call_path']).unlink()
    assert Path(reused['assets'][0]['observation']['model_call_path']).is_file()


@pytest.mark.parametrize('change', ['video', 'model', 'server', 'frames', 'trace', 'payload'])
def test_changed_or_corrupt_cache_causes_a_counted_new_inference(runtime, change):
    first, _ = runtime.run()
    config = VisualQAProfile()
    key = cache.identity(file_hash(runtime.source), config, runtime.revision)
    path = cache.cache_path(runtime.store.settings, key)
    if change == 'video':
        runtime.source.write_bytes(b'a different clip')
    elif change == 'model':
        runtime.revision['digest'] = 'b' * 64
    elif change == 'server':
        runtime.revision['ollama_version'] = 'changed'
    elif change == 'frames':
        (path / 'frames.png').write_bytes(b'changed pixels')
    elif change == 'trace':
        (path / 'model.json').unlink()
    else:
        payload = json.loads((path / 'observation.json').read_text())
        payload['observation']['summary'] = 'A different claim not returned by the model.'
        (path / 'observation.json').write_text(json.dumps(payload))
        manifest = json.loads((path / 'manifest.json').read_text())
        manifest['files']['observation.json'] = file_hash(path / 'observation.json')
        (path / 'manifest.json').write_text(json.dumps(manifest))
    second, _ = runtime.run()
    assert len(runtime.calls) == 2
    assert first['provider_calls'] == second['provider_calls'] == 1


def test_cache_disappearing_after_lookup_does_not_trigger_uncounted_inference(runtime):
    runtime.run()
    key = cache.identity(file_hash(runtime.source), VisualQAProfile(), runtime.revision)
    assert cache.load(runtime.store.settings, key)
    (cache.cache_path(runtime.store.settings, key) / 'model.json').unlink()
    with pytest.raises(ValueError, match='changed before copying'):
        cache.restore(runtime.store.settings, key, runtime.source.parent / 'new-job' / 'clip.png')
    assert len(runtime.calls) == 1


def test_cache_mutation_during_copy_is_compared_with_the_verified_manifest(runtime, monkeypatch):
    runtime.run()
    key = cache.identity(file_hash(runtime.source), VisualQAProfile(), runtime.revision)
    original_copy = cache.shutil.copy2
    def mutate(source, destination):
        if Path(source).name == 'frames.png':
            Path(source).write_bytes(b'mutated after successful lookup')
        return original_copy(source, destination)
    monkeypatch.setattr(cache.shutil, 'copy2', mutate)
    with pytest.raises(ValueError, match='changed during copy'):
        cache.restore(runtime.store.settings, key, runtime.source.parent / 'new-job' / 'clip.png')
    assert len(runtime.calls) == 1


@pytest.mark.parametrize('digest', ['a' * 64, '', 'not-a-digest'])
def test_model_name_requires_actual_digest_and_server_version(monkeypatch, digest):
    def response(request):
        if request.url.path == '/api/tags':
            return httpx.Response(200, json={'models': [{'name': 'vision:latest', 'digest': digest}]})
        return httpx.Response(200, json={'version': 'fixture'})
    client = httpx.Client
    monkeypatch.setattr(cache.httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(response), **kwargs))
    result = cache.model_revision(SimpleNamespace(ollama_base_url='http://local.invalid'), 'vision')
    assert bool(result) == (len(digest) == 64)
