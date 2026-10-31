"""Editorial continuation requires evidence; validation keeps genuine defects."""
import asyncio
import json

import pytest

from atnos.intelligence.storage import connect, json_dumps
from atnos.production.order_execution import StageExecutor, produce_script
from atnos.production.order_store import OrderConflict, ProductionBlocked
from atnos.production.project_profile import content_hash
from atnos.production.scoped_script_revision import load_feedback, register_feedback, feedback_issues, authorize_rejected_continuation
from atnos.production.scoped_script_revision import revision_source, authorize_feedback_rounds, _round_limit
from types import SimpleNamespace
from atnos.production.steps import order_script
from atnos.production.steps.common import write_json
from test_scoped_script_revision import store, original_order, feedback, four_thoughts, script
from test_production_order_script import execution


def test_four_scene_rewrite_does_not_request_five_scene_functions(four_thoughts, feedback):
    raw = feedback.model_dump(mode='json')
    raw['target_segments'] = [4, 4]
    context = SimpleNamespace(artifacts={'project_profile': {}, 'production_order': {
        'topic': 'A conditional trial renewal', 'direction': 'Check terms, set a reminder, and choose deliberately.'}})
    source = revision_source(context, four_thoughts, raw, [{'code': 'word_budget'}])
    payload = json.loads(source.split('FRESH FULL REWRITE FROM THE ORIGINAL BRIEF: ', 1)[1])
    assert len(payload['scene_functions']) == 4
    assert 'CTA' in payload['scene_functions'][-1]
    assert 'terms' in payload['direction']


@pytest.mark.parametrize('count,budget', [(4, [66, 81]), (6, [90, 110]), (8, [120, 150])])
def test_fixed_scene_length_guidance_allocates_real_middle_content(four_thoughts, feedback, count, budget):
    from atnos.production.scoped_script_revision import scoped_word_guidance
    context = SimpleNamespace(artifacts={'project_profile': {'production': {'word_budget': budget}}})
    raw = feedback.model_dump(mode='json')
    raw['target_segments'] = [count, count]
    hint = scoped_word_guidance(context, four_thoughts, raw, [{'code': 'word_budget'}])
    allocated = hint['suggested_words_by_scene']
    assert len(allocated) == count
    assert sum(allocated) == hint['target_total'] == sum(budget) // 2
    assert all(0 < words <= 32 for words in allocated)
    assert allocated[0] == 12 and allocated[-1] == 8
    assert context.artifacts['project_profile']['production']['word_budget'] == budget


def test_length_hints_do_not_invent_feasible_budget_or_override_variable_scenes(four_thoughts, feedback):
    from atnos.production.scoped_script_revision import scoped_word_guidance
    context = SimpleNamespace(artifacts={'project_profile': {'production': {'word_budget': [200, 220]}}})
    raw = feedback.model_dump(mode='json')
    raw['target_segments'] = [4, 4]
    assert scoped_word_guidance(context, four_thoughts, raw, [{'code': 'word_budget'}]) == {}
    raw['target_segments'] = [4, 6]
    assert scoped_word_guidance(context, four_thoughts, raw, [{'code': 'word_budget'}]) == {}
    raw['target_segments'] = [6, 6]
    assert scoped_word_guidance(context, four_thoughts, raw, []) == {}


@pytest.mark.parametrize('question', ['Why?', 'Where?', 'When?', 'How?', 'Who?', 'What?', 'Why?"'])
def test_standalone_question_is_not_a_truncated_clause(execution, script, question):
    script.segments[0].text = 'The extra income arrives, but savings stay unchanged. ' + question
    issues = order_script.script_issues(script, execution[2].artifacts['project_profile'])
    assert not any(i['code'] == 'incomplete_sentence' and 's01' in i['segment_ids'] for i in issues)


@pytest.mark.parametrize('text', ['You can choose where.', 'You can choose where?', 'Money gives you control of.',
                                 'The choice matters when.', 'Decide what?', 'Why.'])
