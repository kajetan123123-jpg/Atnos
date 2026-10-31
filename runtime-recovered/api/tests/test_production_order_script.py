from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from atnos.production import order_execution
from atnos.production.job_recovery import ProductionJobRecoveryService
from atnos.production.job_resume import _load_video_resume_context
from atnos.production.order_execution import StageExecutor, produce_script
from atnos.production.order_store import ProductionBlocked, ProductionOrderStore
from atnos.production.steps import order_script as script_module
from atnos.production.steps.order_script import (
    EditorialScript, NumericClaim, ScriptReview, apply_segment_repair, calculate_claim, script_issues,
)
from test_production_order_store import create, store


@pytest.fixture
def script():
    texts = [
        ("hook", "Your next payday can disappear again, or it can finally give your savings a clear direction."),
        ("practical", "Open your banking app and list the essential bills due before your next payday arrives."),
        ("practical", "Choose an amount you can spare, then schedule a transfer into a separate savings account."),
        ("practical", "Cancel an unused subscription and redirect that payment into savings instead of spending the difference."),
        ("explanation", "A separate account makes progress visible without mixing your savings with money you need for bills."),
        ("cta", "Before you close your banking app today, schedule that transfer and check that your essentials are covered."),
    ]
    return EditorialScript.model_validate({"title": "Give your next payday a job", "description": "A practical guide to making a sustainable savings habit.",
        "segments": [{"id": f"s{i + 1:02}", "type": kind, "text": text, "visual_intent": "Show a person reviewing bills at a desk", "on_screen": "Make the next step concrete"}
                     for i, (kind, text) in enumerate(texts)], "claims": []})


@pytest.fixture
def execution(store, monkeypatch):
    job = create(store)["id"]
    context = store.context(job)
    path = store.settings.data_dir / context.artifacts["project_profile"]["production"]["reference_profile"]
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"profile_key": "wealth_blueprint", "sample_count": 40,
                                "generation_rules": ["Use direct, concrete instructions"]}), encoding="utf-8")
    monkeypatch.setattr(script_module, "read_channel_style_guide", lambda *_: SimpleNamespace(source="test", content="Natural English"))
    monkeypatch.setattr(script_module, "KnowledgeLibraryService", lambda *_: SimpleNamespace(lookup=lambda *a, **k: []))
    return store, job, context


def claim(operation="product", operands=None, result=10000, unit="dollars"):
    return NumericClaim(id="c1", operation=operation, operands=operands or [100, 100], result=result, unit=unit,
                        spoken_result="ten thousand", assumptions="Saving one hundred for one hundred weeks with no withdrawals.")


def test_script_repair_stops_a_b_a_cycle_without_finalizing(execution, script, monkeypatch):
    store, job, _ = execution
    original = script.model_copy(deep=True)
    alternate = script.model_copy(deep=True)
    alternate.segments[1].text += " Check the amount carefully."
    calls = []

    async def draft(settings, context, evidence):
        from atnos.production.steps.common import write_json
        return write_json(context.artifact_dir / 'script_draft.json', original.model_dump(mode='json'))

    async def reviewer(*args):
        return {'status': 'failed', 'issues': [{'code': 'naturalness',
            'segment_ids': ['s02'], 'detail': 'Controlled rejected wording.'}]}

    async def repair(*args):
        calls.append(1)
        return (alternate if len(calls) == 1 else original).model_copy(deep=True)

    async def planner(settings, context, prompt, schema, **kwargs):
        assert schema.__name__ == 'CalculationPlan'
        return {'rationale': 'Controlled qualitative fixture; no numerical claims.', 'examples': []}

    monkeypatch.setattr(script_module, 'ask_model', planner)
    monkeypatch.setattr(script_module, 'run', draft)
    monkeypatch.setattr(script_module, 'review', reviewer)
    monkeypatch.setattr(script_module, 'repair', repair)
    with store.lease(job) as token, pytest.raises(ProductionBlocked, match='script_repair cycle'):
        asyncio.run(produce_script(StageExecutor(store, job, token)))
    state = store.get(job)
    assert len(calls) == 2
    assert 'script_repair_cycle' in state['error']
    assert not any(row['stage'] == 'script' for row in state['checkpoints'])
    assert not (store.context(job).artifact_dir / 'script.json').exists()


@pytest.mark.parametrize("operation,operands,result", [("sum", [2, 3], 5), ("product", [100, 100], 10000),
    ("compound", [10000, .07, 2], 11449), ("ratio", [5, 2], 2.5), ("percent", [10000, 7], 700)])
def test_bounded_arithmetic(operation, operands, result):
    assert calculate_claim(claim(operation, operands, result)) == pytest.approx(result)


@pytest.mark.parametrize("operation,operands", [("ratio", [10, 0]), ("compound", [100, 7, 10]), ("compound", [100, .07, 1000]), ("sum", [1e11, 1])])
def test_invalid_arithmetic_rejected(operation, operands):
    with pytest.raises(ValueError):
        calculate_claim(claim(operation, operands))


def test_valid_script_and_strict_rate_tolerance(execution, script):
    _, _, context = execution
    profile = context.artifacts["project_profile"]
    assert script_issues(script, profile) == []
    script.claims = [claim("sum", [.1, .1], .7, unit="percent")]
    script.segments[0].claim_ids = ["c1"]
    assert "arithmetic" in {i["code"] for i in script_issues(script, profile)}


