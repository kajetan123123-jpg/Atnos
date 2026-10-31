import asyncio
import copy
import json

import pytest

from atnos.production.calculation_plan import CalculationPlan, compile_plan, produce_calculations
from atnos.production.order_execution import StageExecutor, produce_script
from atnos.production.order_store import ProductionBlocked, ProductionOrderStore
from atnos.production.script_adaptation import bind_claims
from atnos.production.steps import order_script
from test_production_order_script import execution, script
from test_production_order_store import store


def plan(kind="monthly_deposits", **fields):
    example = {"id": "c1", "kind": kind, "purpose": "Show the total saved from regular deposits.",
               "currency": "dollars", "spoken_rounding": 1000,
               **({"monthly_payment": 500, "annual_rate": .05, "years": 5} if kind == "monthly_deposits" else {}), **fields}
    return {"rationale": "A single worked example makes the lesson concrete.", "examples": [example]}


def test_real_failed_script_regression_monthly_is_not_lump_sum_or_product():
    claims = compile_plan(CalculationPlan.model_validate(plan()), "dollars")["claims"]
    claim = claims["c1"]
    assert claim["operation"] == "monthly_contribution"
    assert claim["result"] == pytest.approx(34003.04142042158)
    assert claim["result"] != pytest.approx(500 * 1.05 ** 5)
    assert claim["result"] != 500 * .05 * 5
    assert claim["spoken_result"] == "thirty four thousand"
    assert "end of every month" in claim["assumptions"]
    assert "Not a forecast" in claim["assumptions"]


def test_phase12_long_rationale_is_clamped_not_blocked():
    """E2E prod-bc9bca82b3c7 blocked: model rationale > 400 chars on empty-examples plan."""
    from atnos.production.calculation_plan import normalize_calculation_plan_payload

    long = (
        "The request is focused on behavioural framing around keeping more of a raise, "
        "not on financial math. An invented dollar amount or rate would invent claims "
        "the brief never asked for, so an empty examples array is the honest choice here "
        "and keeps the writer free to stay qualitative without fake percentages. "
        "Padding this rationale past the schema limit reproduces the live failure where "
        "pydantic rejected CalculationPlan before script generation could begin."
    )
    assert len(long) > 400
    normalized = normalize_calculation_plan_payload({"rationale": long, "examples": []})
    plan_obj = CalculationPlan.model_validate(normalized)
    assert 10 <= len(plan_obj.rationale) <= 400
    assert plan_obj.examples == []
    assert "behavioural" in plan_obj.rationale or "raise" in plan_obj.rationale


def test_explicit_arithmetic_request_rejects_empty_examples():
    """prod-3ff39dc56e21: model returned empty examples despite 50/week × 52 = 2600."""
    from atnos.production.calculation_plan import CalculationPlan
    from atnos.production.request_fidelity import (
        request_requires_calculation_example,
        validate_calculation_plan_against_request,
    )

    order = {
        "topic": "How fifty dollars a week becomes twenty six hundred a year",
        "direction": (
            "Use one explicit arithmetic example only: fifty dollars saved each week "
            "times fifty-two weeks equals twenty-six hundred dollars in a year."
        ),
    }
    assert request_requires_calculation_example(order) is True
    empty = CalculationPlan.model_validate(
        {
            "rationale": "The request focuses on simple weekly-to-annual savings with no growth.",
            "examples": [],
        }
    )
    with pytest.raises(ValueError, match="explicit arithmetic example"):
        validate_calculation_plan_against_request(empty, order)
    filled = CalculationPlan.model_validate(
        plan(
            "repeated_saving",
            amount_per_period=50,
            periods=52,
            period="week",
            spoken_rounding=1,
            purpose="Show fifty dollars weekly times fifty-two weeks.",
        )
    )
    validate_calculation_plan_against_request(filled, order)
    assert compile_plan(filled, "dollars")["claims"]["c1"]["result"] == pytest.approx(2600)


@pytest.mark.parametrize("kind,fields,value", [
    ("monthly_deposits", {"annual_rate": 0}, 30000),
    ("lump_sum", {"principal": 500, "annual_rate": .05, "years": 5}, 638.14078125),
    ("repeated_saving", {"amount_per_period": 200, "periods": 12, "period": "month"}, 2400),
    ("percentage", {"amount": 200, "percentage_points": 50}, 100),
    ("sum", {"amounts": [40, 60]}, 100),
    ("difference", {"before": 100, "after": 60}, 40),
])
def test_named_calculation_kinds(kind, fields, value):
    data = plan(kind, spoken_rounding=0, **fields)
    compiled = compile_plan(CalculationPlan.model_validate(data), "dollars")
    assert compiled["claims"]["c1"]["result"] == pytest.approx(value)