def test_question_exception_does_not_hide_a_dangling_clause(text):
    assert order_script._is_dangling_narration(text)


def test_long_segment_and_allocation_guarantee_remain_real_defects(execution, script):
    script.segments[1].text = ' '.join(['word'] * 32 + ['word.'])
    script.segments[2].text = "Earmarking ensures it won't be used for unexpected costs."
    codes = {i['code'] for i in order_script.script_issues(script, execution[2].artifacts['project_profile'])}
    assert {'long_segment', 'unsupported_allocation_guarantee'} <= codes
    script.segments[2].text = 'A separate account can help keep it apart from daily spending.'
    codes = {i['code'] for i in order_script.script_issues(script, execution[2].artifacts['project_profile'])}
    assert 'unsupported_allocation_guarantee' not in codes


@pytest.mark.parametrize('text', ['Allocating money first ensures it’s not used for new expenses.',
    'Choose an affordable portion to set aside. This ensures it’s available for a future need you define.',
    'Choose an affordable portion to set aside. This ensures it’s ready for a future need you define.'])
def test_actual_rejected_b_protection_claim_is_checked_in_scoped_review(four_thoughts, feedback, text):
    raw = feedback.model_dump(mode='json')
    raw['issues'].append({'code': 'unsupported_protection', 'segment_ids': ['s03'],
                         'detail': 'An allocation is not a guarantee against future costs or withdrawals.'})
    four_thoughts.segments[2].text = text
    assert 'unsupported_protection' in {issue['code'] for issue in feedback_issues(
        four_thoughts, raw)}
    four_thoughts.segments[2].text = 'Set an affordable amount aside for the goal before committing to new spending.'
    assert 'unsupported_protection' not in {issue['code'] for issue in feedback_issues(
        four_thoughts, raw)}
    four_thoughts.segments[2].text = 'Ensure your calculator is ready before comparing the two available payment options.'
    assert 'unsupported_protection' not in {issue['code'] for issue in feedback_issues(four_thoughts, raw)}


def test_repeated_short_draft_writer_uses_brief_and_functions_not_rejected_sentence_template(four_thoughts, feedback):
    context = SimpleNamespace(artifacts={'production_order': {'topic': 'A raise and recurring spending',
        'direction': 'Show the causal choice with physical illustrations', 'optional_notes': 'Natural English'}})
    raw = feedback.model_dump(mode='json')
    source = revision_source(context, four_thoughts, raw, [{'code': 'word_budget'}])
    assert 'FRESH FULL REWRITE' in source and 'A raise and recurring spending' in source
    assert 'scene_functions' in source and 'physical illustrations' in source
    assert four_thoughts.segments[0].text not in source
    assert 'PREVIOUS SCRIPT:' in revision_source(context, four_thoughts, raw, [])


def test_round_extension_requires_explicit_authorization_preserves_all_existing_budget_evidence(store, original_order, feedback):
    job, _ = original_order
    register_feedback(store.settings, job, feedback, approved=True)
    before = store.get(job)
    assert _round_limit(store.settings, job) == 3
    with pytest.raises(ValueError, match='Explicit'):
        authorize_feedback_rounds(store.settings, job, 4, reason='Controlled explicit bounded extension')
    result = authorize_feedback_rounds(store.settings, job, 4, reason='Controlled explicit bounded extension', approved=True)
    assert result['approved_maximum'] == 4 and result['attempts_reset'] is False
    after = store.get(job)
    assert after['provider_calls'] == before['provider_calls']
    assert after['reserved_cost_units'] == before['reserved_cost_units']
    assert after['profile_hash'] == before['profile_hash'] and after['request'] == before['request']
    assert [(r['stage'], r['attempts']) for r in after['checkpoints']] == [(r['stage'], r['attempts']) for r in before['checkpoints']]
    assert _round_limit(store.settings, job) == 4
    with pytest.raises(ValueError, match='only extend'):
        authorize_feedback_rounds(store.settings, job, 4, reason='Controlled duplicate request', approved=True)
    receipt = store.context(job).artifact_dir / 'script_feedback/round_authorizations/maximum-4.json'
    receipt.write_text('{}')
    with pytest.raises(ProductionBlocked, match='receipt'):
        _round_limit(store.settings, job)


