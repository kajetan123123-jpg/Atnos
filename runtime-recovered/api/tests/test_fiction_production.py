import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from atnos.intelligence.storage import connect
from atnos.production import fiction_script
from atnos.production.fiction_production import produce_script
from atnos.production.order_dispatch import order_status
from atnos.production.order_execution import StageExecutor
from atnos.production.order_store import ProductionBlocked, ProductionOrderStore
from atnos.production.project_profile import ProductionOrder, load_project_profile
from atnos.production.steps.order_script import generation_schema
from atnos.production.workspace_catalog import build_workspace_catalog
from test_support import build_test_settings


@pytest.fixture
def store(tmp_path):
    settings = build_test_settings(tmp_path)
    with connect(settings) as db:
        for project in ('children_stories', 'horror_stories'):
            db.execute("INSERT INTO intelligence_accounts(account_id,niches,region,last_requested_at) VALUES(?,'[]','US','2026-09-13T00:00:00Z')", ('local-' + project,))
        db.commit()
    return ProductionOrderStore(settings)


def sample(kind='children_story'):
    texts = [
        'Milo the little fox found a lantern beside the path as warm evening light filled the quiet forest.',
        'Its ribbon was caught beneath a branch, and Milo wondered who might be looking for their little light.',
        'He gently lifted the branch and carried the lantern along the soft moss, watching the path ahead.',
        'Owl saw him coming and pointed toward a hollow tree, where her empty lantern hook waited beside the door.',
        'Milo handed Owl the lantern, and she thanked him with a smile as its warm light filled her home.',
        'Together they watched the stars appear, then Milo curled up nearby, feeling safe and ready for sleep.',
    ]
    return fiction_script.StoryScript.model_validate(dict(title='The Little Lost Lantern', description='An original fictional bedtime story about helping a friend.',
        is_fiction=True, characters=[dict(id='milo', name='Milo', appearance='A small orange fox with a cream tail tip'), dict(id='owl', name='Owl', appearance='A round brown owl with a small yellow beak')],
        locations=[dict(id='forest', description='A quiet mossy forest path beside a hollow tree')],
        segments=[dict(id=f's0{i+1}', type=beat, text=texts[i], on_screen=f'Lantern scene {i+1}',
                       visual_intent='A small orange fox holds a warm lantern on a forest path.', character_ids=['milo'], location_id='forest')
                  for i, beat in enumerate(fiction_script.BEATS[kind])]))


def make_order(store, project='children_stories'):
    return store.create(ProductionOrder(project=project, topic='The lost lantern', production_scope='script_preview', delivery_mode='local_only'))


def allocated_narrations():
    texts = [
        'Milo found a small lantern beside the forest path and wondered which friend had left it behind.',
        'Its ribbon lay beneath a fallen branch, so Milo carefully lifted the wood and freed the lantern.',
        'He carried the lantern along the mossy path, holding it gently as the evening shadows grew longer.',
        'Owl waited beside her hollow tree and smiled when she saw Milo coming back with her lantern.',
        'Milo handed Owl the lantern, and she thanked her kind friend as warm light filled their clearing.',
        'They watched the stars appear before Milo curled beside the tree, feeling safe and ready for sleep.',
    ]
    return {f's0{i+1}': text.split() for i, text in enumerate(texts)}


def test_finance_contract_and_story_scopes_remain_distinct(store):
    finance = load_project_profile(store.settings, 'wealth_blueprint')['production']
    assert finance['minimum_practical_share'] >= 1 / 3
    for project in ('children_stories', 'horror_stories'):
        with pytest.raises(ValidationError):
            ProductionOrder(project=project, topic='A fictional story')
        profile = load_project_profile(store.settings, project)['production']
        assert 'minimum_practical_share' not in profile and 'example_currency' not in profile
        assert profile['publish'] is False
    catalog = build_workspace_catalog(store.settings)
    stories = [c for c in catalog['channels'] if c['key'] != 'wealth_blueprint']
    assert all(c['can_prepare_story'] and not c['can_order'] for c in stories)


