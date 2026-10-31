"""Historical canonical + current renewal fixtures; never actual model/quality evidence."""
import asyncio
import copy
import json

import pytest

from atnos.intelligence.storage import connect, json_dumps
from atnos.production import scoped_script_revision as scoped, voice_revision as revision
from atnos.production.order_execution import StageExecutor
from atnos.production.order_store import ProductionBlocked, file_hash
from atnos.production.project_profile import content_hash
from atnos.production.steps.order_script import SCRIPT_VALIDATOR_VERSION
from test_local_voice_revision import trial, register, executor
from test_production_order_script import script
from test_production_order_store import store
from test_renderer_refresh_followup import complete


def mark_ready(t):
    # Isolated fixture only, never an application recovery shortcut.
    with connect(t.store.settings) as db:
        db.execute("UPDATE pipeline_jobs SET status='ready',current_step='ready',error=NULL WHERE id=?", (t.job,))
        db.commit()


@pytest.fixture
def renewed(trial, monkeypatch):
    t = trial
    req = {'feedback_hash': 'e'*64, 'feedback': {'target_segments': [6, 6], 'issues': [{
        'code': 'fixture_criterion', 'segment_ids': ['s01'], 'field': 'text',
        'detail': 'ISOLATED FIXTURE: quote the existing first sentence'}]}}
    with t.store.lease(t.job) as token:
        complete(t.store, t.job, token, 'script_scoped/' + req['feedback_hash'][:12] + '/accepted',
                 {'fixture': 'historical accepted'}, t.root/'accepted-fixture.json', t.script)
    mark_ready(t)
    from atnos.production.remotion_quality_repair import register_renderer_refresh
    action = asyncio.run(register_renderer_refresh(t.store, t.job, expected_package_sha256='b'*64,
        reason='ISOLATED FIXTURE: prior registered renderer correction with current model review', authorized=True))
    state = t.store.get(t.job)
    accepted = next(r for r in state['checkpoints'] if r['stage'].endswith('/accepted'))
    inputs = {'action_digest': action['request_digest'], 'canonical_sha256': file_hash(t.root/'script.json'),
        'candidate_hash': content_hash({k: t.script[k] for k in scoped.order_script.EditorialScript.model_fields}),
        'accepted_input_hash': accepted['input_hash'], 'feedback_hash': req['feedback_hash'],
        'review_prompt_version': 3, 'review_contract_version': SCRIPT_VALIDATOR_VERSION,
        'feedback_validator_version': scoped.SCOPED_FEEDBACK_VALIDATOR_VERSION}
    result = {**inputs, 'status': 'passed', 'issues': [], 'semantic_review': 'model_reviewed',
        'scope': 'review_only', 'canonical_changed': False, 'issue_evidence': [], 'editorial_checks': [{
            'criterion_index': 0, 'segment_id': 's01', 'field': 'text', 'quote': t.script['segments'][0]['text'],
            'satisfied': True, 'reason': 'ISOLATED stored review fixture, not a real assessment.'}]}
    stage = 'remotion_script_review/' + action['request_digest'][:12] + '/v' + str(SCRIPT_VALIDATOR_VERSION) + '-p3'
    path = t.root/'renderer_refresh'/action['request_digest'][:12]/'review-fixture.json'
    with t.store.lease(t.job) as token:
        complete(t.store, t.job, token, stage, inputs, path, result)
    mark_ready(t)
    monkeypatch.setattr(scoped, 'load_feedback', lambda *_: req)
    proof, _ = scoped.verified_current_renderer_review(t.store, t.store.get(t.job))
    t.binding = t.binding.model_copy(update={'current_script_review': revision.CurrentScriptReviewBinding(**proof)})
    t.review_stage, t.review_path, t.review_result = stage, path, result
    t.accepted, t.feedback_request = accepted, req
    return t


