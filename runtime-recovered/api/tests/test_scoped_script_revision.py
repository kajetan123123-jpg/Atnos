"""Controlled responses verify revision plumbing; these are not production scripts."""
import asyncio
import copy
import json

import pytest
from pydantic import ValidationError

from atnos.production.order_execution import StageExecutor, produce_script
from atnos.production.order_store import OrderConflict, ProductionBlocked, file_hash
from atnos.production.project_profile import ProductionOrder, content_hash
from atnos.production.scoped_script_revision import ScriptFeedback, feedback_issues, load_feedback, register_feedback
from atnos.production.steps.common import write_json
from atnos.production.steps.order_script import EditorialScript, draft_prompt, script_issues
from test_production_order_store import store
from test_production_order_script import script


@pytest.fixture
def four_thoughts():
    rows = [
        ('hook', 'A raise can change your income while leaving your savings unchanged. Follow the extra money and you can see where the difference goes.'),
        ('practical', 'Check whether a new monthly commitment will use that extra income. Write down the payment before adding it, and compare it with your available money.'),
        ('practical', 'Choose an affordable amount for a separate savings goal before committing the rest to another expense. Keep enough available for the bills you already have.'),
        ('cta', 'That amount remains available for your goal instead of another payment. On your next payday, make that allocation before deciding what else to spend.'),
    ]
    return EditorialScript.model_validate({'title': 'Where the extra income goes',
        'description': 'A qualitative example of income allocation and recurring expenses.', 'claims': [],
        'segments': [{'id': f's{i:02}', 'type': kind, 'text': text, 'on_screen': 'A deliberate allocation',
                      'visual_intent': 'Show the spoken allocation with the physical objects'}
                     for i, (kind, text) in enumerate(rows, 1)]})


@pytest.fixture
def feedback():
    return ScriptFeedback(editor='Controlled test operator', reason='Correct temporal logic and avoid repetitive unsupported advice.',
        issues=[{'code': 'temporal_logic', 'segment_ids': ['s03'],
                 'detail': 'The decision occurs before spending the raise, not before accepting it.'}])


@pytest.fixture
def original_order(store, script, monkeypatch):
    monkeypatch.setattr('atnos.integrations.claim_evidence_gate.ensure_media_claim_gate', lambda **_: {'blocked': False})
    job = store.create(ProductionOrder(topic='Where a pay raise goes', delivery_mode='local_only'))['id']
    payload = {**script.model_dump(mode='json'), 'text': ' '.join(s.text for s in script.segments),
               'word_count': sum(len(s.text.split()) for s in script.segments),
               'qa': {'status': 'passed', 'revision': 0, 'script_hash': content_hash(script.model_dump(mode='json'))}}
    with store.lease(job) as token:
        for stage, value in [('script', payload), ('asset_ranking', {'method': 'controlled_original', 'candidates': {}}),
                             ('asset_planning', {'scenes': [], 'selection_method': 'controlled_original'})]:
            store.begin(job, token, stage, {'old': stage})
            path = write_json(store.context(job).artifact_dir / (stage + '.json'), value).artifact_path
            store.complete(job, token, stage, value, [path])
        store.begin(job, token, 'voice/s01', {'text': script.segments[0].text}, provider_call=True, estimated_cost_usd=.03)
        store.fail(job, token, 'voice/s01', category='provider_failure', message='No audio was returned')
    return job, payload


def test_feedback_rejects_manual_script_and_requires_authorization(store, original_order, feedback):
    job, _ = original_order
    with pytest.raises(ValueError, match='Explicit'):
        register_feedback(store.settings, job, feedback)
    with pytest.raises(ValidationError):
        ScriptFeedback.model_validate({**feedback.model_dump(mode='json'), 'segments': [{'text': 'Manual production text'}]})
    before = store.get(job)
    result = register_feedback(store.settings, job, feedback, approved=True)
    after = store.get(job)
    assert result['mode'] == 'revise_existing'
    assert after['provider_calls'] == before['provider_calls']
    assert after['reserved_cost_units'] == before['reserved_cost_units']
    assert [(r['stage'], r['attempts']) for r in after['checkpoints']] == [(r['stage'], r['attempts']) for r in before['checkpoints']]
    assert register_feedback(store.settings, job, feedback, approved=True)['status'] == 'already_registered'
    changed = feedback.model_copy(update={'reason': 'Another changed editorial reason cannot silently replace the request.'})
    with pytest.raises(OrderConflict, match='already exists'):
        register_feedback(store.settings, job, changed, approved=True)

