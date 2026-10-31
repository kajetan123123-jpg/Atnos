"""User review after a prior PASS stays in the existing bounded repair path."""
import asyncio
import copy
from pathlib import Path
import pytest

from atnos.production.hypothetical_script_intake import compile_supplied_plan
from atnos.production.order_store import OrderConflict, ProductionBlocked
from atnos.production.steps.common import write_json
from atnos.production.studio_script_recovery import (
    StudioScriptRepair, register_script_repair, load_recovery_request,
    prepare_recovery_context, apply_recovery_guidance, recovery_snapshot,
    USER_EDITORIAL_REASSESSMENT_MESSAGE)
from test_hypothetical_script_intake import order
from test_production_order_script import script
from test_production_order_store import store


@pytest.fixture
def canonical(store, script):
    job = store.create(order(), request_key='editorial-after-pass')['id']
    state = store.get(job)
    calc = compile_supplied_plan(state['request']['intake'])
    source = script.model_dump(mode='json')
    source['claims'] = list(calc['claims'].values())
    source['segments'][4].update(
        text='If ten dollars earned an assumed eight percent annually for thirty years, it could become roughly one hundred one dollars.',
        claim_ids=['c1'])
    source['qa'] = {'status':'passed','issues':[]}
    with store.lease(job) as token:
        for stage, value in [('script_calculations',calc), ('script',source)]:
            store.begin(job,token,stage,{'fixture':stage})
            path = write_json(Path(state['artifact_dir'])/(stage+'.json'),value).artifact_path
            store.complete(job,token,stage,value,[path])
        store.block(job,token,'image_generation',category='execution_failure',message='Authorized image request limit reached; history and cost remain preserved')
    return job, source


def action(**changes):
    return StudioScriptRepair.model_validate({
        'request_key':'user-editorial-20261009',
        'note':'Preserve choice without investment directives; preserve the Follow Wealth Blueprint CTA.',
        'editorial_issues':[{'code':'structure','segment_ids':['s06'],
                             'detail':'Restore the channel-follow CTA, not a banking instruction.'}], **changes})


def test_register_after_pass_archives_source_preserves_counters_and_binds_user_targets(store, canonical):
    job, source = canonical
    before = store.get(job)
    first = register_script_repair(store,job,action())
    receipt = load_recovery_request(store,job)
    assert receipt == first['request'] and receipt['mode'] == 'hypothetical_review'
    assert receipt['issue_source'] == 'user_editorial_feedback_not_validator_verdict'
    assert receipt['hypothetical_evidence']['source_script']['segments'] == source['segments']
    assert receipt['submitted_editorial_issues'] == receipt['issues']
    assert receipt['max_rounds'] == 3
    after = store.get(job)
    assert after['provider_calls'] == before['provider_calls']
    assert after['reserved_cost_units'] == before['reserved_cost_units']
    assert after['request'] == before['request'] and after['profile_hash'] == before['profile_hash']
    assert [(r['stage'],r['attempts']) for r in after['checkpoints']] == [(r['stage'],r['attempts']) for r in before['checkpoints']]
    assert register_script_repair(store,job,action())['already_registered']
    changed = action().model_dump(mode='json')
    changed['editorial_issues'][0]['detail'] = 'Another requirement under the same key is not allowed.'
    with pytest.raises(OrderConflict):
        register_script_repair(store,job,changed)


@pytest.mark.parametrize('fault',['unknown_segment','autonomous','media_exists','tampered'])
def test_user_feedback_does_not_bypass_origin_scope_media_or_integrity(store, canonical, fault):
    job, source = canonical
    request = action()
    if fault == 'unknown_segment':
        request.editorial_issues[0].segment_ids = ['not-a-segment']
    elif fault == 'media_exists':
        with store.lease(job) as token:
            store.begin(job,token,'voice/narration',{'fixture':'existing speech'})
            path = write_json(Path(store.get(job)['artifact_dir'])/'fixture-voice.json',{'test':True}).artifact_path
            store.complete(job,token,'voice/narration',{'audio_path':str(path)},[path])
            store.block(job,token,'image_generation',category='execution_failure',message='Test idle state')
    elif fault == 'tampered':
        (Path(store.get(job)['artifact_dir'])/'script.json').write_text('tampered',encoding='utf-8')
    before = store.get(job)
    with pytest.raises((OrderConflict,ValueError)):
        register_script_repair(store,job,request,
            origin='autonomous_recovery' if fault == 'autonomous' else 'studio_user')
    assert store.get(job)['provider_calls'] == before['provider_calls']
    assert load_recovery_request(store,job) is None


def test_financial_edits_and_replacement_narration_are_not_accepted():
    for change in [{'code':'hypothetical_evidence'}, {'text':'Use a guaranteed ten percent return.'}]:
        raw = action().model_dump(mode='json')
        raw['editorial_issues'][0].update(change)
        with pytest.raises(ValueError):
            StudioScriptRepair.model_validate(raw)


def test_explicit_feedback_binds_reviewed_script_even_when_a_newer_draft_exists(store, canonical):
    job, source = canonical
    newer = copy.deepcopy(source)
    newer.pop('qa')
    newer['segments'][0]['text'] = 'This is a later draft, not the reviewed film script.'
    with store.lease(job) as token:
        store.begin(job, token, 'script_repair/1', {'fixture':'later unpublished draft'})
        path = write_json(Path(store.get(job)['artifact_dir'])/'script/later-draft.json',newer).artifact_path
        store.complete(job, token, 'script_repair/1', newer, [path])
        store.block(job, token, 'image_generation', category='execution_failure', message='Still pre-media')
    receipt = register_script_repair(store, job, action())['request']
    assert receipt['hypothetical_evidence']['source_stage'] == 'script'
    assert receipt['hypothetical_evidence']['source_script']['segments'] == source['segments']