def test_story_continuity_and_genre_validation(store):
    script = sample()
    config = load_project_profile(store.settings, 'children_stories')['production']
    config['word_budget'] = [90, 125]
    assert not fiction_script.script_issues(script, config)
    wrong = script.model_copy(deep=True)
    wrong.segments[2].character_ids = ['unknown']
    wrong.segments[3].location_id = 'nowhere'
    wrong.segments[-1].type = 'reveal'
    assert {'character_reference', 'location_reference', 'story_structure'} <= {i['code'] for i in fiction_script.script_issues(wrong, config)}
    horror = load_project_profile(store.settings, 'horror_stories')['production']
    wrong.description = 'A true story that actually happened in this forest.'
    assert 'false_factual_framing' in {i['code'] for i in fiction_script.script_issues(wrong, horror)}


def test_repair_cannot_change_unflagged_scenes_or_metadata(store):
    script = sample()
    config = load_project_profile(store.settings, 'children_stories')['production']
    issue = dict(code='style', segment_ids=['s01'], fields=['text'], detail='Shorten the first sentence')
    replacement = script.segments[1].model_copy(update={'text': 'Unrelated changed scene.'})
    with pytest.raises(ValueError, match='unflagged'):
        fiction_script.apply_repair(script, fiction_script.StoryRepair(replacements=[replacement]), [issue], config)
    with pytest.raises(ValueError, match='metadata'):
        fiction_script.apply_repair(script, fiction_script.StoryRepair(replacements=[], title='An unrelated new title'), [issue], config)
    replacement = script.segments[0].model_copy(update={'text': 'A shorter sentence.', 'visual_intent': 'A completely different image of a city at night.'})
    with pytest.raises(ValueError, match='unflagged field'):
        fiction_script.apply_repair(script, fiction_script.StoryRepair(replacements=[replacement]), [issue], config)


def test_repair_decoding_cannot_generate_unrequested_metadata():
    schema = fiction_script.story_patch_schema(sample(), [dict(fields=['characters', 'visual_intent'], segment_ids=['s01'], code='invisible_character')])
    decoded = generation_schema(schema)
    assert 'locations' not in decoded['properties'] and 'title' not in decoded['properties']
    assert 'text' not in decoded['$defs']['Patchs01']['properties']
    with pytest.raises(ValidationError):
        schema.model_validate({'scenes': {}, 'locations': [{'id': 'new', 'description': 'An unrelated new location'}]})


def test_partial_patch_can_only_touch_named_fields_of_named_scenes(store):
    original = sample()
    issues = [dict(code='wording', fields=['text'], segment_ids=['s01'], detail='Clarify the opening')]
    budget = fiction_script.repair_word_budget(original, {'s01'}, [90, 115])
    schema = fiction_script.story_patch_schema(original, issues, budget)
    text = 'Milo found a lantern beside the path as evening light filled the quiet forest.'
    patch = schema.model_validate({'scenes': {'s01': {'text': text.split()}}})
    result = fiction_script.apply_repair(original, fiction_script.expand_story_patch(original, patch), issues,
                                          load_project_profile(store.settings, 'children_stories')['production'])
    assert result.segments[0].text == text
    assert result.segments[0].visual_intent == original.segments[0].visual_intent
    assert result.segments[1:] == original.segments[1:]
    assert result.characters == original.characters and result.locations == original.locations
    with pytest.raises(ValidationError):
        schema.model_validate({'scenes': {'s01': {'text': text.split(), 'visual_intent': 'An unrelated new image'}}})
    with pytest.raises(ValidationError):
        schema.model_validate({'scenes': {'s01': {'text': text.split()}, 's02': {'text': text.split()}}})