@pytest.mark.parametrize('trial', [True], indirect=True)
def test_current_registered_review_reused_without_new_model_or_canonical_change(renewed):
    t = renewed
    prepared = revision.prepare_local_voice_revision(t.store, t.job,
        candidate_receipt_path=t.binding.candidate_receipt_path, audition_path=t.binding.audition_path,
        selection_path=t.binding.selection_path, feedback_id=t.binding.feedback_id)
    assert prepared == t.binding
    register(t)
    before = copy.deepcopy(t.store.get(t.job))
    canonical_sha = file_hash(t.root/'script.json')
    with t.store.lease(t.job) as token:
        ex = executor(t, token)
        async def forbidden(*a, **k):pytest.fail('Reuse must not reserve any new stage or call a model')
        ex.run = forbidden
        for _ in range(2):
            assert asyncio.run(scoped.renew_renderer_review(ex, t.feedback_request, t.accepted, t.script, {})) == t.review_result
    after = t.store.get(t.job)
    assert after['checkpoints'] == before['checkpoints']
    assert after['provider_calls'] == before['provider_calls']
    assert file_hash(t.root/'script.json') == canonical_sha
    assert json.loads((t.root/'script.json').read_text())['qa']['review_contract_version'] is None


@pytest.mark.parametrize('trial', [True], indirect=True)
@pytest.mark.parametrize('case', ['canonical_sha256', 'candidate_hash', 'feedback_hash', 'accepted_input_hash',
    'review_contract_version', 'false_pass', 'missing_criterion', 'absent_quote', 'input_hash'])
def test_reuse_rejects_stale_and_internally_consistent_false_pass_receipts(renewed, case):
    t = renewed; result = copy.deepcopy(t.review_result)
    if case == 'false_pass':result['editorial_checks'][0]['satisfied'] = False
    elif case == 'missing_criterion':result['editorial_checks'] = []
    elif case == 'absent_quote':result['editorial_checks'][0]['quote'] = 'THIS QUOTE DOES NOT EXIST'
    elif case == 'review_contract_version':result[case] -= 1
    elif case != 'input_hash':result[case] = 'f'*64
    t.review_path.write_text(json.dumps(result), encoding='utf-8')
    row = next(r for r in t.store.get(t.job)['checkpoints'] if r['stage'] == t.review_stage)
    input_hash = 'f'*64 if case == 'input_hash' else row['input_hash']
    entries = [{'path': t.review_path.relative_to(t.root).as_posix(), 'sha256': file_hash(t.review_path)}]
    with connect(t.store.settings) as db:
        db.execute('UPDATE pipeline_stage_checkpoints SET output_json=?,files_json=?,input_hash=? WHERE job_id=? AND stage=?',
            (json_dumps(result), json_dumps(entries), input_hash, t.job, t.review_stage))
        db.commit()
    proof = {'stage': t.review_stage, 'input_hash': input_hash, 'output_hash': content_hash(result)}
    with pytest.raises(ProductionBlocked):
        scoped.verified_current_renderer_review(t.store, t.store.get(t.job), proof=proof)


@pytest.mark.parametrize('trial', [True], indirect=True)
@pytest.mark.parametrize('case', ['receipt_file', 'owner_request', 'proof'])
def test_reuse_rejects_changed_registered_provenance(renewed, case):
    t = renewed
    proof = t.binding.current_script_review.model_dump()
    if case == 'receipt_file':t.review_path.write_text('{}', encoding='utf-8')
    elif case == 'owner_request':(t.review_path.parent/'request.json').write_text('{}', encoding='utf-8')
    else:proof['output_hash'] = 'f'*64
    with pytest.raises(ProductionBlocked):
        scoped.verified_current_renderer_review(t.store, t.store.get(t.job), proof=proof)


@pytest.mark.parametrize('trial', [True], indirect=True)
def test_historical_canonical_without_current_registered_review_blocks_before_registration(trial):
    t = trial; before = copy.deepcopy(t.store.get(t.job))
    with pytest.raises(ProductionBlocked, match='bound current script review'):
        register(t)
    after = t.store.get(t.job)
    assert after['checkpoints'] == before['checkpoints'] and after['history'] == before['history']