def test_live_worker_never_receives_an_extra_round_allowance(store, original_order, feedback):
    job, _ = original_order
    register_feedback(store.settings, job, feedback, approved=True)
    with store.lease(job):
        with pytest.raises(OrderConflict, match='active'):
            authorize_feedback_rounds(store.settings, job, 4, reason='Controlled explicit bounded extension', approved=True)
    assert _round_limit(store.settings, job) == 3


@pytest.mark.parametrize('text', ['Before accepting a new income, decide where it goes.',
                                 'Review your budget before any new income arrives.'])
def test_income_wording_does_not_evade_raise_chronology_guard(four_thoughts, feedback, text):
    four_thoughts.segments[2].text = text
    assert 'temporal_logic' in {i['code'] for i in feedback_issues(four_thoughts, feedback.model_dump(mode='json'))}


def test_illustrated_trial_guard_is_scoped_to_actual_operator_defect(four_thoughts, feedback):
    four_thoughts.segments[0].visual_intent = 'Footage of a phone screen showing a deposit notification'
    assert 'unsupported_visual_intent' not in {i['code'] for i in feedback_issues(four_thoughts, feedback.model_dump(mode='json'))}
    raw = feedback.model_dump(mode='json')
    raw['issues'].append({'code': 'unsupported_visual_intent', 'segment_ids': ['s01'],
                         'detail': 'This trial uses physical editorial illustrations instead of footage.'})
    assert 'unsupported_visual_intent' in {i['code'] for i in feedback_issues(four_thoughts, raw)}
    four_thoughts.segments[0].visual_intent = 'An illustrated pay envelope with blank money slips on cream paper'
    assert 'unsupported_visual_intent' not in {i['code'] for i in feedback_issues(four_thoughts, raw)}


def _followup(feedback):
    return feedback.model_copy(update={'reason': 'Correct the remaining actual narration and visual intent defects.'})


def test_scoped_writer_decoder_requires_nonempty_metadata_before_model_call(store, original_order, feedback, four_thoughts, monkeypatch):
    job, _ = original_order
    register_feedback(store.settings, job, feedback, approved=True)
    inspected = []
    async def writer(settings, context, prompt, response_model, **kwargs):
        schema = kwargs['decoding_schema']
        for field in ('title', 'description'):
            assert schema['properties'][field].get('minLength', 0) >= 1
        inspected.append(True)
        return four_thoughts.model_dump(mode='json')
    async def reviewer(*args, **kwargs):
        return {'status': 'passed', 'issues': [], 'semantic_review': 'controlled_contract_test'}
    monkeypatch.setattr(order_script, 'ask_model', writer)
    monkeypatch.setattr(order_script, 'review', reviewer)
    with store.lease(job) as token:
        asyncio.run(produce_script(StageExecutor(store, job, token)))
    assert inspected == [True]


def test_scoped_literal_constraint_blocks_model_pass_before_canonical(store, original_order, feedback, four_thoughts, monkeypatch):
    from atnos.production.scoped_script_revision import ScriptFeedback
    job, _ = original_order
    payload = feedback.model_dump(mode='json')
    payload['max_drafts'] = 1
    payload['issues'] = [dict(code='brief_fidelity', segment_ids=['s01'], field='text',
        detail='This particular operator wording is expressly disallowed.',
        forbidden_phrases=['hidden deadline'])]
    register_feedback(store.settings, job, ScriptFeedback.model_validate(payload), approved=True)
    canonical = (store.context(job).artifact_dir / 'script.json').read_bytes()
    four_thoughts.segments[0].text = 'A hidden deadline can change this decision.'
    async def writer(*args, **kwargs):
        return four_thoughts.model_dump(mode='json')
    async def reviewer(*args, **kwargs):
        return {'status': 'passed', 'issues': [], 'semantic_review': 'controlled_false_pass'}
    monkeypatch.setattr(order_script, 'ask_model', writer)
    monkeypatch.setattr(order_script, 'review', reviewer)
    with store.lease(job) as token, pytest.raises(ProductionBlocked):
        asyncio.run(produce_script(StageExecutor(store, job, token)))
    assert (store.context(job).artifact_dir / 'script.json').read_bytes() == canonical
    assert not any(row['stage'].endswith('/accepted') for row in store.get(job)['checkpoints'])