@pytest.mark.parametrize('malformed', [
    'a few.days.before.it.to.ensure.you.have.time.to.decide.',
    'a few daysbefore,allowingtimeforinformeddecisionmaking.',
    'confirm theactionimmediatelytopreventautomaticrenewalandavoidunexpectedcosts.',
])
def test_live_joined_word_budget_evasion_is_rejected(execution, script, malformed):
    script.segments[1].text = 'Set a reminder ' + malformed
    findings = script_issues(script, execution[2].artifacts['project_profile'])
    assert any(issue['code'] == 'naturalness' and issue['segment_ids'] == ['s02']
               and 'word boundaries' in issue['detail'] for issue in findings)


def test_normal_english_punctuation_is_not_joined_word_evasion(execution, script):
    script.segments[1].text = 'Check the auto-renewal terms. Then set a reminder before the cutoff.'
    findings = script_issues(script, execution[2].artifacts['project_profile'])
    assert not any('Malformed joined narration' in issue['detail'] for issue in findings)


def test_practical_ratio_and_tts_numbers_rejected(execution, script):
    script.segments[1].type = script.segments[2].type = script.segments[3].type = "explanation"
    script.segments[1].text = "Save 100 dollars."
    codes = {i["code"] for i in script_issues(script, execution[2].artifacts["project_profile"])}
    assert {"practical_share", "tts_numbers", "unverified_number"}.issubset(codes)


def test_deterministic_repair_never_pads_or_relabels_practical_share(execution, script):
    context = execution[2]
    context.artifacts["production_order"]["intake"] = {"kind": "user_hypothetical"}
    for segment in script.segments[1:-1]:
        segment.type = "explanation"
    original = script.model_dump(mode="json")
    issues = [{"code": "practical_share", "segment_ids": [s.id for s in script.segments[1:-1]],
               "detail": "Explain a real viewer action instead of relabeling explanations."}]
    fixed, trace = script_module.try_deterministic_repair(script, issues, context)
    assert fixed is None and trace["requires_model_rewrite"]
    assert script.model_dump(mode="json") == original


def test_reviewer_cannot_repeat_resolved_average_defect_but_keeps_other_findings(execution, script, monkeypatch):
    from test_hypothetical_script_intake import order
    from atnos.production.hypothetical_script_intake import compile_supplied_plan
    context = execution[2]
    context.artifacts['production_order'] = order().model_dump(mode='json')
    receipt = compile_supplied_plan(context.artifacts['production_order']['intake'])
    context.artifacts['script_calculations'] = receipt
    script.claims = [NumericClaim.model_validate(item) for item in receipt['claims'].values()]
    script.segments[4].text = 'If ten dollars earned an assumed eight percent annually for thirty years, this hypothetical example reaches about one hundred one dollars.'
    script.segments[4].claim_ids = ['c1']
    captured = []
    async def stale_reviewer(settings, context, prompt, schema, **kwargs):
        captured.append(prompt)
        return {'passed': False, 'issues': [
            {'code': 'hypothetical_assumption_wording', 'segment_ids': ['s05'], 'detail': 'This accurate assumed rate could be improved.'},
            {'code': 'naturalness', 'segment_ids': ['s01'], 'detail': 'A separate current grammar defect needs a model rewrite.'}]}
    monkeypatch.setattr(script_module, 'ask_model', stale_reviewer)
    result = asyncio.run(script_module.review(execution[0].settings, context, script))
    assert result['status'] == 'failed'
    assert 'naturalness' in {item['code'] for item in result['issues']}
    assert 'hypothetical_assumption_wording' not in {item['code'] for item in result['issues']}
    assert '"original_scenario"' not in captured[0]


def test_active_studio_repair_bundle_is_reviewed_and_unresolved_issue_is_returned(execution, script, monkeypatch):
    context = execution[2]
    context.artifacts['studio_script_recovery'] = {
        'mode': 'hypothetical_review',
        'request_key': 'repair-request-123',
        'note': 'Keep the coffee image reassuring and clearly uncrossed.',
        'issues': [{
            'code': 'unsupported_visual_intent',
            'segment_ids': ['s04'],
            'fields': ['visual_intent'],
            'detail': 'Show an ordinary upright coffee cup, uncrossed, with no X or hand in the raster.',
        }],
    }
    captured = []

    async def unresolved_reviewer(settings, context, prompt, schema, **kwargs):
        captured.append(prompt)
        return {'passed': False, 'issues': [{
            'code': 'unsupported_visual_intent',
            'segment_ids': ['s04'],
            'fields': ['visual_intent'],
            'detail': 'The current visual intent still asks for a hand moving the red X away.',
        }]}

    monkeypatch.setattr(script_module, 'ask_model', unresolved_reviewer)
    result = asyncio.run(script_module.review(execution[0].settings, context, script))

    assert 'ACTIVE HYPOTHETICAL STUDIO REVIEW BUNDLE' in captured[0]
    assert 'ordinary upright coffee cup, uncrossed' in captured[0]
    assert 'check each registered acceptance target against the current script' in captured[0]
    assert 'do not demand a hand or object motion' in captured[0]
    assert result['status'] == 'failed'
    unresolved = [item for item in result['issues'] if item['code'] == 'unsupported_visual_intent']
    assert unresolved and unresolved[0]['segment_ids'] == ['s04']
    assert 'moving the red X away' in unresolved[0]['detail']