def _completed_guided_draft(store,script,feedback):
    job=store.create(ProductionOrder(topic='Guided draft correction',delivery_mode='local_only'))['id']
    register_feedback(store.settings,job,feedback,approved=True)
    source=script.model_dump(mode='json');digest=content_hash(source)
    review={'status':'passed','issues':[],'revision':0,'script_hash':digest,'human_review':'not_performed'}
    payload={**source,'text':' '.join(s.text for s in script.segments),'qa':review}
    root=store.context(job).artifact_dir
    with store.lease(job) as token:
        for stage,value,path in [('script_review/0',review,root/'script'/'review_0.json'),('script',payload,root/'script.json')]:
            store.begin(job,token,stage,{'fixture':stage});path.parent.mkdir(parents=True,exist_ok=True)
            write_json(path,value);store.complete(job,token,stage,value,[path])
        store.begin(job,token,'voice/s01',{'fixture':'failed process'},local_model_call=True)
        store.fail(job,token,'voice/s01',category='dependency_failure',message='Fixture process exited before audio')
    return job,root,review

def test_followup_of_completed_guidance_uses_real_ordinary_review_without_fake_accepted_row(store,script,feedback):
    job,root,_=_completed_guided_draft(store,script,feedback)
    before=store.get(job);canonical=file_hash(root/'script.json')
    changed=feedback.model_copy(update={'reason':'Actual guided canonical has a new observed problem requiring correction.'})
    result=register_feedback(store.settings,job,changed,approved=True,followup=True)
    after=store.get(job)
    assert result['mode']=='revise_existing'
    assert file_hash(root/'script.json')==canonical
    assert after['provider_calls']==before['provider_calls']
    assert [(r['stage'],r['attempts']) for r in after['checkpoints']]==[(r['stage'],r['attempts']) for r in before['checkpoints']]
    assert not any(r['stage'].endswith('/accepted') for r in after['checkpoints'])

def test_guidance_followup_rejects_unhashed_changed_review_instead_of_accepting_a_label(store,script,feedback):
    job,root,review=_completed_guided_draft(store,script,feedback)
    write_json(root/'script'/'review_0.json',{**review,'script_hash':'stale'})
    changed=feedback.model_copy(update={'reason':'Actual guided canonical has a new observed problem requiring correction.'})
    with pytest.raises(OrderConflict,match='intact canonical/review binding'):
        register_feedback(store.settings,job,changed,approved=True,followup=True)


def test_original_archive_is_required_before_any_model_call(store, original_order, feedback, monkeypatch):
    job, _ = original_order
    register_feedback(store.settings, job, feedback, approved=True)
    (store.context(job).artifact_dir / 'script_feedback/original/files/script.json').write_text('tampered')
    before = store.get(job)['provider_calls']
    with store.lease(job) as token, pytest.raises(ProductionBlocked, match='archive'):
        asyncio.run(produce_script(StageExecutor(store, job, token)))
    assert store.get(job)['provider_calls'] == before


