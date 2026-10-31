"""Round-four defects remain rejected; the normal writer receives usable rules."""

import asyncio
import copy
from types import SimpleNamespace

import pytest

from atnos.production.order_execution import StageExecutor, produce_script
from atnos.production.order_store import ProductionBlocked
from atnos.production.project_profile import ProductionOrder
from atnos.production.scoped_script_revision import (
    _unsupported_still_illustration, feedback_issues, register_feedback,
    scoped_writing_contract,
)
from atnos.production.steps import order_script
from test_scoped_script_revision import store, original_order, feedback, four_thoughts, script
from test_production_order_script import execution


@pytest.mark.parametrize("intent", [
    "A hand-drawn envelope labeled 'Pay Raise' sits on a table.",
    "A wallet with a recurring payment label 'Monthly Subscription' is open.",
    "A small notebook labeled 'Future Goal' sits next to a savings pot.",
    "A hand-drawn calendar with a checkmark next to 'Review Plan' and handwritten lettering.",
    "A hand-drawn illustration of a labeled savings envelope next to a shopping bag.",
    "A wallet is open, showing a stack of bills being moved into it.",
    "A small stack of bills is being placed inside the savings pot.",
    "A hand is transferring coins from the wallet to the jar.",
    "A jar fills with money while the narrator speaks.",
])
def test_actual_embedded_words_and_raster_motion_are_not_supported(intent):
    assert _unsupported_still_illustration(intent)


@pytest.mark.parametrize("intent", [
    "One open envelope contains plain unprinted teal paper cards, all blank faces.",
    "A wallet with a plain unprinted receipt sits beside a closed savings jar.",
    "An adult hand rests beside the savings pot, coins already inside the slot.",
    "A hand points to a blank notebook without words or labels.",
    "A still jar with coins inside. No moving objects, labels or lettering.",
    "A static illustration of a blank wallet. Do not add labels or money moving into it.",
])
def test_literal_static_states_and_negative_instructions_remain_allowed(intent):
    assert not _unsupported_still_illustration(intent)


def test_visual_checks_are_scoped_to_the_actual_feedback_defect(four_thoughts, feedback):
    raw = feedback.model_dump(mode="json")
    four_thoughts.segments[1].visual_intent = "An envelope labeled 'Pay Raise' with bills being moved into it."
    assert not any(issue["code"] == "unsupported_visual_intent" for issue in feedback_issues(four_thoughts, raw))
    raw["issues"].append({"code": "unsupported_visual_intent", "segment_ids": ["s02"],
                          "detail": "Use still physical states; screen text belongs to the renderer."})
    issues = feedback_issues(four_thoughts, raw)
    assert any(issue["code"] == "unsupported_visual_intent" and issue["fields"] == ["visual_intent"]
               and issue["segment_ids"] == ["s02"] for issue in issues)


def test_wrong_role_types_remain_rejected_without_relabelling(execution, four_thoughts):
    original = copy.deepcopy(four_thoughts.model_dump(mode="json"))
    for segment in four_thoughts.segments[1:-1]:
        segment.type = "explanation"
    four_thoughts.segments[-1].type = "hook"
    before = four_thoughts.model_dump(mode="json")
    issues = order_script.script_issues(four_thoughts, execution[2].artifacts["project_profile"])
    assert {"structure", "practical_share"} <= {issue["code"] for issue in issues}
    assert four_thoughts.model_dump(mode="json") == before
    assert [segment["text"] for segment in before["segments"]] == [segment["text"] for segment in original["segments"]]


def test_writer_function_contract_uses_frozen_budget_and_does_not_lower_threshold(execution):
    context = execution[2]
    profile_before = copy.deepcopy(context.artifacts["project_profile"])
    contract = scoped_writing_contract(context)
    minimum = profile_before["production"]["minimum_practical_share"]
    low, high = profile_before["production"]["word_budget"]
    assert f"At least {minimum:.0%}" in contract
    assert f"{low}-{high} total spoken words" in contract
    assert "first segment type=hook; last segment type=cta" in contract
    assert "Hook and cta words do not count" in contract
    assert "do not relabel an explanation as practical" in contract
    assert "no quoted labels" in contract and "STILL physical state" in contract
    assert context.artifacts["project_profile"] == profile_before