def test_compact_studio_bundle_keeps_current_source_and_excludes_old_assertions(execution, script):
    context = execution[2]
    context.artifacts['studio_script_recovery'] = {
        'mode': 'hypothetical_review', 'note': 'Assess current coffee reassurance.',
        'issues': [{'code': 'naturalness', 'segment_ids': ['s04'],
                    'detail': 'Use complete natural reassurance.'}],
    }
    context.artifacts['project_profile']['feedback_rules'] = [{
        'feedback': {'issues': [{'detail': 'OBSOLETE_COFFER_VACATION_ASSERTION'}]}}]
    bundle = script_module._active_studio_script_bundle(context, script, [{
        'code': 'naturalness', 'segment_ids': ['s04'],
        'detail': 'OBSOLETE_COFFER_VACATION_ASSERTION'}])
    assert bundle['current_script'] == script.model_dump(mode='json')
    assert bundle['frozen_writing_rules_and_budget']['word_budget'] == context.artifacts['project_profile']['production']['word_budget']
    assert 'OBSOLETE_COFFER_VACATION_ASSERTION' not in json.dumps(bundle)
    assert 'Supported' in bundle['render_contract']['still_image_reveal_hold']
    schema = script_module.repair_generation_schema(script_module.AdaptedScriptRepair, set())
    assert schema['$defs']['ScriptSegment']['properties']['on_screen']['minLength'] == 1
    assert 'title' not in schema['properties']


def test_plain_time_reference_does_not_require_financial_claim_but_year_two_is_rejected(execution, script):
    script.segments[1].text = "In one year, your habit feels normal because the transfer happens before spending starts."
    assert not {"unverified_number", "spoken_amount_mismatch"} & {i["code"] for i in script_issues(script, execution[2].artifacts["project_profile"])}
    script.segments[1].text = "In year two, raise the transfer only after rent, bills, and food are safely covered."
    codes = {i["code"] for i in script_issues(script, execution[2].artifacts["project_profile"])}
    assert "unnatural_year_phrase" in codes


def test_word_repair_budget_reserves_unchanged_text(script):
    allowed = {"s02", "s03"}
    budget = script_module.repair_word_budget(script, allowed, [90, 125])
    unchanged = sum(len(s.text.split()) for s in script.segments if s.id not in allowed)
    assert budget["unchanged_words"] == unchanged
    assert set(budget["target_words_by_segment"]) == allowed
    assert sum(budget["target_words_by_segment"].values()) + unchanged == 107


def test_word_repair_targets_fit_allocated_schema_for_six_scene_budget(script):
    """prod-a68d15d5b6b4: 85 words previously produced invalid 107-word targets."""
    from atnos.production.narration_schema import allocated_narration_schema

    counts = [12, 18, 16, 16, 12, 11]
    for segment, count in zip(script.segments, counts):
        segment.text = " ".join(["word"] * (count - 1) + ["word."])
    assert sum(len(segment.text.split()) for segment in script.segments) == 85

    budget = script_module.repair_word_budget(
        script, {segment.id for segment in script.segments}, [90, 125], per_segment_limit=32
    )
    targets = list(budget["target_words_by_segment"].values())
    assert targets == [15, 21, 21, 20, 15, 15]
    assert sum(targets) == 107
    schema = allocated_narration_schema(budget, per_segment_limit=32)
    schema.model_validate({segment.id: ["word"] * count
                           for segment, count in zip(script.segments, targets)})


def test_repair_rejects_unchanged_model_response(execution, script, monkeypatch):
    async def unchanged(*args, **kwargs):
        return {"replacements": [script.segments[2].model_dump()], "claims": []}
    monkeypatch.setattr(script_module, "ask_model", unchanged)
    with pytest.raises(ValueError, match="without correcting"):
        asyncio.run(script_module.repair(execution[0].settings, execution[2], script,
            [{"code": "naturalness", "segment_ids": ["s03"], "detail": "Make this concrete"}]))


def test_metadata_only_repair_preserves_every_segment(execution, script, monkeypatch):
    async def repair_title(*args, **kwargs):
        return {"replacements": [], "title": "Keep Your Raise Without Raising Your Bills"}
    monkeypatch.setattr(script_module, "ask_model", repair_title)
    issues = [{"code": "contradiction", "segment_ids": ["s01"], "fields": ["title"], "detail": "The title contradicts the lesson."}]
    fixed = asyncio.run(script_module.repair(execution[0].settings, execution[2], script, issues))
    assert fixed.title == "Keep Your Raise Without Raising Your Bills"
    assert fixed.segments == script.segments
    assert fixed.description == script.description
    assert fixed.claims == script.claims
    assert script.title != fixed.title
    issues[0]["fields"] = ["text"]
    with pytest.raises(ValueError, match="unflagged metadata"):
        asyncio.run(script_module.repair(execution[0].settings, execution[2], script, issues))