def _accept_first(store, job, feedback, four_thoughts, monkeypatch):
    register_feedback(store.settings, job, feedback, approved=True)
    async def writer(*a, **k):
        return four_thoughts.model_dump(mode='json')
    async def reviewer(*a):
        return {'status': 'passed', 'issues': [], 'semantic_review': 'controlled_independent_reviewer'}
    monkeypatch.setattr(order_script, 'ask_model', writer)
    monkeypatch.setattr(order_script, 'review', reviewer)
    with store.lease(job) as token:
        result = asyncio.run(produce_script(StageExecutor(store, job, token)))
        store.block(job, token, 'scripting', category='operator_review', message='Controlled pre-media review')
    return result


def test_changed_review_contract_cannot_reuse_old_accepted_scoped_pass(store, original_order, feedback, four_thoughts, monkeypatch):
    job, _ = original_order
    result = _accept_first(store, job, feedback, four_thoughts, monkeypatch)
    assert result['qa']['review_contract_version'] == order_script.SCRIPT_VALIDATOR_VERSION
    before = store.get(job)
    path = store.context(job).artifact_dir / 'script.json'
    canonical = path.read_bytes()
    monkeypatch.setattr(order_script, 'SCRIPT_VALIDATOR_VERSION', order_script.SCRIPT_VALIDATOR_VERSION + 1)
    async def forbidden(*args, **kwargs):
        raise AssertionError('No hidden model call to replay exhausted or accepted slots')
    monkeypatch.setattr(order_script, 'ask_model', forbidden)
    monkeypatch.setattr(order_script, 'review', forbidden)
    with store.lease(job) as token, pytest.raises(ProductionBlocked, match='current editorial review contract'):
        asyncio.run(produce_script(StageExecutor(store, job, token)))
    after = store.get(job)
    assert after['provider_calls'] == before['provider_calls']
    assert after['checkpoints'] == before['checkpoints']
    assert path.read_bytes() == canonical
    assert 'script_review_contract_changed' in after['error']


def _rejected_final(store, job, feedback, four_thoughts, *, index=None):
    registered = register_feedback(store.settings, job, feedback, approved=True)
    prefix = 'script_scoped/' + registered['feedback_hash'][:12]
    raw = four_thoughts.model_dump(mode='json')
    raw['segments'][2]['text'] = ' '.join(['word'] * 32 + ['word.'])
    review = {'status': 'failed', 'issues': [{'code': 'long_segment', 'segment_ids': ['s03'],
              'detail': 'This narration exceeds the segment word limit.'}],
              'feedback_hash': registered['feedback_hash'], 'script_hash': content_hash(raw)}
    last = feedback.max_drafts - 1 if index is None else index
    with store.lease(job) as token:
        for stage, payload in [(prefix + f'/draft/{last}', raw), (prefix + f'/review/{last}', review)]:
            store.begin(job, token, stage, {'controlled_evidence': True}, local_model_call=True)
            path = write_json(store.context(job).artifact_dir / (stage + '.json'), payload).artifact_path
            store.complete(job, token, stage, payload, [path])
        store.block(job, token, 'script_feedback', category='script_feedback_rejected', message='Controlled rejected draft')
    return prefix, raw, review