def test_revision_uses_atnos_calls_preserves_original_attempts_and_replans_assets(store, original_order, feedback, four_thoughts, monkeypatch):
    from atnos.production.steps import order_assets
    from test_production_order_assets import plan_for
    job, original = original_order
    register_feedback(store.settings, job, feedback, approved=True)
    before = store.get(job)
    calls = []
    async def write(*args, **kwargs):
        calls.append('writer')
        return four_thoughts.model_dump(mode='json')
    async def review(*args):
        calls.append('reviewer')
        return {'status': 'passed', 'issues': [], 'semantic_review': 'controlled_atnos_reviewer'}
    monkeypatch.setattr('atnos.production.steps.order_script.ask_model', write)
    monkeypatch.setattr('atnos.production.steps.order_script.review', review)
    with store.lease(job) as token:
        executor = StageExecutor(store, job, token)
        result = asyncio.run(produce_script(executor))
        assert len(result['segments']) == 4 and result['qa']['status'] == 'passed'
        assert script_issues(four_thoughts, executor.context.artifacts['project_profile']) == []
        after = store.get(job)
        assert after['provider_calls'] == before['provider_calls'] + 2
        assert after['reserved_cost_units'] == before['reserved_cost_units']
        assert after['request'] == before['request'] and after['profile_hash'] == before['profile_hash']
        assert next(r for r in after['checkpoints'] if r['stage'] == 'script')['attempts'] == 2
        assert next(r for r in after['checkpoints'] if r['stage'] == 'voice/s01')['attempts'] == 1
        assert load_feedback(store, job)['original_script'] == original
        again = asyncio.run(produce_script(StageExecutor(store, job, token)))
        assert again == result and calls == ['writer', 'reviewer']
        library = {'assets': [], 'rejected': [], 'manifests': []}
        monkeypatch.setattr(order_assets, 'library_manifest', lambda *_: library)
        monkeypatch.setattr(order_assets, 'rank_library', lambda *a: {'method': 'controlled_rank', 'candidates': {s.id: [] for s in a[2].segments}})
        monkeypatch.setattr('atnos.production.order_comfy_routing.routing_snapshot_for_order', lambda *_: {'channel_id': 'wealth_blueprint'})
        async def plan(settings, context, actual_script, _library, ranking):
            assert len(actual_script.segments) == 4
            return order_assets.write_scene_plan(context, actual_script, plan_for(actual_script), ranking)
        monkeypatch.setattr(order_assets, 'plan_scenes', plan)
        async def observe(_executor, actual_library):
            return actual_library
        monkeypatch.setattr('atnos.production.asset_observation.observe_library', observe)
        actual = asyncio.run(order_assets.produce_assets(executor))
        assert [s['segment_id'] for s in actual['scenes']] == [s.id for s in four_thoughts.segments]
        current = store.get(job)
        assert next(r for r in current['checkpoints'] if r['stage'] == 'asset_planning')['attempts'] == 2
        archive = json.loads((store.context(job).artifact_dir / 'script_feedback/original/checkpoints.json').read_text())
        assert next(r for r in archive['checkpoints'] if r['stage'] == 'asset_planning')['attempts'] == 1


def test_identical_rewrite_stops_instead_of_repeating_or_resetting(store, original_order, feedback, script, monkeypatch):
    job, original = original_order
    register_feedback(store.settings, job, feedback, approved=True)
    calls = []
    async def unchanged(*a, **k):
        calls.append('write')
        return script.model_dump(mode='json')
    async def accept(*a):
        return {'status': 'passed', 'issues': [], 'semantic_review': 'controlled_reviewer'}
    monkeypatch.setattr('atnos.production.steps.order_script.ask_model', unchanged)
    monkeypatch.setattr('atnos.production.steps.order_script.review', accept)
    with store.lease(job) as token, pytest.raises(ProductionBlocked, match='revision rejected'):
        asyncio.run(produce_script(StageExecutor(store, job, token)))
    assert calls == ['write']
    assert next(r for r in store.get(job)['checkpoints'] if r['stage'] == 'script')['output'] == original


def test_four_segments_preserve_quality_and_dynamic_exact_scene_slots(store, four_thoughts):
    from atnos.production.scene_contract import plan_response_for
    from atnos.production.steps.order_assets import ScenePlan, validate_plan
    from test_production_order_assets import plan_for
    profile = store.create(ProductionOrder(topic='A complete allocation story', delivery_mode='local_only'))['profile']
    assert 90 <= sum(len(s.text.split()) for s in four_thoughts.segments) <= 125
    assert script_issues(four_thoughts, profile) == []
    plan = plan_for(four_thoughts)
    validate_plan(plan, four_thoughts, {'assets': []})
    schema = plan_response_for(four_thoughts, {'assets': []})
    slots = {scene.segment_id: scene.model_dump(mode='json') for scene in plan.scenes}
    schema.model_validate({'scenes': slots})
    with pytest.raises(ValidationError):
        schema.model_validate({'scenes': {**slots, 's05': copy.deepcopy(slots['s04'])}})
    with pytest.raises(ValidationError):
        ScenePlan.model_validate({'scenes': plan.model_dump(mode='json')['scenes'][:3]})
    four_thoughts.segments[1].type = four_thoughts.segments[2].type = 'explanation'
    assert 'practical_share' in {i['code'] for i in script_issues(four_thoughts, profile)}