@pytest.mark.parametrize('allowed_metadata,expected_allowed', [([], set()), (['description'], {'description'})])
def test_repair_prompt_and_decoding_schema_enforce_metadata_allowlist(execution, script, monkeypatch,
                                                                    allowed_metadata, expected_allowed):
    captured = []

    async def repaired(settings, context, prompt, schema, **kwargs):
        captured.append((prompt, kwargs.get('decoding_schema')))
        replacement = script.segments[2].model_dump()
        replacement['text'] = 'Schedule a manageable transfer after payday and first cover essential bills.'
        result = {'replacements': [replacement], 'claims': []}
        if 'description' in expected_allowed:
            result['description'] = 'A clearer description of this practical savings decision.'
        return result

    monkeypatch.setattr(script_module, 'ask_model', repaired)
    fields = ['text', *allowed_metadata]
    issues = [{'code': 'naturalness', 'segment_ids': ['s03'], 'fields': fields,
               'detail': 'Improve the current segment.'}]
    if 'description' in expected_allowed:
        fixed = asyncio.run(script_module.repair(execution[0].settings, execution[2], script, issues))
        assert fixed.description == 'A clearer description of this practical savings decision.'
    else:
        asyncio.run(script_module.repair(execution[0].settings, execution[2], script, issues))

    prompt, decoding_schema = captured[0]
    properties = decoding_schema['properties']
    assert ('title' in properties) is ('title' in expected_allowed)
    assert ('description' in properties) is ('description' in expected_allowed)
    assert 'ALLOWED METADATA FIELDS: ' + json.dumps(allowed_metadata) in prompt
    assert all(field not in decoding_schema.get('required', []) for field in ('title', 'description'))
    assert 'claims' in properties


def test_script_writer_lane_overrides_only_authoring_calls_and_keeps_profile_frozen(execution, script, monkeypatch):
    from atnos.production import script_model_lane

    context = execution[2]
    profile_before = copy.deepcopy(context.artifacts['project_profile'])
    default_endpoint = execution[0].settings.ollama_base_url
    lane = {'digest': 'a' * 64, 'model': 'qwen3.5:9b', 'model_digest': 'b' * 64,
            'endpoint': 'http://127.0.0.1:11436',
            'execution_scope': 'normal_produce_script_authoring'}
    context.artifacts['_active_script_model_lane'] = lane
    monkeypatch.setattr(script_model_lane, 'load_script_model_lane', lambda *_args, **_kwargs: dict(lane))
    calls = []

    async def local_call(settings, prompt, **kwargs):
        assert kwargs['think'] is False
        calls.append((settings.ollama_base_url, kwargs['model']))
        if kwargs['model'] == 'qwen3:14b':
            return {'passed': True, 'issues': []}
        return script.model_dump(mode='json', exclude={'claims'})

    monkeypatch.setattr(script_module, 'ask_ollama_json', local_call)
    asyncio.run(script_module.ask_model(execution[0].settings, context, 'authoring request',
                                       script_module.AdaptedScript))
    asyncio.run(script_module.ask_model(execution[0].settings, context, 'review request',
                                       ScriptReview, reviewing=True))
    context.artifacts.pop('_active_script_model_lane')
    asyncio.run(script_module.ask_model(execution[0].settings, context, 'ordinary authoring request',
                                       script_module.AdaptedScript))

    assert calls == [
        ('http://127.0.0.1:11436', 'qwen3.5:9b'),
        (default_endpoint, 'qwen3:14b'),
        (default_endpoint, 'qwen3:14b'),
    ]
    assert context.artifacts['project_profile'] == profile_before
    audits = [json.loads(path.read_text(encoding='utf-8'))
              for path in (context.artifact_dir / 'model_calls').glob('*.json')]
    trial_audit = next(row for row in audits if row.get('script_model_lane_receipt_digest') == 'a' * 64)
    assert trial_audit['effective_model'] == 'qwen3.5:9b'
    assert trial_audit['script_model_lane_model_digest'] == 'b' * 64


@pytest.mark.parametrize("changed_field", [None, "title", "description"])
def test_segment_repair_can_echo_metadata_but_cannot_change_it(execution, script, monkeypatch, changed_field):
    replacement = script.segments[2].model_dump()
    replacement["text"] = "Schedule a small savings transfer after payday, once rent and essential bills are covered."
    response = {"replacements": [replacement], "claims": [], "title": script.title, "description": script.description}
    if changed_field:
        response[changed_field] = "A different title or description that was never requested."
    async def repaired(*args, **kwargs):
        return response
    monkeypatch.setattr(script_module, "ask_model", repaired)
    issues = [{"code": "naturalness", "segment_ids": ["s03"], "fields": ["text"], "detail": "Make the instruction concrete"}]
    if changed_field:
        with pytest.raises(ValueError, match="unflagged metadata"):
            asyncio.run(script_module.repair(execution[0].settings, execution[2], script, issues))
    else:
        fixed = asyncio.run(script_module.repair(execution[0].settings, execution[2], script, issues))
        assert fixed.title == script.title and fixed.description == script.description
        assert fixed.segments[2].text == replacement["text"]
        assert fixed.segments[:2] == script.segments[:2]


def test_review_collects_metadata_with_length_errors_without_overriding_them(execution, script, monkeypatch):
    script.segments[0].text += " " + " ".join(["word"] * 40)
    calls = []
    async def review(*args, **kwargs):
        calls.append(1)
        return {"passed": False, "issues": [{"code": "title_contradiction", "segment_ids": ["s01"],
            "fields": ["title"], "detail": "The headline reverses the intended lesson."}]}
    monkeypatch.setattr(script_module, "ask_model", review)
    result = asyncio.run(script_module.review(execution[0].settings, execution[2], script))
    assert {"word_budget", "title_contradiction"}.issubset({issue["code"] for issue in result["issues"]})
    assert result["status"] == "failed" and len(calls) == 1