@pytest.mark.parametrize('failure', ['local_retry_paused', 'resource_unavailable', 'dependency_failure', 'running', 'tampered_draft'])
def test_terminal_review_format_failure_can_only_continue_as_unassessed_source(
        store, original_order, feedback, four_thoughts, failure, monkeypatch):
    job, _ = original_order
    registered = register_feedback(store.settings, job, feedback, approved=True)
    prefix = 'script_scoped/' + registered['feedback_hash'][:12]
    last = feedback.max_drafts - 1
    candidate = four_thoughts.model_dump(mode='json')
    canonical_path = store.context(job).artifact_dir / 'script.json'
    canonical = canonical_path.read_bytes()
    with store.lease(job) as token:
        stage = prefix + f'/draft/{last}'
        store.begin(job, token, stage, {'fixture': 'unassessed source'}, local_model_call=True)
        path = write_json(store.context(job).artifact_dir / 'unassessed-source.json', candidate).artifact_path
        store.complete(job, token, stage, candidate, [path])
        stage = prefix + f'/review/{last}'
        store.begin(job, token, stage, {'candidate': candidate}, local_model_call=True)
        if failure != 'running':
            store.fail(job, token, stage, category=(failure if failure in {'dependency_failure', 'resource_unavailable'}
                       else 'local_retry_paused'), message='Controlled terminal review format failure')
        store.block(job, token, stage, category='controlled_failure', message='Review did not complete')
    if failure == 'tampered_draft':
        path.write_text('{}', encoding='utf-8')
    before = store.get(job)
    if failure in {'local_retry_paused', 'resource_unavailable'}:
        register_feedback(store.settings, job, _followup(feedback), approved=True, followup=True)
        request = load_feedback(store, job)
        source = request['rejected_continuation']
        assert source['status'] == 'unassessed_not_accepted'
        assert source['review'] == {'status': 'unassessed', 'issues': []}
        assert source['script'] == candidate
        assert source['assessment_failure']['stage'] == stage
        after = store.get(job)
        assert after['provider_calls'] == before['provider_calls']
        assert after['checkpoints'] == before['checkpoints']
        assert canonical_path.read_bytes() == canonical
        calls = []
        async def writer(*args, **kwargs):
            calls.append('writer')
            return four_thoughts.model_dump(mode='json')
        async def reviewer(*args, **kwargs):
            calls.append('reviewer')
            return {'status': 'failed', 'issues': [{'code': 'naturalness', 'segment_ids': ['s01'],
                'detail': 'Controlled new independent rejection.'}]}
        monkeypatch.setattr(order_script, 'ask_model', writer)
        monkeypatch.setattr(order_script, 'review', reviewer)
        with store.lease(job) as token, pytest.raises(ProductionBlocked):
            asyncio.run(produce_script(StageExecutor(store, job, token)))
        assert 'writer' in calls and 'reviewer' in calls
        assert canonical_path.read_bytes() == canonical
        assert not any(row['stage'].endswith('/accepted') for row in store.get(job)['checkpoints'])
    else:
        with pytest.raises(OrderConflict, match='matching final rejected'):
            register_feedback(store.settings, job, _followup(feedback), approved=True, followup=True)