def test_editorial_patch_word_limits_include_only_editable_narration(store, monkeypatch):
    original = sample()
    for segment in original.segments:
        segment.text = ' '.join(allocated_narrations()[segment.id])
    context = store.context(make_order(store)['id'])
    issues = [dict(code='visual', fields=['visual_intent'], segment_ids=['s01'], detail='Clarify the lantern'),
              dict(code='ending', fields=['text'], segment_ids=['s06'], detail='Clarify the reassuring ending')]
    ending = 'Milo curled up beside his friend and fell asleep, safe beneath the warm light of her lantern.'

    async def answer(settings, context, prompt, schema, **kwargs):
        decoded = kwargs['decoding_schema']['$defs']
        assert 'text' not in decoded['Patchs01']['properties']
        limits = decoded['Patchs06']['properties']['text']
        # Five untouched texts already use 85 words; a visual edit cannot redistribute them.
        assert limits['minItems'] == 5 and limits['maxItems'] == 30
        patch = {'scenes': {'s01': {'visual_intent': 'Milo holds the lantern beside a quiet forest path.',
                                    'character_ids': ['milo'], 'location_id': 'forest'},
                            's06': {'text': ending.split()}}}
        from copy import deepcopy
        for invalid in (['word'] * 35, ['two words'] * 18, ending):
            bad = deepcopy(patch)
            bad['scenes']['s06']['text'] = invalid
            with pytest.raises(ValidationError):
                schema.model_validate(bad)
        return patch

    monkeypatch.setattr(fiction_script, 'ask_model', answer)
    fixed = asyncio.run(fiction_script.repair(store.settings, context, original, issues))
    assert [s.text for s in fixed.segments[:5]] == [s.text for s in original.segments[:5]]
    assert fixed.segments[-1].text == ending
    assert fixed.title == original.title and fixed.characters == original.characters and fixed.locations == original.locations
    assert not fiction_script.script_issues(fixed, context.artifacts['project_profile']['production'])


def test_editorial_narration_patch_requires_an_explicit_budget():
    with pytest.raises(ValueError, match='remaining word budget'):
        fiction_script.story_patch_schema(sample(), [dict(code='ending', fields=['text'], segment_ids=['s06'])])


def test_unpunctuated_real_model_ending_cannot_pass_even_with_a_positive_reviewer(store, monkeypatch):
    script = sample('fiction_horror')
    script.segments[-1].text = 'Inside the phone rang again This time a shadow moved behind the door Its shape was familiar Elena held the key but it was not hers'
    context = store.context(make_order(store, 'horror_stories')['id'])

    async def answer(*args, **kwargs):
        return {'passed': True, 'issues': []}

    monkeypatch.setattr(fiction_script, 'ask_model', answer)
    result = asyncio.run(fiction_script.review(store.settings, context, script))
    assert result['status'] == 'failed'
    assert any(i['code'] == 'narration_punctuation' and i['segment_ids'] == ['s06'] for i in result['issues'])
    config = context.artifacts['project_profile']['production']
    for ending in ('The voice said, "Good night."', "The voice whispered, 'Good night.'", 'Was anyone there?', 'Then the light went out!'):
        script.segments[-1].text = ending
        assert not any(i['code'] == 'narration_punctuation' for i in fiction_script.script_issues(script, config))


def test_durable_story_preview_reuses_models_and_never_becomes_a_ready_video(store, monkeypatch):
    job = make_order(store)
    script = sample()
    # Keep a real story inside the profile budget, without rewriting the test's outcome assertions.
    script.segments[0].text = 'Milo found a lantern beside the path as evening light filled the quiet forest.'
    calls = []

    async def answer(settings, context, prompt, schema, **kwargs):
        calls.append(schema.__name__)
        return script.model_dump(mode='json') if schema is fiction_script.StoryScript else {'passed': True, 'issues': []}

    monkeypatch.setattr(fiction_script, 'ask_model', answer)
    with store.lease(job['id']) as token:
        executor = StageExecutor(store, job['id'], token)
        first = asyncio.run(produce_script(executor))
        assert first['qa']['status'] == 'passed'
        assert asyncio.run(produce_script(StageExecutor(store, job['id'], token))) == first
        assert len(calls) == 2
        done = store.finish_script_preview(job['id'], token)
        assert done['status'] == 'script_ready' and done['provider_calls'] == 2
        assert done['reserved_cost_units'] == 0 and not done['can_resume']
        public = order_status(done)
        assert not public['ready_for_review'] and not public['ready_for_private_upload'] and public['package'] is None
        with pytest.raises(ProductionBlocked):
            store.finish(job['id'], token, executor.context.artifact_dir / 'script.json')
    assert not store.get(job['id'])['worker_live']
    (executor.context.artifact_dir / 'script.json').write_text('{}')
    with store.lease(job['id']) as token:
        with pytest.raises(ProductionBlocked, match='stale'):
            store.finish_script_preview(job['id'], token)


def test_false_model_pass_cannot_override_deterministic_story_errors(store, monkeypatch):
    script = sample()
    script.segments[0].character_ids = ['unknown']

    async def passing(*args, **kwargs):
        return {'passed': True, 'issues': []}

    monkeypatch.setattr(fiction_script, 'ask_model', passing)
    context = store.context(make_order(store)['id'])
    verdict = asyncio.run(fiction_script.review(store.settings, context, script))
    assert verdict['status'] == 'failed'
    assert any(i['code'] == 'character_reference' for i in verdict['issues'])


