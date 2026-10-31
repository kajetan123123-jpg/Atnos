import asyncio

import pytest

from atnos.production import request_fidelity as fidelity
from atnos.production.calculation_plan import CalculationPlan
from atnos.production.steps import order_script
from atnos.production.steps.order_assets import validate_plan
from test_calculation_plan import plan
from test_production_order_assets import plan_for
from test_production_order_script import script, execution
from test_production_order_store import store


@pytest.mark.parametrize('topic,expected', [
    ('Build a weekly savings habit that lasts', 'week'),
    ('Cotygodniowe oszczędzanie', 'week'),
    ('Monthly transfers with an annual review', 'month'),
    ('Compare weekly and monthly savings', None), ('Build a savings habit', None),
])
def test_explicit_cadence_is_preserved_without_guessing(topic, expected):
    assert fidelity.requested_cadence({'topic': topic}) == expected


def test_real_regression_rejects_monthly_calculation_for_weekly_request():
    with pytest.raises(ValueError, match='week.*month'):
        fidelity.validate_calculation_cadence(CalculationPlan.model_validate(plan()), {'topic': 'Weekly savings habit'})
    correct = plan('repeated_saving', amount_per_period=20, periods=12, period='week')
    fidelity.validate_calculation_cadence(CalculationPlan.model_validate(correct), {'topic': 'Weekly savings habit'})
    fidelity.validate_calculation_cadence(CalculationPlan(rationale='A practical process does not need an invented amount.', examples=[]), {'topic': 'Weekly savings habit'})


def test_rejects_actual_cadence_drift_and_invented_review_interval_but_allows_monthly_bills(script):
    script.segments[0].text = 'Start by reviewing your monthly expenses.'
    script.segments[1].text = 'Choose a small, affordable amount for monthly transfers.'
    script.segments[1].on_screen = 'Set monthly savings'
    script.segments[3].text = 'Review your habit every second year.'
    issues = fidelity.script_issues(script, {'topic': 'Build a weekly savings habit that lasts'})
    assert {row['code'] for row in issues} == {'request_cadence', 'invented_review_interval'}
    assert not any('s01' in row['segment_ids'] for row in issues)
    assert all(row['fields'] for row in issues)


def test_weekly_habit_may_state_yearly_savings_goal_without_cadence_defect():
    """prod-ef9f833aa323: description 'weekly transfer' + 'yearly savings goal' is valid framing."""
    from types import SimpleNamespace

    script = SimpleNamespace(
        title="How Fifty Dollars a Week Becomes Twenty Six Hundred a Year",
        description=(
            "Automate a small weekly transfer to build a meaningful yearly savings goal. "
            "No investment returns needed, just simple math."
        ),
        segments=[],
    )
    order = {
        "topic": "How fifty dollars a week becomes twenty six hundred a year",
        "direction": "fifty dollars each week times fifty-two weeks equals twenty-six hundred dollars in a year",
    }
    assert fidelity.script_issues(script, order) == []


def test_request_defect_cannot_be_overridden_by_a_positive_model_review(execution, script, monkeypatch):
    store, _, context = execution
    context.artifacts['production_order']['topic'] = 'Build a weekly savings habit'
    script.segments[1].text = 'Choose a small amount for monthly transfers.'
    async def unexpected(*args, **kwargs):
        pytest.fail('Deterministic request defects must fail before a model can approve them')
    monkeypatch.setattr(order_script, 'ask_model', unexpected)
    result = asyncio.run(order_script.review(store.settings, context, script))
    assert result['status'] == 'failed'
    assert any(row['code'] == 'request_cadence' for row in result['issues'])


def test_metadata_only_repair_preserves_every_spoken_segment(execution, script):
    _, _, context = execution
    context.artifacts['production_order']['topic'] = 'Build a weekly savings habit'
    script.title = 'Make monthly savings stick'
    issues = fidelity.script_issues(script, context.artifacts['production_order'])
    fixed = order_script.validate_repair_response(context, script, issues,
        {'replacements': [], 'claims': [], 'title': 'Make weekly savings stick'})
    assert fixed.segments == script.segments
    assert fixed.claims == script.claims
    assert fixed.title == 'Make weekly savings stick'
    with pytest.raises(ValueError, match='did not resolve'):
        order_script.validate_repair_response(context, script, issues,
            {'replacements': [], 'claims': [], 'title': 'Begin monthly savings now'})


def test_bank_settings_cannot_be_illustrated_with_calculator_footage(script):
    script.segments[1].text = 'Choose an affordable amount for weekly transfers. Set it up automatically.'
    plan = plan_for(script)
    plan.scenes[1].kind, plan.scenes[1].asset_id = 'footage', 'calculator'
    library = {'assets': [{'id': 'calculator', 'duration_seconds': 20}]}
    with pytest.raises(ValueError, match='steps or flow'):
        validate_plan(plan, script, library)
    plan.scenes[1].kind, plan.scenes[1].asset_id = 'steps', None
    validate_plan(plan, script, library)


def test_generic_transfer_mentions_still_allow_relevant_footage(script):
    script.segments[1].text = 'A separate account keeps savings apart from the money for your bills.'
    assert not fidelity.requires_action_diagram(script.segments[1])


def test_empty_calculation_plan_does_not_encourage_writer_to_invent_an_example(execution):
    _, _, context = execution
    context.artifacts['script_calculations'] = {'claims': {}}
    prompt = order_script.draft_prompt(context, {})
    assert 'NO approved numerical example' in prompt
    assert 'Keep every claim_ids array empty' in prompt
    assert 'Prefer ONE useful worked numerical example' not in prompt
    assert 'Compound operands' not in prompt
    from atnos.production.calculation_plan import compile_plan
    context.artifacts['script_calculations'] = compile_plan(CalculationPlan.model_validate(plan()), 'dollars')
    prompt = order_script.draft_prompt(context, {})
    assert 'only their existing IDs and spoken_result' in prompt
    assert 'NO approved numerical example' not in prompt
    assert 'Do not add numerical examples' in prompt
    context.artifacts.pop('script_calculations')
    assert 'Compound operands' in order_script.draft_prompt(context, {})