def test_short_five_thought_revision_repairs_length_without_blind_full_rewrite(
        store, original_order, feedback, four_thoughts, monkeypatch):
    job,_=original_order
    feedback=feedback.model_copy(update={'target_segments':(4,6),'max_drafts':2})
    register_feedback(store.settings,job,feedback,approved=True)
    short=EditorialScript.model_validate({'title':'Controlled short revision',
        'description':'Controlled workflow fixture, not production content.','claims':[],
        'segments':[{'id':f's{i:02d}','type':kind,'text':text,
            'visual_intent':'Show the actual supported qualitative scene','on_screen':'A concrete check'}
            for i,(kind,text) in enumerate([
                ('hook','Your account shows a balance, but can you actually use that money right now?'),
                ('practical','Check your actual account terms before relying on this money.'),
                ('practical','Name the purpose of your reserve and the future goal separately.'),
                ('practical','Review both purposes when your essential obligations change.'),
                ('cta','Follow Wealth Blueprint. Build wealth on purpose.')],1)]})
    fixed=short.model_copy(deep=True)
    for i,source in enumerate(four_thoughts.segments[:3],1):fixed.segments[i].text=source.text
    assert 90<=sum(len(s.text.split()) for s in fixed.segments)<=125
    calls=[]
    async def draft(*args,**kwargs):
        calls.append('full-draft')
        return short.model_dump(mode='json')
    async def repair(settings,context,previous,issues):
        calls.append('repair-five-thoughts')
        assert len(previous.segments)==5
        assert all(item['segment_ids']==['s02','s03','s04'] for item in issues)
        return fixed
    async def review(settings,context,candidate):
        issues=script_issues(candidate,context.artifacts['project_profile'])
        return {'status':'failed' if issues else 'passed','issues':issues,'semantic_review':'controlled_fixture'}
    monkeypatch.setattr('atnos.production.steps.order_script.ask_model',draft)
    monkeypatch.setattr('atnos.production.steps.order_script.repair',repair)
    monkeypatch.setattr('atnos.production.steps.order_script.review',review)
    monkeypatch.setattr('atnos.integrations.claim_evidence_gate.production_blocker_from_ledger',lambda _: {'blocked':False})
    with store.lease(job) as token:
        result=asyncio.run(produce_script(StageExecutor(store,job,token)))
    assert calls==['full-draft','repair-five-thoughts']
    assert len(result['segments'])==5


def test_guidance_before_first_draft_has_no_canned_text_or_model_cost(store, feedback):
    job = store.create(ProductionOrder(topic='Income and expenses', delivery_mode='local_only'))['id']
    feedback.issues[0].segment_ids = []
    before = store.get(job)
    assert register_feedback(store.settings, job, feedback, approved=True)['mode'] == 'guide_first_draft'
    from atnos.production.scoped_script_revision import produce_requested
    with store.lease(job) as token:
        executor = StageExecutor(store, job, token)
        assert asyncio.run(produce_requested(executor)) is None
        prompt = draft_prompt(executor.context, {})
        assert 'between 4 and 5' in prompt and 'before accepting it' in prompt
        assert 'Write 6 segments' not in prompt
    after = store.get(job)
    assert after['provider_calls'] == before['provider_calls']
    assert after['profile_hash'] == before['profile_hash']


def test_actual_audio_bars_revision_but_an_unused_image_candidate_does_not(store, original_order, feedback):
    job, _ = original_order
    with store.lease(job) as token:
        store.begin(job, token, 'assets/style_preview/s01', {'subject': 'income'}, local_model_call=True)
        path = write_json(store.context(job).artifact_dir / 'candidate.json', {'approved': False}).artifact_path
        store.complete(job, token, 'assets/style_preview/s01', {'approved': False}, [path])
    assert register_feedback(store.settings, job, feedback, approved=True)['status'] == 'registered'
    another = store.create(ProductionOrder(topic='Already narrated', delivery_mode='local_only'))['id']
    with store.lease(another) as token:
        store.begin(another, token, 'voice/s01', {'text': 'Recorded speech'})
        path = store.context(another).artifact_dir / 'voice.wav'
        path.write_bytes(b'controlled evidence, not production audio')
        store.complete(another, token, 'voice/s01', {'audio_path': str(path)}, [path])
    with pytest.raises(OrderConflict, match='actual audio'):
        register_feedback(store.settings, another, feedback, approved=True)