def test_decoding_contract_rejects_positive_verdict_with_an_issue():
    schema = generation_schema(fiction_script.StoryReviewResponse)
    assert schema['$defs']['StoryPass']['properties']['issues']['maxItems'] == 0
    with pytest.raises(ValidationError):
        fiction_script.StoryReviewResponse.model_validate({'passed': True, 'issues': [
            {'code': 'none', 'segment_ids': ['s01'], 'fields': ['text'], 'detail': 'No issues found'}]})


def test_worker_routes_story_without_paid_media_preflight_and_api_exposes_checked_script(store, monkeypatch):
    from atnos.production import order_execution
    from atnos.api.routes.production import get_production_order_review_package
    from fastapi import HTTPException
    story = sample()
    story.segments[0].text = 'Milo found a lantern beside the path as evening light filled the quiet forest.'

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url):
            assert url.endswith('/api/tags')
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'models': [{'name': 'qwen3:14b'}]})

    async def answer(settings, context, prompt, schema, **kwargs):
        return story.model_dump(mode='json') if schema is fiction_script.StoryScript else {'passed': True, 'issues': []}

    async def disallowed(*args, **kwargs):
        raise AssertionError('A script preview must not require paid media services')

    monkeypatch.setattr(order_execution.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(order_execution, 'preflight_order', disallowed)
    monkeypatch.setattr(fiction_script, 'ask_model', answer)
    job = make_order(store)
    asyncio.run(order_execution.run_order(store.settings, job['id']))
    assert store.get(job['id'])['status'] == 'script_ready'
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=store.settings)))
    review = get_production_order_review_package(request, job['id'])
    assert review['script_preview']['word_count'] > 90
    assert not review['video_ready'] and not review['ready_for_review']
    monkeypatch.setattr(fiction_script, 'script_issues', lambda *args: [dict(
        code='invisible_character', segment_ids=['s01'], fields=['characters'], detail='English model-only explanation')])
    updated = get_production_order_review_package(request, job['id'])
    assert 'W obsadzie obrazu umieszczono sam głos.' in updated['blocking_failures'][0]
    assert 'English model-only explanation' not in updated['blocking_failures'][0]
    assert store.get(job['id'])['status'] == 'script_ready'
    (store.context(job['id']).artifact_dir / 'script.json').write_text('{}')
    with pytest.raises(HTTPException) as caught:
        get_production_order_review_package(request, job['id'])
    assert caught.value.status_code == 409


def test_length_only_repair_preserves_story_entities_and_visuals(store, monkeypatch):
    original = sample()
    shortened = original.model_copy(deep=True)
    for segment in shortened.segments:
        segment.text = 'A short sentence.'
    config = load_project_profile(store.settings, 'children_stories')['production']
    original.segments[0].text = 'Milo found a lantern beside the path as evening light filled the quiet forest.'

    async def answer(settings, context, prompt, schema, **kwargs):
        assert schema.__name__ == 'AllocatedStoryNarration'
        assert kwargs['decoding_schema']['properties']['s01']['minItems'] == 15
        return allocated_narrations()

    monkeypatch.setattr(fiction_script, 'ask_model', answer)
    context = store.context(make_order(store)['id'])
    issues = [i for i in fiction_script.script_issues(shortened, config) if i['code'] == 'word_budget']
    fixed = asyncio.run(fiction_script.repair(store.settings, context, shortened, issues))
    assert fixed.characters == shortened.characters and fixed.locations == shortened.locations
    assert [s.visual_intent for s in fixed.segments] == [s.visual_intent for s in shortened.segments]
    assert not fiction_script.script_issues(fixed, config)
    assert all(len(s.text.split()) == 17 for s in fixed.segments)


def test_word_allocation_rejects_short_array_and_whitespace_inside_a_word():
    schema = fiction_script.allocated_narration_schema({'target_words_by_segment': {'s01': 4}})
    assert schema.model_validate({'s01': ['The', 'door', 'was', 'closed.']}).s01[-1] == 'closed.'
    with pytest.raises(ValidationError):
        schema.model_validate({'s01': ['The', 'door', 'closed.']})
    with pytest.raises(ValidationError):
        schema.model_validate({'s01': ['The locked', 'door', 'was', 'closed.']})


