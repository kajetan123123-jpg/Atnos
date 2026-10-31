import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from atnos.production import story_image_review as review
from test_fiction_production import sample

httpx_original_client = httpx.Client


@pytest.fixture
def harness(tmp_path, monkeypatch):
    image = tmp_path / 'image.png'
    image.write_bytes(b'fixed-image-fixture')
    calls = []
    observation = dict(subjects=['A seated orange fox with a white tail tip'], clothing_and_headwear=['No hat'],
        objects_and_positions=['Trees behind the fox, grass at its feet'], actions_and_poses=['The fox sits with empty paws'],
        setting='A mossy forest path', style_and_mood='Calm watercolor illustration with warm light', visible_defects=[],
        uncertainty='Only one still image is visible.')
    checks = {name: {'status': 'passed', 'evidence': 'The observed feature supports this part of the scene.'}
              for name in review.IllustrationChecks.model_fields}
    checks['required_objects'] = {'status': 'failed', 'evidence': 'There is no lantern in the observed objects.'}
    answers = [observation, checks]

    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={'done': True, 'done_reason': 'stop', 'message': {'content': json.dumps(answers[len(calls) - 1])}})

    monkeypatch.setattr(review, 'model_revision', lambda *args: {'name': 'local-vision', 'digest': 'a' * 64, 'ollama_version': 'test'})
    monkeypatch.setattr(review.httpx, 'Client', lambda **kwargs: httpx_original_client(transport=httpx.MockTransport(respond)))
    settings = SimpleNamespace(ollama_base_url='http://local.invalid')
    arguments = dict(image_path=image, script=sample(), segment_id='s01',
                     config={'audience': 'Children ages four to seven', 'visual_rules': ['Calm watercolor']}, model='local-vision')
    return settings, tmp_path / 'review', arguments, calls, answers


def test_missing_story_prop_blocks_assembly_and_blind_observation_has_no_plot(harness):
    settings, directory, arguments, calls, _ = harness
    result = review.review_illustration(settings, directory, **arguments)
    assert result['status'] == 'failed' and not result['usable_for_assembly']
    assert result['blockers'] == ['required_objects']
    first, second = [call['messages'][0] for call in calls]
    assert 'Milo' not in first['content'] and 'lantern' not in first['content'].casefold()
    assert 'images' in first and 'images' not in second
    assert calls[0]['format']['properties']['subjects']['maxItems'] == 3
    assert 'Milo' in second['content'] and 'empty paws' in second['content']
    assert review.review_illustration(settings, directory, **arguments) == result
    assert len(calls) == 2
    arguments['image_path'].write_bytes(b'different-image')
    with pytest.raises(ValueError, match='changed'):
        review.review_illustration(settings, directory, **arguments)
    assert len(calls) == 2


def test_uncertain_check_is_not_a_pass_and_evidence_cannot_be_replaced(harness):
    settings, directory, arguments, _, answers = harness
    answers[1]['required_objects']['status'] = 'uncertain'
    result = review.review_illustration(settings, directory, **arguments)
    assert not result['usable_for_assembly']
    (directory / 'observation.json').write_text('{}')
    with pytest.raises(ValueError, match='evidence changed'):
        review.review_illustration(settings, directory, **arguments)


def test_every_category_is_required_before_image_can_pass(harness):
    settings, directory, arguments, _, answers = harness
    del answers[1]['required_objects']
    with pytest.raises(ValidationError):
        review.review_illustration(settings, directory, **arguments)
    assert not (directory / 'review.json').exists()


def test_cached_review_cannot_be_applied_to_another_scene(harness):
    settings, directory, arguments, calls, _ = harness
    review.review_illustration(settings, directory, **arguments)
    arguments['script'].segments[0].visual_intent = 'The fox is sleeping beneath a tree.'
    with pytest.raises(ValueError, match='inputs changed'):
        review.review_illustration(settings, directory, **arguments)
    assert len(calls) == 2


def test_interrupted_inference_is_retained_without_automatic_repeat(harness, monkeypatch):
    settings, directory, arguments, _, _ = harness
    attempts = []

    def fail(request):
        attempts.append(request)
        raise httpx.ReadTimeout('Simulated lost response')

    monkeypatch.setattr(review.httpx, 'Client', lambda **kwargs: httpx_original_client(transport=httpx.MockTransport(fail)))
    with pytest.raises(httpx.ReadTimeout):
        review.review_illustration(settings, directory, **arguments)
    with pytest.raises(RuntimeError, match='uncertain outcome'):
        review.review_illustration(settings, directory, **arguments)
    assert len(attempts) == 1 and not (directory / 'review.json').exists()


def test_all_categories_must_pass_for_a_positive_result(harness):
    settings, directory, arguments, _, answers = harness
    answers[1]['required_objects'] = {'status': 'passed', 'evidence': 'The expected lantern is visible in the observed pixels.'}
    result = review.review_illustration(settings, directory, **arguments)
    assert result['status'] == 'passed' and result['usable_for_assembly']
    assert result['blockers'] == [] and result['human_review'] == 'not_performed'
