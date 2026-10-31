import json

import pytest

from atnos.production import story_images
from atnos.production.order_store import file_hash
from atnos.production.project_profile import content_hash
from test_fiction_production import sample
from test_support import build_test_settings


def test_illustration_expands_location_and_character_ids():
    script = sample()
    script.segments[0].visual_intent = 'milo holds a lantern beside forest.'
    prompt = story_images.illustration_prompt(script, 's01', {'content_kind': 'children_story', 'visual_rules': ['Calm lighting']})
    assert 'small orange fox' in prompt and 'quiet mossy forest path' in prompt
    assert 'beside forest.' not in prompt and 'watercolor storybook' in prompt


def test_uncertain_submission_cannot_create_a_duplicate_image(tmp_path, monkeypatch):
    settings = build_test_settings(tmp_path)
    directory = tmp_path / 'illustration'
    directory.mkdir()
    inputs = {'prompt': 'A fox', 'checkpoint': 'test.safetensors', 'seed': 5, 'width': 640, 'height': 896, 'contract': 1}
    (directory / 'request.json').write_text(json.dumps({'status': 'submitting', 'input_hash': content_hash(inputs)}))

    def no_network(*args, **kwargs):
        raise AssertionError('An uncertain submit must be inspected before more network work')

    monkeypatch.setattr(story_images.httpx, 'Client', no_network)
    with pytest.raises(RuntimeError, match='uncertain'):
        story_images.generate_illustration(settings, directory, prompt='A fox', checkpoint='test.safetensors', seed=5)


def test_cached_illustration_is_bound_to_request_and_file_hash(tmp_path, monkeypatch):
    settings = build_test_settings(tmp_path)
    directory = tmp_path / 'illustration'
    directory.mkdir()
    path = directory / 'illustration.png'
    path.write_bytes(b'checked-test-image')
    inputs = {'prompt': 'A fox', 'checkpoint': 'test.safetensors', 'seed': 5, 'width': 640, 'height': 896, 'contract': 1}
    record = {'status': 'complete', 'input_hash': content_hash(inputs), 'sha256': file_hash(path)}
    (directory / 'request.json').write_text(json.dumps(record))
    result = story_images.generate_illustration(settings, directory, prompt='A fox', checkpoint='test.safetensors', seed=5)
    assert result == record
    with pytest.raises(ValueError, match='different inputs'):
        story_images.generate_illustration(settings, directory, prompt='An owl', checkpoint='test.safetensors', seed=5)
    path.write_bytes(b'changed')
    with pytest.raises(ValueError, match='changed'):
        story_images.generate_illustration(settings, directory, prompt='A fox', checkpoint='test.safetensors', seed=5)