def test_allocated_scene_ranges_fit_the_total_budget_without_forcing_one_exact_length():
    schema = fiction_script.allocated_narration_schema({'target_words_by_segment': {f's0{i}': 17 for i in range(1, 7)},
                                                       'replacement_total_range': [90, 115]}).model_json_schema()
    limits = schema['properties'].values()
    assert sum(p['minItems'] for p in limits) == 90
    assert sum(p['maxItems'] for p in limits) == 115
    assert all(p['minItems'] < 17 < p['maxItems'] for p in limits)


def test_address_label_is_spoken_without_changing_its_identity():
    story = sample('fiction_horror')
    story.segments[0].text = 'Elena stands outside apartment 4B with a key.'
    assert fiction_script.normalized(story).segments[0].text == 'Elena stands outside apartment four B with a key.'


def test_horror_request_cannot_turn_a_locked_opening_into_an_open_door(store):
    story = sample('fiction_horror')
    config = load_project_profile(store.settings, 'horror_stories')['production']
    request = {'topic': 'A call comes from my locked apartment while I stand outside holding the key'}
    story.segments[0].text = 'Elena holds the key outside the locked apartment door.'
    story.segments[0].visual_intent = 'Elena holds a key beside the closed apartment door.'
    assert not any(i['code'] == 'request_locked_setup' for i in fiction_script.script_issues(story, config, request))
    story.segments[0].visual_intent = 'The door is slightly ajar, with a broken lock.'
    assert any(i['code'] == 'request_locked_setup' for i in fiction_script.script_issues(story, config, request))


@pytest.mark.parametrize('opening', [
    'The apartment door would not open. Elena held its key beside her open notebook.',
    "The apartment door wouldn't open. Elena held its key.",
    'Elena waited at the locked apartment door, holding an open notebook and the key.',
])
def test_locked_setup_accepts_negated_open_and_unrelated_objects(store, opening):
    story = sample('fiction_horror')
    story.segments[0].text = opening
    story.segments[0].visual_intent = 'Elena stands beside the closed apartment door holding a key.'
    config = load_project_profile(store.settings, 'horror_stories')['production']
    issues = fiction_script.script_issues(story, config, {'topic': 'A call from my locked apartment'})
    assert not any(i['code'] == 'request_locked_setup' for i in issues)


def test_invisible_voice_cannot_be_a_visible_illustration_character(store):
    story = sample('fiction_horror')
    story.characters[0].appearance = 'not visible, only heard, male, calm and familiar'
    config = load_project_profile(store.settings, 'horror_stories')['production']
    issues = fiction_script.script_issues(story, config)
    found = next(i for i in issues if i['code'] == 'invisible_character')
    assert found['segment_ids'] == [s.id for s in story.segments]
    assert found['fields'] == ['characters', 'visual_intent']


def test_explicitly_invisible_cast_is_corrected_without_asking_model_to_rewrite_plot(store, monkeypatch):
    story = sample('fiction_horror')
    story.characters[1].appearance = 'not visible, only heard, a calm voice on the phone'
    story.segments[2].character_ids.append('owl')
    context = store.context(make_order(store, 'horror_stories')['id'])
    config = context.artifacts['project_profile']['production']
    issues = [i for i in fiction_script.script_issues(story, config) if i['code'] == 'invisible_character']

    async def forbidden(*args, **kwargs):
        raise AssertionError('Removing explicitly invisible metadata does not require generative rewriting')

    monkeypatch.setattr(fiction_script, 'ask_model', forbidden)
    fixed = asyncio.run(fiction_script.repair(store.settings, context, story, issues))
    assert [c.id for c in fixed.characters] == ['milo']
    assert fixed.segments[2].character_ids == ['milo']
    assert [s.text for s in fixed.segments] == [s.text for s in story.segments]
    assert fixed.locations == story.locations
    assert len(list((context.artifact_dir / 'script').glob('cast-correction-*.json'))) == 1
    assert fiction_script.repair_requires_model(story, issues) is False