def test_word_repair_must_meet_profile_before_checkpointing(execution, script, monkeypatch):
    async def still_long(settings, context, prompt, schema, **kwargs):
        assert "WORD ALLOCATION" in prompt
        assert context.artifacts["production_order"]["topic"] in prompt
        assert kwargs['decoding_schema']['properties']['s01']['maxItems'] <= 21
        return {s.id: ['spending'] * 28 for s in script.segments}
    monkeypatch.setattr(script_module, "ask_model", still_long)
    with pytest.raises(ValueError, match="at most"):
        asyncio.run(script_module.repair(execution[0].settings, execution[2], script,
            [{"code": "word_budget", "segment_ids": [s.id for s in script.segments], "detail": "Too long"}]))


def test_full_repair_prompt_sets_global_budget_and_review_rejects_oversized_draft(execution, script, monkeypatch):
    review_calls = []
    low, high = execution[2].artifacts["project_profile"]["production"]["word_budget"]

    async def model(settings, context, prompt, schema, **kwargs):
        if schema is ScriptReview:
            review_calls.append(prompt)
            # Even a model's positive verdict cannot override deterministic gates.
            return {"passed": True, "issues": []}
        assert "HARD GLOBAL WORD BUDGET" in prompt
        assert f"{low}-{high} spoken words, inclusive" in prompt
        assert "one distinct decision action" in prompt
        assert "Do not arbitrarily shorten beyond the word allocation" not in prompt
        replacements = []
        for segment in script.segments:
            replacement = segment.model_dump()
            replacement["text"] += (
                " Compare the immediate benefit with a meaningful alternative before deciding whether to buy."
            )
            replacements.append(replacement)
        return {"replacements": replacements, "claims": []}

    monkeypatch.setattr(script_module, "ask_model", model)
    issues = [
        {"code": "word_budget", "segment_ids": [segment.id for segment in script.segments],
         "detail": "Too long"},
        {"code": "practical_share", "segment_ids": ["s02", "s03", "s04"],
         "detail": "Each practical beat needs a useful decision action."},
    ]
    repaired = asyncio.run(script_module.repair(execution[0].settings, execution[2], script, issues))
    assert sum(len(segment.text.split()) for segment in repaired.segments) > high
    verdict = asyncio.run(script_module.review(execution[0].settings, execution[2], repaired))
    assert verdict["status"] == "failed"
    assert "word_budget" in {item["code"] for item in verdict["issues"]}
    assert len(review_calls) == 1
    assert script_module.SCRIPT_REPAIR_PROMPT_VERSION == 28
    assert script_module.SCRIPT_VALIDATOR_VERSION == 42


@pytest.mark.parametrize("defect", [None, "missing", "multiword", "foreign", "metadata", "too_short", "truncated"])
def test_narration_repair_preserves_scene_and_evidence_fields(execution, script, monkeypatch, defect):
    original = script.model_dump(mode="json")
    replacements = {s.id: s.text.split() for s in script.segments}
    replacements['s01'] = "Keep savings affordable: check essential expenses carefully before deciding what you can comfortably set aside regularly.".split()
    if defect == "missing":
        replacements.pop('s06')
    elif defect == "multiword":
        replacements['s01'][0] = 'Keep your'
    elif defect == "foreign":
        replacements['foreign'] = replacements.pop('s06')
    elif defect == "metadata":
        replacements['on_screen'] = ['Unrequested', 'headline']
    elif defect == 'too_short':
        replacements['s01'] = ['Save', 'regularly.']
    elif defect == 'truncated':
        replacements['s01'][-1] = 'because'
    async def model(settings, context, prompt, schema, **kwargs):
        assert schema.__name__ == 'AllocatedFinanceNarration'
        assert kwargs['decoding_schema']['additionalProperties'] is False
        return replacements
    monkeypatch.setattr(script_module, "ask_model", model)
    issues = [{"code": "word_budget", "segment_ids": [s.id for s in script.segments], "detail": "Too long"}]
    if defect:
        with pytest.raises(ValueError):
            asyncio.run(script_module.repair(execution[0].settings, execution[2], script, issues))
    else:
        fixed = asyncio.run(script_module.repair(execution[0].settings, execution[2], script, issues))
        assert fixed.segments[0].text == ' '.join(replacements['s01'])
        assert script_issues(fixed, execution[2].artifacts["project_profile"]) == []
        for before, after in zip(script.segments, fixed.segments, strict=True):
            assert before.model_dump(exclude={"text"}) == after.model_dump(exclude={"text"})
        assert fixed.title == script.title and fixed.claims == script.claims
    assert script.model_dump(mode="json") == original


def test_local_repair_does_not_modify_good_segments(script):
    replacement = script.segments[2].model_dump()
    replacement["text"] = "Choose a manageable amount and schedule a payday transfer, after allowing enough money for your essential bills."
    original = script.model_dump(mode="json")
    fixed = apply_segment_repair(script, [replacement], {"s03"})
    assert fixed.segments[2].text != script.segments[2].text
    for before, after in zip(script.segments, fixed.segments, strict=True):
        if before.id != "s03":
            assert before == after
    assert script.model_dump(mode="json") == original
    with pytest.raises(ValueError, match="explicitly flagged"):
        apply_segment_repair(script, [replacement], {"s01"})
    with pytest.raises(ValueError):
        apply_segment_repair(script, [], {"s03"})