def _failed_user_action(store, job, *, category='studio_script_action_limit'):
    issues=[{'code':'naturalness','segment_ids':['s06'],
             'detail':'The current final segment still lacks the channel-follow CTA.'}]
    with store.lease(job) as token:
        store.begin(job,token,'script_review/0',{'fixture':'actual review failure'})
        path=write_json(Path(store.get(job)['artifact_dir'])/'script/current-review.json',
                        {'status':'failed','issues':issues}).artifact_path
        store.complete(job,token,'script_review/0',{'status':'failed','issues':issues},[path])
        store.block(job,token,'script_repair/2',category=category,
                    message='Studio script repair reached its three-operation limit')


@pytest.mark.parametrize('category',['studio_script_action_limit','script_quality_limit'])
def test_autonomy_cannot_reserve_a_new_action_after_user_action_limit(store, canonical, monkeypatch, category):
    from atnos.production.order_autonomy import enable, policy, recover_due
    job,_=canonical
    register_script_repair(store,job,action())
    _failed_user_action(store,job,category=category)
    enable(store.settings,job,reason='Explicitly bounded autonomous local recovery for this test.')
    monkeypatch.setattr('atnos.production.studio_script_recovery.register_script_repair',
                        lambda *_a,**_k: pytest.fail('Automatic recovery bypassed the action limit'))
    monkeypatch.setattr('atnos.production.order_dispatch.ProductionOrderDispatcher.dispatch',
                        lambda *_a,**_k: pytest.fail('An exhausted user action was dispatched'))
    before=store.get(job)
    assert recover_due(store.settings)==[]
    assert policy(store.settings,job)['recoveries_used']==0
    assert store.get(job)['provider_calls']==before['provider_calls']


def test_auto_registration_cannot_drop_existing_user_acceptance_targets(store, canonical):
    job,_=canonical
    register_script_repair(store,job,action())
    _failed_user_action(store,job)
    with pytest.raises(OrderConflict,match='user editorial criteria'):
        register_script_repair(store,job,{
            'request_key':'machine-cannot-replace-user-feedback',
            'note':'Automatically retry the current script without user requirements.'},origin='autonomous_recovery')
    assert load_recovery_request(store,job)['request_key']==action().request_key


def test_legacy_automatic_receipt_cannot_reuse_pass_without_user_criteria(store, canonical, monkeypatch):
    from atnos.production.order_execution import StageExecutor
    job,_=canonical
    register_script_repair(store,job,action())
    _failed_user_action(store,job)
    # Recreate the historical registration through its original service,
    # never through manual checkpoint or database replacement.
    with monkeypatch.context() as old_runtime:
        old_runtime.setattr('atnos.production.studio_script_recovery.has_user_editorial_constraints',lambda *_:False)
        register_script_repair(store,job,{
            'request_key':'historical-generic-auto-assessment',
            'note':'Automatic historical review without the explicit user acceptance targets.'},origin='autonomous_recovery')
    before=store.get(job)
    with store.lease(job) as token:
        with pytest.raises(ProductionBlocked,match='omitted user editorial criteria'):
            prepare_recovery_context(StageExecutor(store,job,token))
        store.block(job,token,'scripting',category='execution_failure',message=USER_EDITORIAL_REASSESSMENT_MESSAGE)
    assert store.get(job)['provider_calls']==before['provider_calls']
    status=recovery_snapshot(store,job)
    assert status['kind']=='script' and status['can_repair_script'] is False
    assert status['issues']==[]  # No old reviewer allegations about another draft.
    assert 'jawna ocena' in status['repair_blocker']


def test_separate_reviewer_sees_current_user_requirements_as_targets(store, canonical, monkeypatch):
    from atnos.production.order_execution import StageExecutor
    from atnos.production.steps import order_script
    job, source = canonical
    register_script_repair(store,job,action())
    seen = []
    async def reviewer(settings,context,prompt,schema,**kwargs):
        seen.append(prompt)
        return {'review_format':'registered_named_v1','passed':True,'issues':[],
            'editorial_checks':{'criterion_0_segment_s06':{
            'satisfied':True,'field':'text','quote':source['segments'][5]['text'],
            'reason':'Fixture current CTA meets the requested criterion.'}}}
    # Contract test: mock above GPU preparation and provider dispatch.
    monkeypatch.setattr(order_script,'ask_model',reviewer)
    monkeypatch.setattr(order_script,'script_issues',lambda *_a,**_k: [])
    with store.lease(job) as token:
        executor = StageExecutor(store,job,token)
        prepare_recovery_context(executor)
        apply_recovery_guidance(executor.context)
        executor.context.artifacts['script_calculations'] = next(
            r['output'] for r in store.get(job)['checkpoints'] if r['stage']=='script_calculations')
        asyncio.run(order_script.review(store.settings,executor.context,
            order_script.EditorialScript.model_validate({k:v for k,v in source.items() if k != 'qa'})))
    assert len(seen) == 1
    assert 'user_editorial_requirements' in seen[0] and 'Restore the channel-follow CTA' in seen[0]
    assert 'Acceptance criteria, not observed defects' in seen[0]