def test_known_temporal_and_prevalence_errors_cannot_pass_only_by_reviewer_label(four_thoughts, feedback):
    four_thoughts.segments[1].text = 'This is common and often unnoticed.'
    four_thoughts.segments[2].text = 'Before accepting the raise, set money aside. This ensures your savings grow.'
    codes = {issue['code'] for issue in feedback_issues(four_thoughts, feedback.model_dump(mode='json'))}
    assert {'temporal_logic', 'unsupported_prevalence', 'guaranteed_growth'}.issubset(codes)


def test_short_six_beat_guidance_develops_middle_without_stretching_hook_or_changing_gate(four_thoughts, feedback):
    from types import SimpleNamespace
    from atnos.production.scoped_script_revision import scoped_word_guidance
    profile={'word_budget':[90,125]}
    context=SimpleNamespace(artifacts={'project_profile':{'production':profile}})
    request=feedback.model_dump(mode='json')
    request['target_segments']=[6,6]
    result=scoped_word_guidance(context,four_thoughts,request,[{'code':'word_budget'}])
    assert sum(result['suggested_words_by_scene'])==result['target_total']==107
    assert result['suggested_words_by_scene'][0]<=14
    assert result['suggested_words_by_scene'][-1]<=10
    assert max(result['suggested_words_by_scene'])<=32
    assert result['actual_rejected_total']==sum(len(item.text.split()) for item in four_thoughts.segments)
    assert profile=={'word_budget':[90,125]}
    assert scoped_word_guidance(context,four_thoughts,request,[])=={}
    request['target_segments']=[4,5]
    assert scoped_word_guidance(context,four_thoughts,request,[{'code':'word_budget'}])=={}


def test_actual_short_hook_draft_uses_existing_middle_repair_before_canonical(store, original_order, monkeypatch):
    from pathlib import Path
    candidate=EditorialScript.model_validate(json.loads((Path(__file__).parent/'fixtures/wb_hook_candidate_73_words.json').read_text('utf-8')))
    job,original=original_order
    request=ScriptFeedback(editor='Controlled length regression',reason='Repair the observed real short candidate through existing narration allocation.',
        issues=[{'code':'word_budget','segment_ids':[],'detail':'The actual candidate has 73 words; develop the middle, preserve hook and CTA.'}],target_segments=(6,6),max_drafts=2)
    register_feedback(store.settings,job,request,approved=True)
    calls=[]
    async def author(*a,**kw):
        calls.append('writer')
        return candidate.model_dump(mode='json')
    async def assess(settings,context,actual,*a,**kw):
        calls.append('review')
        assert next(row['output'] for row in store.get(job)['checkpoints'] if row['stage']=='script')==original
        issues=script_issues(actual,context.artifacts['project_profile'])
        return {'status':'failed' if issues else 'passed','issues':issues,'semantic_review':'controlled_test_only'}
    async def allocated(settings,context,actual,issues):
        calls.append('allocated_middle')
        assert {sid for issue in issues for sid in issue['segment_ids']}=={item.id for item in candidate.segments[1:-1]}
        revised=actual.model_copy(deep=True)
        # Controlled response tests plumbing, not English quality or LIVE acceptance.
        for item in revised.segments[1:-1]:
            item.text+=' Use the result to decide what to check next.'
        assert revised.segments[0]==candidate.segments[0] and revised.segments[-1]==candidate.segments[-1]
        return revised
    monkeypatch.setattr('atnos.production.steps.order_script.ask_model',author)
    monkeypatch.setattr('atnos.production.steps.order_script.review',assess)
    monkeypatch.setattr('atnos.production.steps.order_script.repair',allocated)
    before=store.get(job)
    with store.lease(job) as token:
        result=asyncio.run(produce_script(StageExecutor(store,job,token)))
    assert calls==['writer','review','allocated_middle','review']
    assert result['segments'][0]['text']==candidate.segments[0].text
    assert result['segments'][-1]['text']==candidate.segments[-1].text
    assert store.get(job)['profile_hash']==before['profile_hash']