def test_repair_cannot_checkpoint_spelled_out_but_still_unsupported_amount(execution, script):
    context = execution[2]
    context.artifacts["script_calculations"] = {"claims": {}}
    script.segments[0].text = "Can you save $20 each week while keeping all of your essential bills covered?"
    issues = script_issues(script, context.artifacts["project_profile"])
    replacement = script.segments[0].model_dump()
    replacement["text"] = "Can you save twenty dollars each week while keeping all of your essential bills covered?"
    with pytest.raises(ValueError, match="did not resolve.*spoken_amount_mismatch"):
        script_module.validate_repair_response(context, script, issues, {"replacements": [replacement]})
    replacement["text"] = "Can you build a weekly savings habit while keeping all of your essential bills covered?"
    fixed = script_module.validate_repair_response(context, script, issues, {"replacements": [replacement]})
    assert script_issues(fixed, context.artifacts["project_profile"]) == []


def test_repair_prompt_lists_empty_per_segment_numeric_inventory_and_guard_rejects_added_amount(execution, script, monkeypatch):
    captured = []

    async def model(settings, context, prompt, schema, **kwargs):
        captured.append(prompt)
        replacements = []
        for segment_id in ('s01', 's03'):
            replacement = next(segment for segment in script.segments if segment.id == segment_id).model_dump()
            replacement['text'] = 'Set aside ten dollars before making an optional purchase today.'
            replacements.append(replacement)
        return {'replacements': replacements, 'claims': []}

    monkeypatch.setattr(script_module, 'ask_model', model)
    issues = [{'code': 'naturalness', 'segment_ids': ['s01', 's03'],
               'detail': 'Make the targeted lines more natural.'}]
    with pytest.raises(ValueError):
        asyncio.run(script_module.repair(execution[0].settings, execution[2], script, issues))

    assert len(captured) == 1
    assert 'PER-SEGMENT NUMERIC INVENTORY' in captured[0]
    assert '"segment_id": "s01", "spoken_numbers": []' in captured[0]
    assert '"segment_id": "s03", "spoken_numbers": []' in captured[0]
    assert 'do not introduce or repeat any numeric amount there' in captured[0]


def test_repair_protects_shared_numeric_claim(script):
    script.claims = [claim()]
    script.segments[0].claim_ids = script.segments[1].claim_ids = ["c1"]
    changed = claim().model_dump()
    changed["result"] = 20000
    with pytest.raises(ValueError, match="unaffected segment"):
        apply_segment_repair(script, [script.segments[0].model_dump()], {"s01"}, claims=[changed])


@pytest.mark.parametrize("verdict", [{"passed": True, "issues": [{"code": "bad", "segment_ids": ["s01"], "detail": "bad copy"}]},
    {"passed": False, "issues": []}, {"passed": "true", "issues": []},
    {"passed": False, "issues": [{"code": "bad", "segment_ids": [], "detail": "No target"}]}])
def test_malformed_verdict_cannot_pass(verdict):
    with pytest.raises(ValueError):
        ScriptReview.model_validate(verdict)


def test_configured_reference_is_required_and_its_content_is_hashed(execution):
    store, _, context = execution
    evidence = script_module.gather_script_context(store.settings, context)
    assert evidence["usage"]["reference_samples"] == 40
    assert len(evidence["reference_source"]["sha256"]) == 64
    context.artifacts["project_profile"]["production"]["reference_profile"] = "learning/missing.json"
    with pytest.raises(FileNotFoundError):
        script_module.gather_script_context(store.settings, context)
    context.artifacts["project_profile"]["production"]["reference_profile"] = "../../outside.json"
    with pytest.raises(ValueError, match="escapes"):
        script_module.gather_script_context(store.settings, context)


def test_model_failure_has_no_canned_fallback(execution, monkeypatch):
    async def unavailable(*a, **kw):
        raise TimeoutError("Model unavailable")
    monkeypatch.setattr(script_module, "ask_model", unavailable)
    store, _, context = execution
    with pytest.raises(TimeoutError):
        asyncio.run(script_module.run(store.settings, context))
    assert not (context.artifact_dir / "script_draft.json").exists()


def test_generation_schema_is_compact_but_full_validation_still_applies(script):
    schema = script_module.generation_schema(EditorialScript)
    assert "maxLength" not in schema["properties"]["description"]
    assert schema["$defs"]["ScriptSegment"]["properties"]["on_screen"]["maxLength"] == 70
    assert schema["$defs"]["ScriptSegment"]["properties"]["type"]["enum"]
    raw = script.model_dump(mode="json")
    raw["segments"][0]["text"] = "x" * 401
    with pytest.raises(ValueError):
        EditorialScript.model_validate(raw)


def test_script_pipeline_repairs_only_flagged_text_and_resumes_without_model_calls(execution, script, monkeypatch):
    store, job, context = execution
    calls = []
    replacement = script.segments[2].model_dump()
    replacement["text"] = "Choose a manageable amount and schedule a payday transfer, after allowing enough money for your essential bills."
    answers = [{"rationale": "This behavioural test does not quote financial amounts.", "examples": []},
        script.model_dump(mode="json", exclude={"claims"}),
        {"passed": False, "issues": [{"code": "naturalness", "segment_ids": ["s03"], "detail": "Make the wording more conversational"}]},
        {"patches": [{key: replacement[key] for key in ('id', 'text', 'on_screen')}]}, {"passed": True, "issues": []}]
    async def model(*a, **kw):
        calls.append(kw)
        return copy.deepcopy(answers.pop(0))
    monkeypatch.setattr(script_module, "ask_model", model)
    with store.lease(job) as token:
        first = asyncio.run(produce_script(StageExecutor(store, job, token)))
    assert len(calls) == 5
    assert first["qa"]["status"] == "passed"
    assert first["segments"][2]["text"] == replacement["text"]
    assert first["segments"][0]["text"] == script.segments[0].text
    with ProductionOrderStore(store.settings).lease(job) as token:
        second = asyncio.run(produce_script(StageExecutor(store, job, token)))
    assert first == second
    assert len(calls) == 5
    assert store.get(job)["provider_calls"] == 5
    assert store.get(job)["status"] != "ready"
    assert (context.artifact_dir / "script.json").is_file()


