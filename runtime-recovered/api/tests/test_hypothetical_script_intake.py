from __future__ import annotations

import asyncio
import copy
import json

import pytest

from atnos.production.hypothetical_script_intake import (
    HypotheticalScriptIntake, compile_supplied_plan, make_hypothetical_intake,
    produce_supplied_calculations, text_hash,
)
from atnos.production.order_execution import StageExecutor, produce_script
from atnos.production.order_intake import intake_blockers
from atnos.production.project_profile import ProductionOrder, load_project_profile, profile_for_order
from atnos.production.script_adaptation import bind_claims, compiled_claims, draft_prompt, writer_input
from atnos.production.steps import order_script
from test_finance_four_intake import supported_intake
from test_production_order_script import execution, script
from test_production_order_store import store


SOURCE = "\r\nTITLE: The $10 Trap That Keeps You Broke\r\nALEX: This $10 purchase isn't costing you $10.\r\nVISUAL: $10 → $22 → $47 → $101.\r\n"


def brief():
    return {"language": "en", "evidence_kind": "hypothetical_assumptions",
            "calculation_plan": {"rationale": "Illustrate opportunity cost using a stated hypothetical rate, never a market average.",
                "examples": [{"id": "c1", "kind": "lump_sum", "purpose": "Show the alternative future value of one initial ten-dollar deposit.",
                              "currency": "dollars", "principal": 10, "annual_rate": .08, "years": 30,
                              "spoken_rounding": 1}]},
            "editorial_corrections": ["Eight percent is a hypothetical constant annual return, not an observed average or a guarantee.",
                "State that the example excludes fees, tax and inflation; preserve the purchase decision, not a guaranteed outcome."]}


def contract(**changes):
    item = make_hypothetical_intake(SOURCE, brief(), target_duration_seconds=44)
    return HypotheticalScriptIntake.model_validate(item.model_dump(mode="json") | changes)


def order(**changes):
    return ProductionOrder(topic="The $10 Trap That Keeps You Broke", intake=contract(),
                           delivery_mode="local_only", **changes)


def test_lossless_source_and_hypothetical_provenance_are_not_external_factcheck():
    intake = contract().model_dump(mode="json")
    assert intake["original_scenario"] == SOURCE
    assert intake["source_scenario_sha256"] == text_hash(SOURCE)
    assert intake["review_status"] == "supplied_not_production_qa"
    assert not {"factcheck_sha256", "fact_source_urls", "scenario_index"} & intake.keys()
    assert intake_blockers(intake) == []
    parsed = ProductionOrder.model_validate(order().model_dump(mode="json"))
    assert isinstance(parsed.intake, HypotheticalScriptIntake)
    assert parsed.intake.original_scenario == SOURCE


@pytest.mark.parametrize("field", ["original_scenario", "corrected_brief"])
def test_modified_source_or_assumptions_requires_a_new_digest(field):
    intake = contract().model_dump(mode="json")
    intake[field] += " "
    with pytest.raises(ValueError, match="SHA256"):
        HypotheticalScriptIntake.model_validate(intake)
    assert intake_blockers(intake)
    with pytest.raises(ValueError, match="SHA256"):
        compile_supplied_plan(intake)


@pytest.mark.parametrize("changes", [{"result": 101}, {"annual_rate": 8}, {"annual_rate": float("nan")},
                                    {"years": 101}, {"principal": -10}, {"currency": "PLN"}])
def test_hypothetical_input_rejects_model_results_and_invalid_assumptions(changes):
    data = brief()
    data["calculation_plan"]["examples"][0].update(changes)
    with pytest.raises(ValueError):
        make_hypothetical_intake(SOURCE, data, target_duration_seconds=44)


def test_hypothetical_input_cannot_smuggle_external_facts_or_an_empty_plan():
    data = brief()
    data["verified_external_facts"] = [{"id": "fake", "source": "https://www.gov.pl/fake"}]
    with pytest.raises(ValueError, match="Extra inputs"):
        make_hypothetical_intake(SOURCE, data, target_duration_seconds=44)
    data = brief()
    data["calculation_plan"]["examples"] = []
    with pytest.raises(ValueError, match="named calculation assumptions"):
        make_hypothetical_intake(SOURCE, data, target_duration_seconds=44)


def test_legacy_polish_contract_and_supported_sources_remain_unchanged():
    legacy = supported_intake()
    encoded = legacy.model_dump(mode="json")
    assert "kind" not in encoded
    assert encoded["source_language"] == "pl" and "factcheck_sha256" in encoded
    assert intake_blockers(encoded) == []
    parsed = ProductionOrder(topic="Legacy intake", intake=encoded)
    assert parsed.intake.model_dump(mode="json") == encoded


def test_hypothetical_profile_is_local_and_keeps_gates_budgets_and_voice(store):
    base = load_project_profile(store.settings, "wealth_blueprint")
    before = copy.deepcopy(base)
    scoped = profile_for_order(base, order())
    assert base == before
    assert scoped["production"]["duration_seconds"] == [42, 46]
    assert scoped["production"]["word_budget"] == [96, 118]
    assert scoped["production"]["example_currency"] == "dollars"
    for key in ("minimum_practical_share", "max_attempts_per_element", "max_provider_calls", "max_cost_usd",
                "required_gates", "voice", "publish"):
        assert scoped["production"][key] == base["production"][key]
    with pytest.raises(ValueError, match="lokalnego"):
        ProductionOrder(topic="A supplied example", intake=contract())
    with pytest.raises(ValueError):
        order(project="children_stories")