@pytest.mark.parametrize('change', ['prompt_version', 'draft_file'])
def test_consumed_scoped_draft_cannot_replay_after_input_or_evidence_change(
        store, original_order, feedback, four_thoughts, monkeypatch, change):
    from atnos.production import scoped_script_revision as scoped
    job, _ = original_order
    feedback = feedback.model_copy(update={'max_drafts': 1})
    registered = register_feedback(store.settings, job, feedback, approved=True)
    async def writer(*args, **kwargs):
        assert args[2].endswith(order_script.REGISTERED_EDITORIAL_PRECEDENCE_RULE)
        return four_thoughts.model_dump(mode='json')
    async def reviewer(*args, **kwargs):
        return {'status': 'failed', 'issues': [{'code': 'naturalness',
            'segment_ids': ['s01'], 'detail': 'Controlled rejected candidate.'}]}
    monkeypatch.setattr(order_script, 'ask_model', writer)
    monkeypatch.setattr(order_script, 'review', reviewer)
    with store.lease(job) as token, pytest.raises(ProductionBlocked):
        asyncio.run(produce_script(StageExecutor(store, job, token)))
    before = store.get(job)
    canonical = (store.context(job).artifact_dir / 'script.json').read_bytes()
    draft = next(row for row in before['checkpoints'] if row['stage'] ==
        'script_scoped/' + registered['feedback_hash'][:12] + '/draft/0')
    assert draft['status'] == 'done'
    if change == 'prompt_version':
        monkeypatch.setattr(scoped, 'SCOPED_WRITER_PROMPT_VERSION', scoped.SCOPED_WRITER_PROMPT_VERSION + 1)
    else:
        (store.context(job).artifact_dir / draft['files'][0]['path']).write_text('{}', encoding='utf-8')
    async def forbidden(*args, **kwargs):
        raise AssertionError('A consumed slot must not issue another model call')
    monkeypatch.setattr(order_script, 'ask_model', forbidden)
    monkeypatch.setattr(order_script, 'review', forbidden)
    with store.lease(job) as token, pytest.raises(ProductionBlocked, match='cannot be replayed'):
        asyncio.run(produce_script(StageExecutor(store, job, token)))
    after = store.get(job)
    assert after['provider_calls'] == before['provider_calls']
    assert after['checkpoints'] == before['checkpoints']
    assert (store.context(job).artifact_dir / 'script.json').read_bytes() == canonical


def test_accepted_followup_has_immutable_dependencies_and_keeps_counters(store, original_order, feedback, four_thoughts, monkeypatch):
    job, _ = original_order
    _accept_first(store, job, feedback, four_thoughts, monkeypatch)
    before = store.get(job)
    canonical = next(r for r in before['checkpoints'] if r['stage'] == 'script')
    assert 'script_feedback/request.json' not in {f['path'] for f in canonical['files']}
    register_feedback(store.settings, job, _followup(feedback), approved=True, followup=True)
    after = store.get(job)
    assert after['provider_calls'] == before['provider_calls']
    assert after['reserved_cost_units'] == before['reserved_cost_units']
    assert after['request'] == before['request'] and after['profile_hash'] == before['profile_hash']
    assert [(r['stage'], r['attempts']) for r in after['checkpoints']] == [(r['stage'], r['attempts']) for r in before['checkpoints']]
    assert store._valid_files(store.context(job).artifact_dir, canonical['files_json'])
    assert load_feedback(store, job)['round'] == 2


def test_changed_accepted_db_output_cannot_authorize_followup(store, original_order, feedback, four_thoughts, monkeypatch):
    job, _ = original_order
    _accept_first(store, job, feedback, four_thoughts, monkeypatch)
    prefix = 'script_scoped/' + load_feedback(store, job)['feedback_hash'][:12]
    with connect(store.settings) as db:
        db.execute('UPDATE pipeline_stage_checkpoints SET output_json=? WHERE job_id=? AND stage=?',
                   (json_dumps({'qa': {'status': 'passed'}}), job, prefix + '/accepted'))
        db.commit()
    with pytest.raises(OrderConflict, match='matching final rejected'):
        register_feedback(store.settings, job, _followup(feedback), approved=True, followup=True)


def test_rejected_followup_uses_actual_matching_draft_without_acceptance_or_reset(store, original_order, feedback, four_thoughts):
    job, original = original_order
    prefix, raw, review = _rejected_final(store, job, feedback, four_thoughts)
    before = store.get(job)
    result = register_feedback(store.settings, job, _followup(feedback), approved=True, followup=True)
    request = load_feedback(store, job)
    assert request['original_script'] == original
    assert request['rejected_continuation']['script'] == raw
    assert request['rejected_continuation']['review'] == review
    assert request['rejected_continuation']['status'] == 'rejected_not_accepted'
    after = store.get(job)
    assert not any(r['stage'] == prefix + '/accepted' for r in after['checkpoints'])
    assert result['feedback_hash'] != before['profile_hash']
    assert after['provider_calls'] == before['provider_calls'] and after['reserved_cost_units'] == before['reserved_cost_units']
    assert [(r['stage'], r['attempts']) for r in after['checkpoints']] == [(r['stage'], r['attempts']) for r in before['checkpoints']]