def test_normal_scoped_writer_gets_contract_and_guard_blocks_even_permissive_reviewer(
        store, original_order, feedback, four_thoughts, monkeypatch):
    job, _ = original_order
    raw_feedback = feedback.model_dump(mode="json")
    raw_feedback["issues"].append({"code": "unsupported_visual_intent", "segment_ids": ["s02"],
                                   "detail": "Use still unprinted physical props and renderer captions."})
    raw_feedback["max_drafts"] = 1
    register_feedback(store.settings, job, raw_feedback, approved=True)
    original_checkpoint = next(row for row in store.get(job)["checkpoints"] if row["stage"] == "script")
    candidate = copy.deepcopy(four_thoughts.model_dump(mode="json"))
    candidate["segments"][1]["visual_intent"] = "A wallet labeled 'Monthly Subscription' with bills being moved into it."
    prompts = []

    async def writer(settings, context, prompt, schema, **kwargs):
        prompts.append(prompt)
        return candidate

    async def reviewer(*args):
        return {"status": "passed", "issues": [], "semantic_review": "controlled_permissive_reviewer"}

    monkeypatch.setattr(order_script, "ask_model", writer)
    monkeypatch.setattr(order_script, "review", reviewer)
    with store.lease(job) as token, pytest.raises(ProductionBlocked, match="revision rejected"):
        asyncio.run(produce_script(StageExecutor(store, job, token)))
    assert len(prompts) == 1
    assert "SEGMENT TYPE CONTRACT" in prompts[0]
    assert "last segment type=cta" in prompts[0]
    assert "do not relabel an explanation as practical" in prompts[0]
    final_state = store.get(job)
    canonical = next(row for row in final_state["checkpoints"] if row["stage"] == "script")
    assert canonical["output"] == original_checkpoint["output"]
    assert not any(row["stage"].endswith("/accepted") for row in final_state["checkpoints"])
    rejected = next(row for row in final_state["checkpoints"] if "/review/0" in row["stage"])
    assert rejected["output"]["status"] == "failed"
    assert "unsupported_visual_intent" in {issue["code"] for issue in rejected["output"]["issues"]}


def test_guide_first_draft_normal_prompt_receives_contract_without_profile_mutation(execution, feedback):
    context = execution[2]
    before = copy.deepcopy(context.artifacts['project_profile'])
    plain = order_script.draft_prompt(context, {})
    assert 'SEGMENT TYPE CONTRACT' not in plain
    context.artifacts['scoped_script_guidance'] = feedback.model_dump(mode='json')
    guided = order_script.draft_prompt(context, {})
    assert 'SEGMENT TYPE CONTRACT' in guided
    assert 'first segment type=hook; last segment type=cta' in guided
    assert 'Hook and cta words do not count' in guided
    assert 'no quoted labels' in guided
    assert context.artifacts['project_profile'] == before


def _lengthened_candidate(script, total=127):
    changed = copy.deepcopy(script.model_dump(mode='json'))
    base, extra = divmod(total, len(changed['segments']))
    for index, segment in enumerate(changed['segments']):
        target = base + int(index < extra)
        words = segment['text'].rstrip('.!?').split()
        words = words[:target] + ['carefully'] * max(0, target - len(words))
        segment['text'] = ' '.join(words) + '.'
    return changed


def test_safe_127_word_general_repair_returns_to_review_instead_of_same_stage_retry(
        execution, four_thoughts, feedback, monkeypatch):
    store, _, context = execution
    context.artifacts['scoped_script_guidance'] = feedback.model_dump(mode='json')
    candidate = _lengthened_candidate(four_thoughts)
    issues = [{'code': 'word_budget', 'segment_ids': [s.id for s in four_thoughts.segments],
               'detail': 'Previous draft too short'},
              {'code': 'structure', 'segment_ids': [four_thoughts.segments[-1].id],
               'detail': 'One practical action last'}]
    prompts = []

    async def writer(settings, context, prompt, schema, **kwargs):
        prompts.append(prompt)
        return {'replacements': candidate['segments']}

    monkeypatch.setattr(order_script, 'try_deterministic_repair', lambda *args: (None, {}))
    monkeypatch.setattr(order_script, 'ask_model', writer)
    fixed = asyncio.run(order_script.repair(store.settings, context, four_thoughts, issues))
    assert len(prompts) == 1
    assert 'SEGMENT TYPE CONTRACT' in prompts[0]
    assert sum(len(s.text.split()) for s in fixed.segments) == 127
    assert 'word_budget' in {item['code'] for item in order_script.script_issues(fixed, context.artifacts['project_profile'])}
    trace_files = list((context.artifact_dir / 'script').glob('repair_trace_*.json'))
    import json
    trace = json.loads(trace_files[-1].read_text())
    assert trace['final_status'] == 'needs_review'
    assert 'word_budget' in {item['code'] for item in trace['pending_editorial_issues']}
    assert __import__('pathlib').Path(trace['response_artifact']).is_file()
    assert not hasattr(fixed, 'qa')  # A repaired draft is not acceptance.