def test_deterministic_lump_sum_uses_actual_calculator_not_claimed_market_average():
    compiled = compile_supplied_plan(contract().model_dump(mode="json"))
    claim = compiled["claims"]["c1"]
    assert claim["operation"] == "compound" and claim["operands"] == [10, .08, 30]
    assert claim["result"] == pytest.approx(100.6265688907346)
    assert claim["spoken_result"] == "one hundred one" and claim["spoken_rounding"] == 1
    assert "Annual compounding" in claim["assumptions"]
    assert "not a forecast" in claim["assumptions"] and "no recurring deposits" in claim["assumptions"]
    assert compiled["human_review"] == "not_performed"


def test_normal_writer_sees_full_english_scenario_without_polish_tax_rules(execution):
    context = execution[2]
    context.artifacts["production_order"] = order().model_dump(mode="json")
    context.artifacts["project_profile"] = profile_for_order(context.artifacts["project_profile"], order())
    supplied = writer_input(context)
    assert supplied["original_scenario"] == SOURCE
    assert supplied["calculations"]["c1"]["result"] == pytest.approx(100.6265688907346)
    assert order_script.reviewed_source_facts(context) == {}
    prompt = draft_prompt(context, {})
    assert "supplied English scenario" in prompt and "hypothetical" in prompt
    assert "Retain PLN and Polish jurisdiction" not in prompt
    assert "factcheck_sha256" not in supplied
    assert "$10 → $22 → $47 → $101" in prompt
    assert "Do not claim that a generated still animates" in prompt


def test_claim_binding_ignores_a_writer_result_and_refuses_unknown_ids(execution, script):
    context = execution[2]
    context.artifacts["production_order"] = order().model_dump(mode="json")
    raw = script.model_dump(mode="json")
    raw["segments"][0]["claim_ids"] = ["c1"]
    raw["claims"] = [{"id": "c1", "result": 9999}]
    bound = bind_claims(context, raw)
    assert bound["claims"][0]["result"] == pytest.approx(100.6265688907346)
    assert compiled_claims(context)["c1"].unit == "dollars"
    raw["segments"][0]["claim_ids"] = ["c2"]
    with pytest.raises(ValueError, match="supplied calculation IDs"):
        bind_claims(context, raw)


def test_supplied_calculations_have_normal_durable_no_model_checkpoints(store, monkeypatch):
    job = store.create(order())["id"]
    assert store.get(job)["can_resume"]

    async def forbidden(*args, **kwargs):
        pytest.fail("Supplied assumptions must never be calculated by a model")
    monkeypatch.setattr(order_script, "ask_model", forbidden)
    with store.lease(job) as token:
        first = asyncio.run(produce_supplied_calculations(StageExecutor(store, job, token)))
    with store.lease(job) as token:
        resumed = StageExecutor(store, job, token)
        second = asyncio.run(produce_supplied_calculations(resumed))
    assert first == second == resumed.context.artifacts["script_calculations"]
    state = store.get(job)
    assert state["provider_calls"] == state["reserved_cost_units"] == 0
    assert {row["stage"] for row in state["checkpoints"]} == {"script_calculation_plan", "script_calculations"}
    assert all(row["attempts"] == 1 for row in state["checkpoints"])


def test_normal_produce_script_compiles_hypothetical_before_atnos_authoring(execution, script, monkeypatch):
    store, _, old_context = execution
    job = store.create(order())["id"]
    generated = script.model_dump(mode="json", exclude={"claims"})
    generated["segments"][0]["claim_ids"] = ["c1"]
    generated["segments"][0]["text"] = "Before spending ten dollars, compare the purchase with the future use you would give up."
    generated["segments"][5]["text"] = "Before you close your banking app today, schedule that transfer and check that your essential bills and ordinary daily costs are safely covered."
    observed = []

    async def author(settings, context, prompt, schema, **kwargs):
        assert context.artifacts["script_calculations"]["claims"]["c1"]["result"] == pytest.approx(100.6265688907346)
        observed.append(schema)
        if schema is order_script.ScriptReview:
            return {"passed": True, "issues": []}
        assert schema is order_script.AdaptedScript
        return copy.deepcopy(generated)

    monkeypatch.setattr(order_script, "ask_model", author)
    # This fixture provides the genuine reference-profile path and mocked knowledge lookup.
    assert old_context.artifact_dir.parent.parent.exists()
    with store.lease(job) as token:
        result = asyncio.run(produce_script(StageExecutor(store, job, token)))
    assert result["claims"][0]["result"] == pytest.approx(100.6265688907346)
    assert observed == [order_script.AdaptedScript, order_script.ScriptReview]
    state = store.get(job)
    assert all(row["stage"] in {"script_context", "script_calculation_plan", "script_calculations", "script_draft", "script_review/0", "script"}
               for row in state["checkpoints"])
    assert state["provider_calls"] == 2
