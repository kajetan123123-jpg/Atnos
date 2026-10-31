"""Read-only acceptance proof after normal finalization; no LIVE quality receipts."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from atnos.production import order_execution, studio_script_recovery as recovery
from atnos.production.hypothetical_claim_evidence import editorial_script_payload
from atnos.production.order_execution import StageExecutor
from atnos.production.order_store import ProductionBlocked, file_hash
from atnos.production.project_profile import content_hash
from atnos.production.steps import order_script
from atnos.production.steps.common import write_json
from test_controlled_script_repair import assessment, controlled_action, held, registered
from test_production_order_script import script
from test_production_order_store import store
from test_studio_user_editorial_feedback import canonical


@pytest.fixture(autouse=True)
def no_inference(monkeypatch):
    async def forbidden(*_args, **_kwargs):
        pytest.fail('Finalized acceptance proof attempted real inference')
    monkeypatch.setattr(order_script, 'ask_ollama_json', forbidden)


@pytest.fixture
def finalized(store, registered, monkeypatch):
    """Normal registered producer, hold, assessment and finalize; model calls stubbed."""
    job, source, request = registered
    monkeypatch.setattr('atnos.production.script_model_lane.load_script_model_lane', lambda *_a, **_k: None)
    monkeypatch.setattr('atnos.production.script_review_model_lane.load_script_review_model_lane', lambda *_a, **_k: None)
    monkeypatch.setattr(order_script, 'gather_script_context', lambda *_: {
        'reference_source': 'isolated fixture', 'usage': 'contract test only'})
    calls = []
    async def repaired(*_a, **_k):
        calls.append('offline writer fixture')
        return order_script.EditorialScript.model_validate(source)
    async def reviewed(*_a, **_k):
        calls.append('offline reviewer fixture')
        return {'status': 'passed', 'issues': []}
    monkeypatch.setattr(order_script, 'repair', repaired)
    monkeypatch.setattr(order_script, 'review', reviewed)
    monkeypatch.setattr(order_script, 'script_issues', lambda *_a, **_k: [])
    monkeypatch.setattr(order_script, 'reviewed_source_facts', lambda *_a: {})
    with store.lease(job) as token:
        with pytest.raises(ProductionBlocked, match='independent_semantic_review_required'):
            asyncio.run(order_execution.produce_script(StageExecutor(store, job, token)))
    candidate = recovery.load_script_candidate(store, job)
    receipt = recovery.register_candidate_assessment(store, job, assessment(candidate))
    with store.lease(job) as token:
        result = asyncio.run(order_execution.produce_script(StageExecutor(store, job, token)))
    assert calls == ['offline writer fixture', 'offline reviewer fixture']
    return job, source, request, candidate, receipt, result


def snapshot(store, job):
    state = store.get(job)
    root = Path(state['artifact_dir'])
    return state, {p.relative_to(root).as_posix(): file_hash(p) for p in root.rglob('*') if p.is_file()}


def test_normal_finalized_acceptance_is_read_only_and_bound_to_real_receipts(store, finalized):
    job, _, request, candidate, receipt, result = finalized
    before = snapshot(store, job)
    proof = recovery.verify_finalized_script_acceptance(store, job, script=result)
    assert proof['job_id'] == job
    assert proof['request_digest'] == content_hash(request)
    assert proof['request_key'] == request['request_key']
    assert proof['candidate_hash'] == content_hash(editorial_script_payload(result)) == candidate['candidate_hash']
    assert proof['canonical_sha256'] == file_hash(Path(store.get(job)['artifact_dir']) / 'script.json')
    assert proof['assessment_hash'] == receipt['assessment_hash']
    assert proof['assessment_receipt_sha256'] == receipt['receipt_sha256']
    assert snapshot(store, job) == before


def test_plan_successor_does_not_erase_finalized_acceptance_or_relax_prefinalize_guard(store, finalized):
    job, _, _, candidate, _, result = finalized
    # A plan successor is deliberately outside this verifier's attestation.
    # This isolated plan fixture simulates changed downstream bytes, not AV permission.
    root = Path(store.get(job)['artifact_dir'])
    write_json(root / 'scene_plan.json', {'fixture': 'downstream plan after accepted canonical'})
    before = snapshot(store, job)
    proof = recovery.verify_finalized_script_acceptance(store, job, script=result)
    assert proof['candidate_hash'] == candidate['candidate_hash']
    assert snapshot(store, job) == before
    with pytest.raises(ProductionBlocked, match='plan changed'):
        recovery.load_script_candidate(store, job)
    with pytest.raises(ProductionBlocked, match='plan changed'):
        recovery.require_candidate_acceptance(store, job, script=candidate['script'])


def test_legacy_pass_is_not_controlled_finalized_acceptance(store, canonical):
    job, source = canonical
    assert source['qa']['status'] == 'passed'
    with pytest.raises(ProductionBlocked):
        recovery.verify_finalized_script_acceptance(store, job)


@pytest.mark.parametrize('decision', ['missing', 'accept', 'reject', 'loose_fixture'])
def test_hold_or_assessment_alone_never_proves_finalized_canonical(store, registered, decision):
    candidate = held(store, registered)
    job = registered[0]
    if decision in {'accept', 'reject'}:
        value = assessment(candidate)
        if decision == 'reject':
            value['decision'] = 'reject'
            value['checks'][0]['satisfied'] = False
        recovery.register_candidate_assessment(store, job, value)
    elif decision == 'loose_fixture':
        write_json(Path(store.get(job)['artifact_dir']) / 'loose-accepted-review.json', assessment(candidate))
    before = snapshot(store, job)
    with pytest.raises(ProductionBlocked):
        recovery.verify_finalized_script_acceptance(store, job)
    assert snapshot(store, job) == before


@pytest.mark.parametrize('target', ['canonical', 'candidate', 'assessment'])
def test_changed_receipt_or_canonical_file_fails_closed(store, finalized, target):
    job, _, _, _, _, _ = finalized
    state = store.get(job)
    root = Path(state['artifact_dir'])
    if target == 'canonical':
        path = root / 'script.json'
    elif target == 'candidate':
        row = next(c for c in state['checkpoints'] if c['stage'].startswith('script_candidate/'))
        path = root / row['files'][0]['path']
    else:
        event = next(e for e in state['history'] if e['event'] == 'script_candidate_assessed')
        path = root / event['detail']['receipt_path']
    path.write_text('{}', encoding='utf-8')
    before = snapshot(store, job)
    with pytest.raises(ProductionBlocked):
        recovery.verify_finalized_script_acceptance(store, job)
    assert snapshot(store, job) == before


@pytest.mark.parametrize('fault', ['assessment_hash', 'receipt_hash', 'request_digest', 'candidate_hash',
                                   'qa_pass_only', 'provenance', 'model_identity', 'qa_script_hash'])
def test_done_checkpoint_with_misbound_qa_is_not_acceptance(store, finalized, fault):
    job, _, _, candidate, _, result = finalized
    wrong = deepcopy(result)
    if fault in {'assessment_hash', 'candidate_hash', 'request_digest'}:
        wrong['qa']['independent_assessment'][fault] = '0' * 64
    elif fault == 'receipt_hash':
        wrong['qa']['independent_assessment']['receipt_sha256'] = '0' * 64
    elif fault == 'qa_pass_only':
        wrong['qa'] = {'status': 'passed', 'issues': []}
    elif fault == 'provenance':
        wrong['provenance']['studio_recovery']['request_digest'] = '0' * 64
    elif fault == 'model_identity':
        wrong['provenance']['reviewer'] = 'unrelated reviewer'
    else:
        wrong['qa']['script_hash'] = '0' * 64
    # Fault injection through isolated normal stage APIs: no direct DB/checkpoint edits.
    root = Path(store.get(job)['artifact_dir'])
    with store.lease(job) as token:
        store.begin(job, token, 'script', {'script': candidate['script'], 'fixture': fault})
        path = write_json(root / 'script.json', wrong).artifact_path
        store.complete(job, token, 'script', wrong, [path])
    before = snapshot(store, job)
    with pytest.raises(ProductionBlocked):
        recovery.verify_finalized_script_acceptance(store, job)
    assert snapshot(store, job) == before


def test_input_must_match_full_finalized_envelope(store, finalized):
    job, _, _, candidate, _, result = finalized
    for wrong in [candidate['script'], {**result, 'qa': {'status': 'passed'}}]:
        with pytest.raises(ProductionBlocked):
            recovery.verify_finalized_script_acceptance(store, job, script=wrong)


def test_new_registration_invalidates_previous_finalized_acceptance(store, finalized):
    job, _, _, _, _, _ = finalized
    with store.lease(job) as token:
        store.block(job, token, 'script_review/0', category='execution_failure', message='Isolated new user reassessment')
    next_action = controlled_action(store, job, request_key='next-controlled-request')
    recovery.register_script_repair(store, job, next_action)
    with pytest.raises(ProductionBlocked):
        recovery.verify_finalized_script_acceptance(store, job)