def test_normal_script_trial_digest_binds_only_authoring_and_final_checkpoints(execution, script, monkeypatch):
    from atnos.production import calculation_plan, script_model_lane, script_review_model_lane

    store, job, context = execution
    lane = {'digest': 'c' * 64, 'model': 'qwen3.5:9b', 'model_digest': 'd' * 64,
            'endpoint': 'http://127.0.0.1:11436',
            'execution_scope': 'normal_produce_script_authoring'}
    review_lane = {'digest': 'e' * 64, 'model': 'qwen3.5:9b', 'model_digest': 'd' * 64,
                   'endpoint': 'http://127.0.0.1:11436',
                   'execution_scope': 'normal_produce_script_review'}
    monkeypatch.setattr(script_model_lane, 'load_script_model_lane', lambda *_args, **_kwargs: dict(lane))
    monkeypatch.setattr(script_review_model_lane, 'load_script_review_model_lane', lambda *_args, **_kwargs: dict(review_lane))
    monkeypatch.setattr(calculation_plan, 'produce_calculations', lambda *_args, **_kwargs: asyncio.sleep(0, result=None))
    monkeypatch.setattr(script_module, 'gather_script_context', lambda *_args: {'reference_source': {}, 'usage': {}})
    async def draft(*_args, **_kwargs):
        return script.model_dump(mode='json')
    async def review(*_args, **_kwargs):
        return {'status': 'passed', 'issues': []}
    monkeypatch.setattr(script_module, 'run', draft)
    monkeypatch.setattr(script_module, 'review', review)
    observed = {}
    async def capture_run(executor, stage, inputs, operation, **_kwargs):
        observed[stage] = copy.deepcopy(inputs)
        result = await operation()
        return result.payload if hasattr(result, 'payload') else result
    monkeypatch.setattr(StageExecutor, 'run', capture_run)
    monkeypatch.setattr(store, 'reconcile_script_checkpoints', lambda *_args: None)
    monkeypatch.setattr(store, 'persist_legacy_artifact', lambda *_args: None)
    frozen_profile = copy.deepcopy(context.artifacts['project_profile'])

    with store.lease(job) as token:
        output = asyncio.run(produce_script(StageExecutor(store, job, token)))

    assert observed['script_draft']['script_model_lane_digest'] == lane['digest']
    assert 'script_review_model_lane_digest' not in observed['script_draft']
    assert 'script_model_lane_digest' not in observed['script_review/0']
    assert observed['script_review/0']['script_review_model_lane_digest'] == review_lane['digest']
    assert observed['script']['script_model_lane_digest'] == lane['digest']
    assert observed['script']['script_review_model_lane_digest'] == review_lane['digest']
    assert output['provenance']['generator'] == lane['model']
    assert output['provenance']['reviewer'] == review_lane['model']
    assert output['provenance']['frozen_reviewer'] == frozen_profile['production']['script_model']['reviewer']
    assert output['provenance']['script_model_lane']['receipt_digest'] == lane['digest']
    assert output['provenance']['script_review_model_lane']['receipt_digest'] == review_lane['digest']
    assert output['provenance']['script_review_model_lane']['same_model_as_writer'] is True
    assert context.artifacts['project_profile'] == frozen_profile
    assert '_active_script_model_lane' not in context.artifacts
    assert '_active_script_review_model_lane' not in context.artifacts