def test_cast_cleanup_and_length_repair_keep_visuals_and_locations(store, monkeypatch):
    story = sample('fiction_horror')
    story.characters[1].appearance = 'not visible, only heard over a phone'
    context = store.context(make_order(store, 'horror_stories')['id'])
    config = context.artifacts['project_profile']['production']
    expected = story.model_copy(deep=True)
    expected.segments[0].text = 'Milo found a lantern beside the path as evening light filled the quiet forest.'

    async def answer(settings, context, prompt, schema, **kwargs):
        assert schema.__name__ == 'AllocatedStoryNarration'
        return allocated_narrations()

    monkeypatch.setattr(fiction_script, 'ask_model', answer)
    issues = fiction_script.script_issues(story, config)
    fixed = asyncio.run(fiction_script.repair(store.settings, context, story, issues))
    assert [c.id for c in fixed.characters] == ['milo']
    assert [s.visual_intent for s in fixed.segments] == [s.visual_intent for s in story.segments]
    assert fixed.locations == story.locations and not fiction_script.script_issues(fixed, config)


def test_invisible_cast_referenced_in_image_needs_a_real_visual_repair(store, monkeypatch):
    story = sample('fiction_horror')
    story.characters[1].appearance = 'not visible, only heard over a phone'
    story.segments[0].visual_intent = 'Owl speaks from the shadows beside the fox.'
    context = store.context(make_order(store, 'horror_stories')['id'])
    issues = fiction_script.script_issues(story, context.artifacts['project_profile']['production'])

    async def answer(settings, context, prompt, schema, **kwargs):
        assert schema.__name__ == 'StoryPatchContract'
        raise RuntimeError('A visual repair is required here')

    monkeypatch.setattr(fiction_script, 'ask_model', answer)
    with pytest.raises(RuntimeError, match='visual repair'):
        asyncio.run(fiction_script.repair(store.settings, context, story, issues))
    assert not list((context.artifact_dir / 'script').glob('cast-correction-*.json'))
    assert fiction_script.repair_requires_model(story, issues) is True


def test_automatic_cast_repair_has_no_fake_model_call_and_retains_evidence(store, monkeypatch):
    job = make_order(store)
    script = sample()
    script.segments[0].text = 'Milo found a lantern beside the path as evening light filled the quiet forest.'
    script.characters[1].appearance = 'not visible, only heard in a gentle voice'
    calls = []

    async def answer(settings, context, prompt, schema, **kwargs):
        calls.append(schema.__name__)
        path = context.artifact_dir / 'model_calls' / f'call-{len(calls)}.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"complete":true}')
        return script.model_dump(mode='json') if schema is fiction_script.StoryScript else {'passed': True, 'issues': []}

    monkeypatch.setattr(fiction_script, 'ask_model', answer)
    with store.lease(job['id']) as token:
        executor = StageExecutor(store, job['id'], token)
        result = asyncio.run(produce_script(executor))
        assert result['qa']['status'] == 'passed'
        done = store.finish_script_preview(job['id'], token)
        assert done['provider_calls'] == len(calls) == 3
    final = next(row for row in done['checkpoints'] if row['stage'] == 'script')
    assert 'cast-correction-' in final['files_json'] and 'call-3.json' in final['files_json']
    (executor.context.artifact_dir / 'model_calls' / 'call-3.json').write_text('{"complete":false}')
    assert not store._valid_files(executor.context.artifact_dir, final['files_json'])


def test_cast_cleanup_also_precedes_a_separate_editorial_repair(store, monkeypatch):
    script = sample()
    script.characters[1].appearance = 'not visible, only heard through the phone'
    context = store.context(make_order(store)['id'])
    issues = [i for i in fiction_script.script_issues(script, context.artifacts['project_profile']['production'])
              if i['code'] == 'invisible_character']
    issues.append(dict(code='editorial', fields=['text'], segment_ids=['s06'], detail='Clarify the ending'))

    async def answer(settings, context, prompt, schema, **kwargs):
        assert 'characters' not in schema.model_json_schema()['properties']
        assert 'not visible, only heard through the phone' not in prompt
        raise RuntimeError('The separate editorial correction now receives a valid visible cast')

    monkeypatch.setattr(fiction_script, 'ask_model', answer)
    with pytest.raises(RuntimeError, match='valid visible cast'):
        asyncio.run(fiction_script.repair(store.settings, context, script, issues))
    assert fiction_script.repair_requires_model(script, issues)
