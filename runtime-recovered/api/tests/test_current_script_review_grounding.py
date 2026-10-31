"""Actual ordinary-order review errors, replayed without inference or receipts."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import pytest
from atnos.production.steps import order_script as scripts
from test_production_order_script import execution, script
from test_production_order_store import store
from atnos.production.scoped_script_revision import revision_source, scoped_writing_contract, register_feedback, load_feedback
from test_scoped_script_revision import original_order, feedback
from atnos.production.order_execution import produce_script, StageExecutor
from atnos.production.order_store import ProductionBlocked, file_hash

FIXTURES = Path(__file__).parent / 'fixtures/wb_chat_short_20261010'


def test_current_scoped_operator_requirements_reach_the_reviewer(execution, script, monkeypatch):
    context = execution[2]
    context.artifacts['scoped_script_guidance'] = {
        'reason': 'CURRENT-CLOSURE: verify the renewal price and requested channel CTA.',
        'issues': [{'code': 'brief_fidelity', 'segment_ids': ['s02', 's05'],
                    'detail': 'Require an explicit renewal price check and preserve the requested channel CTA.'}]}
    captured = []
    async def review(settings, context, prompt, schema, **kwargs):
        captured.append((prompt, scripts.registered_script_system_context(context, reviewing=True)))
        assert kwargs['reject_duplicate_keys'] is True
        return {'review_format': 'registered_named_v1', 'passed': True, 'issues': [],
            'editorial_checks': {f'criterion_0_segment_{sid}': {
                'satisfied': True, 'field': 'text',
                'quote': next(s.text for s in script.segments if s.id == sid),
                'reason': 'Controlled format fixture, not LIVE semantic quality.'}
                for sid in ('s02', 's05')}}
    monkeypatch.setattr(scripts, 'ask_model', review)
    asyncio.run(scripts.review(execution[0].settings, context, script))
    assert ' ACTIVE REGISTERED REPAIR: ' not in captured[0][0]
    bundle = captured[0][1].split('CURRENT REGISTERED TARGETS: ', 1)[1].split(' CURRENT WRITING LIMITS:', 1)[0]
    payload = json.loads(bundle)
    assert payload['issues'][0]['detail'].startswith('Require an explicit renewal price')
    assert payload['note'].startswith('CURRENT-CLOSURE')
    assert 'not evidence' in payload['scope']


def test_actual_review_cannot_repeat_resolved_machine_findings(execution):
    draft = scripts.EditorialScript.model_validate(json.loads((FIXTURES/'reviewed-draft.json').read_text('utf-8')))
    recorded = json.loads((FIXTURES/'stale-review.json').read_text('utf-8'))
    assert 90 <= sum(len(s.text.split()) for s in draft.segments) <= 125
    assert all(not s.fact_ids for s in draft.segments)
    current = scripts.script_issues(draft, execution[2].artifacts['project_profile'])
    assert not {i['code'] for i in current} & {'word_budget', 'unknown_source_fact'}
    verdict = scripts.ScriptReview.model_validate({'passed':False, 'issues':recorded['issues']})
    with pytest.raises(ValueError, match='contradicts current deterministic'):
        scripts.validate_review_machine_findings(verdict, current)


@pytest.mark.parametrize('code', ['word_budget','unknown_source_fact'])
def test_machine_errors_still_fail_when_current_and_reject_wrong_targets(code):
    verdict = scripts.ScriptReview.model_validate({'passed':False, 'issues':[
        {'code':code, 'segment_ids':['s02'], 'detail':'Actual current error.'}]})
    scripts.validate_review_machine_findings(verdict, [{'code':code,'segment_ids':['s02']}])
    with pytest.raises(ValueError):
        scripts.validate_review_machine_findings(verdict, [{'code':code,'segment_ids':['s03']}])


def test_unsupported_prose_is_not_discarded_as_missing_fact_id():
    verdict = scripts.ScriptReview.model_validate({'passed':False, 'issues':[
        {'code':'unsupported_claim','segment_ids':['s03'],'detail':'The current fee-free promise is unsupported.'}]})
    scripts.validate_review_machine_findings(verdict, [])
    assert not verdict.passed and verdict.issues[0].code == 'unsupported_claim'


def test_normal_recovery_historical_findings_do_not_leak_to_reviewer(execution, script, monkeypatch):
    context = execution[2]
    rule = {'source':'studio_script_recovery','request_digest':'a'*64,'feedback':{
        'reason':'OLD_REJECTED_WORDING', 'issues':[{'code':'word_budget','segment_ids':['s01'],
        'detail':'66 words; expected 90-125'}]}}
    context.artifacts['project_profile']['feedback_rules'] = [rule]
    before = deepcopy(rule)
    captured = []
    async def mocked_review(settings, context, prompt, schema, **kwargs):
        captured.append(prompt)
        return {'passed':True,'issues':[]}
    monkeypatch.setattr(scripts,'ask_model',mocked_review)
    result = asyncio.run(scripts.review(execution[0].settings,context,script))
    assert result['status'] == 'passed'  # Mock format only, never LIVE quality.
    assert '66 words' not in captured[0] and 'OLD_REJECTED_WORDING' not in captured[0]
    assert 'prior_issue_categories' in captured[0]
    assert 'CURRENT DETERMINISTIC CHECKS' in captured[0]
    assert rule == before


@pytest.mark.parametrize('code', ['word_budget','unsupported_claim','source_entailment'])
def test_short_emergency_reserve_revision_is_not_redirected_to_pay_raise(execution, script, code):
    context = execution[2]
    context.artifacts['production_order'].update(topic='Your Emergency Fund Is Not an Investment',
        direction='Distinguish emergency readiness from a longer-term goal; preserve the original CTA.')
    feedback = {'issues':[{'code':code}]}
    source = revision_source(context, script, feedback, [])
    assert 'FRESH FULL REWRITE' in source
    assert 'Your Emergency Fund Is Not an Investment' in source
    assert 'emergency readiness' in source
    assert not any(value in source for value in ('additional income', 'recurring commitment', 'pay raise'))
    assert 'requested CTA' in source
    assert 'PREVIOUS SCRIPT' not in source
    assert 'recurring commitment' not in scoped_writing_contract(context)


def test_claim_blocked_activation_can_be_corrected_without_promoting_or_resetting(store, original_order, feedback, monkeypatch):
    job, _ = original_order
    candidate = json.loads((FIXTURES/'claim-blocked-candidate.json').read_text('utf-8'))
    async def writer(*args, **kwargs):
        return candidate
    async def mocked_review(*args):
        return {'status':'passed','issues':[],'semantic_review':'mock_not_quality'}
    monkeypatch.setattr(scripts,'ask_model',writer)
    monkeypatch.setattr(scripts,'review',mocked_review)
    register_feedback(store.settings,job,feedback,approved=True)
    root = store.context(job).artifact_dir
    old_hash = file_hash(root/'script.json')
    with store.lease(job) as token, pytest.raises(ProductionBlocked):
        asyncio.run(produce_script(StageExecutor(store,job,token)))
    before = store.get(job)
    assert before['current_step'] == 'script'
    assert file_hash(root/'script.json') == old_hash


    followup = feedback.model_copy(update={'reason':'Correct the actual unsupported definition after claim gate rejection.'})
    register_feedback(store.settings,job,followup,approved=True,followup=True)
    after = store.get(job)
    request = load_feedback(store,job)
    assert request['rejected_continuation']['status'] == 'activation_blocked_not_accepted'
    assert request['rejected_continuation']['review']['status'] == 'failed'
    assert after['provider_calls'] == before['provider_calls']
    assert [(r['stage'],r['attempts']) for r in after['checkpoints']] == [(r['stage'],r['attempts']) for r in before['checkpoints']]
    assert file_hash(root/'script.json') == old_hash
    # A later model rejection must not erase the preserved canonical's provenance
    # or make the next registered correction impossible.
    candidate['segments'][1]['text'] += ' Check your situation.'
    async def rejected_review(*args):
        return {'status':'failed','issues':[{'code':'brief_fidelity','segment_ids':['s02'],
                'detail':'Actual isolated candidate still fails the requested brief.'}],
                'semantic_review':'mock_not_quality'}
    monkeypatch.setattr(scripts,'review',rejected_review)
    with store.lease(job) as token, pytest.raises(ProductionBlocked):
        asyncio.run(produce_script(StageExecutor(store,job,token)))
    third = followup.model_copy(update={'reason':'Correct the later rejected candidate while preserving the old canonical.'})
    register_feedback(store.settings,job,third,approved=True,followup=True)
    assert load_feedback(store,job)['rejected_continuation']['status'] == 'rejected_not_accepted'
    assert file_hash(root/'script.json') == old_hash


def test_scoped_authoring_branch_admits_registered_lanes_before_early_return(execution, monkeypatch):
    task_store, job, context = execution
    lane = {'execution_scope':'normal_produce_script_authoring','digest':'a'*64,'model':'qwen3.5:9b'}
    monkeypatch.setattr('atnos.production.script_model_lane.load_script_model_lane', lambda *a, **k: lane)
    monkeypatch.setattr('atnos.production.script_review_model_lane.load_script_review_model_lane', lambda *a, **k: None)
    observed = []
    async def scoped(executor):
        observed.append(executor.context.artifacts.get('_active_script_model_lane'))
        return {'isolated_scope_probe':True}
    monkeypatch.setattr('atnos.production.scoped_script_revision.produce_requested',scoped)
    with task_store.lease(job) as token:
        executor = StageExecutor(task_store,job,token)
        assert asyncio.run(produce_script(executor)) == {'isolated_scope_probe':True}
        assert '_active_script_model_lane' not in executor.context.artifacts
    assert observed == [lane]