def test_deferred_editorial_review_never_bypasses_financial_safety(execution, script):
    context = execution[2]
    context.artifacts['script_calculations'] = {'claims': {}}
    script.segments[0].text = 'Can you save $20 each week while keeping your essential bills covered?'
    issues = order_script.script_issues(script, context.artifacts['project_profile'])
    replacement = script.segments[0].model_dump()
    replacement['text'] = 'Can you save twenty dollars each week while keeping your essential bills covered?'
    with pytest.raises(ValueError, match='financial safety errors.*spoken_amount_mismatch'):
        order_script.validate_repair_response(context, script, issues, {'replacements': [replacement]},
                                             defer_editorial_validation=True)


def test_normal_bounded_pipeline_reviews_oversized_safe_draft_before_next_repair(
        execution, four_thoughts, feedback, monkeypatch):
    store, _, _ = execution
    job = store.create(ProductionOrder(topic='Where a pay raise goes', delivery_mode='local_only'))['id']
    feedback.issues[0].segment_ids = []
    register_feedback(store.settings, job, feedback, approved=True)
    oversized = _lengthened_candidate(four_thoughts)
    original = four_thoughts.model_dump(mode='json')
    writer_calls = []
    reviewed_counts = []

    async def model(settings, context, prompt, schema, **kwargs):
        writer_calls.append(schema.__name__)
        if schema.__name__ == 'CalculationPlan':
            return {'rationale': 'No numerical example in this behavioral trial.', 'examples': []}
        if schema.__name__ in {'GuidedWBScript', 'AdaptedScript', 'EditorialScript'}:
            return original if schema.__name__ == 'EditorialScript' else {key: value for key, value in original.items() if key != 'claims'}
        if schema.__name__ in {'AdaptedScriptRepair', 'ScriptRepair'}:
            # First actual repair remains too long; the next reviewer must see
            # it before a differently scoped second repair can run.
            result = oversized if len(reviewed_counts) == 1 else original
            return {'replacements': result['segments']}
        if schema.__name__ == 'EditorialFieldRepair':
            result = oversized if len(reviewed_counts) == 1 else original
            return {'patches': [{'id': s['id'], 'text': s['text'], 'on_screen': s['on_screen']}
                                for s in result['segments']]}
        raise AssertionError(schema.__name__)

    async def review(settings, context, candidate):
        count = sum(len(segment.text.split()) for segment in candidate.segments)
        reviewed_counts.append(count)
        if len(reviewed_counts) == 1:
            return {'status': 'failed', 'issues': [{'code': 'naturalness', 'segment_ids': [s.id for s in candidate.segments],
                'detail': 'Make the wording more conversational'}], 'semantic_review': 'controlled_first_review'}
        if count > context.artifacts['project_profile']['production']['word_budget'][1]:
            return {'status': 'failed', 'issues': [
                {'code': 'word_budget', 'segment_ids': [s.id for s in candidate.segments], 'detail': f'{count} spoken words above the frozen maximum'},
                {'code': 'naturalness', 'segment_ids': [s.id for s in candidate.segments], 'detail': 'Replace repetitive wording while preserving the action'},
            ], 'semantic_review': 'controlled_oversized_review'}
        return {'status': 'passed', 'issues': [], 'semantic_review': 'controlled_final_review'}

    monkeypatch.setattr(order_script, 'ask_model', model)
    monkeypatch.setattr(order_script, 'review', review)
    monkeypatch.setattr(order_script, 'try_deterministic_repair', lambda *args: (None, {}))
    with store.lease(job) as token:
        result = asyncio.run(produce_script(StageExecutor(store, job, token)))
    assert reviewed_counts == [sum(len(s.text.split()) for s in four_thoughts.segments), 127,
                               sum(len(s.text.split()) for s in four_thoughts.segments)]
    rows = {row['stage']: row for row in store.get(job)['checkpoints']}
    assert rows['script_repair/1']['attempts'] == rows['script_repair/2']['attempts'] == 1
    assert rows['script_review/1']['output']['status'] == 'failed'
    assert rows['script_review/1']['output']['issues'][0]['detail'].startswith('127 spoken words')
    assert rows['script_review/2']['output']['status'] == 'passed'
    assert result['word_count'] < 127 and result['qa']['status'] == 'passed'
    assert sum(writer_calls.count(name) for name in ('AdaptedScriptRepair', 'ScriptRepair', 'EditorialFieldRepair')) == 2