def test_review_lane_routes_only_review_calls_and_audits_receipt(execution, monkeypatch):
    from atnos.production import script_review_model_lane

    store, job, context = execution
    lane = {'digest': 'e' * 64, 'model': 'qwen3.5:9b', 'model_digest': 'd' * 64,
            'endpoint': 'http://127.0.0.1:11436', 'execution_scope': 'normal_produce_script_review'}
    monkeypatch.setattr(script_review_model_lane, 'load_script_review_model_lane', lambda *_args, **_kwargs: dict(lane))
    context.artifacts['_active_script_review_model_lane'] = lane
    context.artifacts['_active_script_model_lane'] = {
        'digest': 'c' * 64, 'model': 'qwen3.5:9b', 'model_digest': 'd' * 64,
        'endpoint': 'http://127.0.0.1:11436', 'execution_scope': 'normal_produce_script_authoring'}
    calls = []

    async def fake_ollama(settings_arg, prompt, **kwargs):
        calls.append((settings_arg, kwargs))
        return {'passed': True, 'issues': []}

    monkeypatch.setattr(script_module, 'ask_ollama_json', fake_ollama)
    before = copy.deepcopy(context.artifacts['project_profile'])
    result = asyncio.run(script_module.ask_model(store.settings, context, 'review current draft', ScriptReview, reviewing=True))
    audit_files = list((context.artifact_dir / 'model_calls').glob('*.json'))
    audit = json.loads(max(audit_files, key=lambda path: path.stat().st_mtime_ns).read_text(encoding='utf-8'))

    assert result == {'passed': True, 'issues': []}
    assert calls[-1][0].ollama_base_url == lane['endpoint']
    assert calls[-1][1]['model'] == lane['model']
    assert audit['effective_model'] == lane['model']
    assert audit['script_review_model_lane_receipt_digest'] == lane['digest']
    assert audit['script_review_model_lane_model_digest'] == lane['model_digest']
    assert audit['script_review_model_lane_scope'] == lane['execution_scope']
    assert 'script_model_lane_receipt_digest' not in audit
    assert context.artifacts['project_profile'] == before

    asyncio.run(script_module.ask_model(store.settings, context, 'unrelated reviewing caller', EditorialScript, reviewing=True))
    audit_files = list((context.artifact_dir / 'model_calls').glob('*.json'))
    unrelated = next(json.loads(path.read_text(encoding='utf-8')) for path in audit_files
                     if json.loads(path.read_text(encoding='utf-8')).get('prompt') == 'unrelated reviewing caller')
    assert calls[-1][1]['model'] == context.artifacts['project_profile']['production']['script_model']['reviewer']
    assert 'script_review_model_lane_receipt_digest' not in unrelated

    context.artifacts.pop('_active_script_review_model_lane')
    asyncio.run(script_module.ask_model(store.settings, context, 'review later voice correction', ScriptReview, reviewing=True))
    audit_files = list((context.artifact_dir / 'model_calls').glob('*.json'))
    later = next(json.loads(path.read_text(encoding='utf-8')) for path in audit_files
                 if json.loads(path.read_text(encoding='utf-8')).get('prompt') == 'review later voice correction')
    assert calls[-1][1]['model'] == context.artifacts['project_profile']['production']['script_model']['reviewer']
    assert 'script_review_model_lane_receipt_digest' not in later


def test_reviewer_prompt_separates_historical_notes_from_current_script(execution, script, monkeypatch):
    store, _, context = execution
    context.artifacts['project_profile']['feedback_rules'] = [{
        'source': 'scoped_operator_feedback', 'feedback_hash': 'old',
        'feedback': {'target_segments': ['s02'], 'issues': [
            {'code': 'obsolete_visual_intent', 'detail': 'The old coffee vacation example is missing.'}]}}
    ]
    monkeypatch.setattr(script_module, 'script_issues', lambda *_args, **_kwargs: [
        {'code': 'narrative_rules_violation', 'segment_ids': ['s03'], 'fields': ['text'], 'detail': 'Current test finding.'}])
    prompts = []

    async def capture(_settings, _context, prompt, _schema, **_kwargs):
        prompts.append(prompt)
        return {'passed': True, 'issues': []}

    monkeypatch.setattr(script_module, 'ask_model', capture)
    result = asyncio.run(script_module.review(store.settings, context, script))
    prompt = prompts[0]
    assert 'Historical notes are not evidence of current defects' in prompt
    assert 'cite a defect only when it is visible in the current script' in prompt
    assert 'CURRENT SCRIPT:' in prompt
    assert 'The old coffee vacation example is missing' not in prompt
    assert result['status'] == 'failed'  # deterministic current issue remains a real finding


def test_automatic_reviewer_does_not_receive_previous_verdict_as_current_evidence(execution,script,monkeypatch):
    store,_,context=execution
    context.artifacts['studio_script_recovery']={'origin':'autonomous_recovery'}
    obsolete='False old assertion about a missing object'
    monkeypatch.setattr(script_module,'_active_studio_script_bundle',lambda *args,**kwargs:{
        'current_script':script.model_dump(),'registered_note':obsolete,
        'registered_acceptance_targets':[{'detail':obsolete}], 'current_validator_findings':[]})
    monkeypatch.setattr(script_module,'script_issues',lambda *args,**kwargs:[])
    prompts=[]
    async def reviewer(*args,**kwargs):
        prompts.append(args[2])
        return {'passed':True,'issues':[]}
    monkeypatch.setattr(script_module,'ask_model',reviewer)
    result=asyncio.run(script_module.review(store.settings,context,script))
    assert obsolete not in prompts[0]
    assert script.segments[0].text in prompts[0]
    assert 'existing overall minimum practical share still applies' in prompts[0]
    assert result['status']=='passed'


def test_pipeline_stops_retries_at_persistent_limit(execution, monkeypatch):
    store, job, _ = execution
    calls = []
    async def invalid(*a, **kw):
        calls.append(1)
        return {"not_a_script": True}
    monkeypatch.setattr(script_module, "ask_model", invalid)
    limit = store.get(job)["profile"]["production"]["max_attempts_per_element"]
    with store.lease(job) as token, pytest.raises(ProductionBlocked, match="attempt_limit"):
        asyncio.run(produce_script(StageExecutor(store, job, token)))
    with store.lease(job) as token, pytest.raises(ProductionBlocked, match="attempt_limit"):
        asyncio.run(produce_script(StageExecutor(store, job, token)))
    assert len(calls) == limit
    assert store.get(job)["status"] == "blocked"
    assert store.get(job)["provider_calls"] == limit


def test_legacy_recovery_and_video_resume_cannot_take_over_order(execution):
    store, job, _ = execution
    with store.lease(job):
        result = ProductionJobRecoveryService(store.settings).recover_stale_jobs(recover_all_active=True)
    assert result["recovered_count"] == 0
    assert store.get(job)["status"] == "queued"
    with pytest.raises(ValueError, match="durable order"):
        _load_video_resume_context(store.settings, job)