@pytest.mark.parametrize("amount,periods,result,spoken", [
    (200, 12, 2400, "two thousand four hundred"),
    (1, 2, 2, "two"),
    (12.34, 2, 24.68, "twenty four point six eight"),
])
def test_coarse_model_rounding_cannot_distort_a_simple_example(amount, periods, result, spoken):
    compiled = compile_plan(CalculationPlan.model_validate(plan("repeated_saving",
        amount_per_period=amount, periods=periods, period="month", spoken_rounding=1000)), "dollars")
    assert compiled["claims"]["c1"]["result"] == pytest.approx(result)
    assert compiled["claims"]["c1"]["spoken_result"] == spoken


@pytest.mark.parametrize("fields", [
    {"result": 32800}, {"annual_rate": 5}, {"annual_rate": float("nan")},
    {"years": 101}, {"monthly_payment": -1}, {"kind": "product"}, {"principal": 500},
    {"years": .1}, {"currency": "PLN"},
])
def test_reject_invented_results_invalid_units_and_assumptions(fields):
    with pytest.raises(ValueError):
        compile_plan(CalculationPlan.model_validate(plan(**fields)), "dollars")


def test_writer_claims_are_locked_and_unknown_ids_rejected(execution, script):
    context = execution[2]
    compiled = compile_plan(CalculationPlan.model_validate(plan()), "dollars")
    context.artifacts["script_calculations"] = compiled
    raw = script.model_dump(mode="json")
    raw["segments"][3]["claim_ids"] = ["c1"]
    raw["claims"] = [{"id": "c1", "result": 999999}]
    assert bind_claims(context, raw)["claims"] == [compiled["claims"]["c1"]]
    raw["segments"][3]["claim_ids"] = ["made_up"]
    with pytest.raises(ValueError, match="supplied calculation IDs"):
        bind_claims(context, raw)


def test_calculation_checkpoint_resume_without_another_model_call(execution, monkeypatch):
    store, job, context = execution
    calls = []
    async def model(*args, **kwargs):
        calls.append(1)
        return plan()
    monkeypatch.setattr(order_script, "ask_model", model)
    with store.lease(job) as token:
        result = asyncio.run(produce_calculations(StageExecutor(store, job, token), {}))
    with ProductionOrderStore(store.settings).lease(job) as token:
        resumed = StageExecutor(ProductionOrderStore(store.settings), job, token)
        assert asyncio.run(produce_calculations(resumed, {})) == result
        assert resumed.context.artifacts["script_calculations"] == result
    assert calls == [1]
    assert store.get(job)["provider_calls"] == 1
    assert json.loads((context.artifact_dir / "script_calculations.json").read_text()) == result


def test_bad_calculation_plan_is_bounded_before_voice(execution, monkeypatch):
    store, job, _ = execution
    async def model(*args, **kwargs):
        return plan(result=34000)
    monkeypatch.setattr(order_script, "ask_model", model)
    with store.lease(job) as token, pytest.raises(ProductionBlocked, match="attempt_limit"):
        asyncio.run(produce_script(StageExecutor(store, job, token)))
    state = store.get(job)
    assert state["status"] == "blocked"
    limit = state["profile"]["production"]["max_attempts_per_element"]
    assert 1 <= state["provider_calls"] <= limit
    assert not any(row["stage"].startswith("voice") for row in state["checkpoints"])


def test_writer_and_selective_repair_cannot_change_calculations(execution, script, monkeypatch):
    store, _, context = execution
    context.artifacts["script_calculations"] = compile_plan(CalculationPlan.model_validate(plan()), "dollars")
    generated = script.model_dump(mode="json", exclude={"claims"})
    generated["segments"][3]["claim_ids"] = ["c1"]
    async def author(settings, context, prompt, schema):
        assert schema is order_script.AdaptedScript
        assert "thirty four thousand" in prompt
        return copy.deepcopy(generated)
    monkeypatch.setattr(order_script, "ask_model", author)
    result = asyncio.run(order_script.run(store.settings, context, {}))
    draft = order_script.EditorialScript.model_validate(result.payload)
    assert draft.claims[0].result == context.artifacts["script_calculations"]["claims"]["c1"]["result"]
    async def bad_repair(settings, context, prompt, schema):
        assert schema is order_script.AdaptedScriptRepair
        return {"replacements": [generated["segments"][3]], "claims": [{"id": "c1", "result": 5}]}
    monkeypatch.setattr(order_script, "ask_model", bad_repair)
    with pytest.raises(ValueError, match="Extra inputs"):
        asyncio.run(order_script.repair(store.settings, context, draft,
            [{"code": "arithmetic", "segment_ids": ["s04"], "detail": "Use the compiled figure."}]))