@pytest.mark.parametrize('damage', ['draft_file', 'review_hash', 'missing_draft'])
def test_rejected_followup_refuses_incomplete_or_spoofed_evidence(store, original_order, feedback, four_thoughts, damage):
    job, _ = original_order
    prefix, raw, review = _rejected_final(store, job, feedback, four_thoughts)
    root = store.context(job).artifact_dir
    stage = prefix + f'/draft/{feedback.max_drafts - 1}'
    if damage == 'draft_file':
        (root / (stage + '.json')).write_text('{}')
    else:
        with connect(store.settings) as db:
            if damage == 'missing_draft':
                db.execute('DELETE FROM pipeline_stage_checkpoints WHERE job_id=? AND stage=?', (job, stage))
            else:
                review['script_hash'] = '0' * 64
                path = write_json(root / (prefix + f'/review/{feedback.max_drafts - 1}.json'), review).artifact_path
                from atnos.production.order_store import file_hash
                files = [{'path': path.relative_to(root).as_posix(), 'sha256': file_hash(path)}]
                db.execute('UPDATE pipeline_stage_checkpoints SET output_json=?,files_json=? WHERE job_id=? AND stage=?',
                           (json_dumps(review), json_dumps(files), job, prefix + f'/review/{feedback.max_drafts - 1}'))
            db.commit()
    with pytest.raises(OrderConflict, match='matching final rejected'):
        register_feedback(store.settings, job, _followup(feedback), approved=True, followup=True)


def test_legacy_mutable_request_dependency_uses_only_its_hashed_history(store, original_order, feedback, four_thoughts, monkeypatch):
    from atnos.production.order_store import file_hash
    job, _ = original_order
    _accept_first(store, job, feedback, four_thoughts, monkeypatch)
    root = store.context(job).artifact_dir
    canonical = next(r for r in store.get(job)['checkpoints'] if r['stage'] == 'script')
    files = [*canonical['files'], {'path': 'script_feedback/request.json', 'sha256': file_hash(root / 'script_feedback/request.json')}]
    with connect(store.settings) as db:
        db.execute("UPDATE pipeline_stage_checkpoints SET files_json=? WHERE job_id=? AND stage='script'",
                   (json_dumps(files), job))
        db.commit()
    second = _followup(feedback)
    second_result = register_feedback(store.settings, job, second, approved=True, followup=True)
    prefix = 'script_scoped/' + second_result['feedback_hash'][:12]
    raw = four_thoughts.model_dump(mode='json')
    review = {'status': 'failed', 'issues': [{'code': 'long_segment', 'segment_ids': ['s03'], 'detail': 'Actual rejected verdict'}],
              'feedback_hash': second_result['feedback_hash'], 'script_hash': content_hash(raw)}
    with store.lease(job) as token:
        for stage, payload in [(prefix + '/draft/1', raw), (prefix + '/review/1', review)]:
            store.begin(job, token, stage, {'controlled_evidence': True})
            path = write_json(root / (stage + '.json'), payload).artifact_path
            store.complete(job, token, stage, payload, [path])
        store.block(job, token, 'script_feedback', category='script_feedback_rejected', message='Controlled rejected verdict')
    assert not store._valid_files(root, json_dumps(files))
    third = feedback.model_copy(update={'reason': 'Correct the remaining actual defect in a third explicitly authorized round.'})
    register_feedback(store.settings, job, third, approved=True, followup=True)
    assert load_feedback(store, job)['round'] == 3
    assert load_feedback(store, job)['rejected_continuation']['status'] == 'rejected_not_accepted'


def test_rejected_continuation_writer_receives_actual_draft_and_requires_new_review(store, original_order, feedback, four_thoughts, monkeypatch):
    job, original = original_order
    _, raw, _ = _rejected_final(store, job, feedback, four_thoughts)
    followup = _followup(feedback)
    register_feedback(store.settings, job, followup, approved=True, followup=True)
    calls = []
    async def writer(settings, context, prompt, schema, **kwargs):
        calls.append('writer')
        assert 'PREVIOUS SCRIPT: ' + order_script.EditorialScript.model_validate(raw).model_dump_json() in prompt
        assert 'PREVIOUS REVIEW DEFECTS:' in prompt and 'long_segment' in prompt
        return four_thoughts.model_dump(mode='json')
    async def reviewer(settings, context, candidate):
        calls.append('reviewer')
        assert candidate == four_thoughts
        return {'status': 'passed', 'issues': [], 'semantic_review': 'controlled_new_review'}
    monkeypatch.setattr(order_script, 'ask_model', writer)
    monkeypatch.setattr(order_script, 'review', reviewer)
    before = store.get(job)
    with store.lease(job) as token:
        result = asyncio.run(produce_script(StageExecutor(store, job, token)))
    assert calls == ['writer', 'reviewer']
    assert result['qa']['status'] == 'passed' and result['qa']['human_review'] == 'not_performed'
    assert result['qa']['feedback_hash'] == content_hash(followup.model_dump(mode='json'))
    assert store.get(job)['provider_calls'] == before['provider_calls'] + 2
    assert load_feedback(store, job)['original_script'] == original


def test_followup_keeps_previous_request_archive_under_integrity_gate(store, original_order, feedback, four_thoughts):
    job, _ = original_order
    _rejected_final(store, job, feedback, four_thoughts)
    register_feedback(store.settings, job, _followup(feedback), approved=True, followup=True)
    root = store.context(job).artifact_dir
    request = load_feedback(store, job)
    (root / request['previous_request_archive']).write_text('{}')
    with pytest.raises(ProductionBlocked, match='archive'):
        load_feedback(store, job)


def test_explicit_rejected_continuation_uses_remaining_registered_draft_without_replay(store, original_order, feedback, four_thoughts, monkeypatch):
    job, _ = original_order
    prefix, raw, _ = _rejected_final(store, job, feedback, four_thoughts, index=0)
    before = store.get(job)
    calls = []
    async def writer(settings, context, prompt, schema, **kwargs):
        calls.append('writer')
        assert 'AT MOST 32 words' in prompt and 'END OF REJECTED SCRIPT' in prompt
        assert 'PREVIOUS SCRIPT: ' + order_script.EditorialScript.model_validate(raw).model_dump_json() in prompt
        return four_thoughts.model_dump(mode='json')
    async def reviewer(*a):
        calls.append('reviewer')
        return {'status': 'passed', 'issues': [], 'semantic_review': 'controlled_new_review'}
    monkeypatch.setattr(order_script, 'ask_model', writer)
    monkeypatch.setattr(order_script, 'review', reviewer)
    with store.lease(job) as token:
        executor = StageExecutor(store, job, token)
        with pytest.raises(ValueError, match='Explicit'):
            authorize_rejected_continuation(executor)
        authorize_rejected_continuation(executor, approved=True)
        assert store.get(job)['provider_calls'] == before['provider_calls']
        result = asyncio.run(produce_script(executor))
    assert calls == ['writer', 'reviewer'] and result['qa']['status'] == 'passed'
    after = store.get(job)
    assert after['provider_calls'] == before['provider_calls'] + 2
    for old in before['checkpoints']:
        if old['stage'].startswith(prefix):
            current = next(r for r in after['checkpoints'] if r['stage'] == old['stage'])
            assert current['attempts'] == old['attempts'] and current['output'] == old['output']


def test_rejected_continuation_cannot_extend_exhausted_draft_allowance(store, original_order, feedback, four_thoughts):
    job, _ = original_order
    _rejected_final(store, job, feedback, four_thoughts)
    before = store.get(job)
    with store.lease(job) as token:
        with pytest.raises(OrderConflict, match='allowance is exhausted'):
            authorize_rejected_continuation(StageExecutor(store, job, token), approved=True)
    assert store.get(job)['provider_calls'] == before['provider_calls']
