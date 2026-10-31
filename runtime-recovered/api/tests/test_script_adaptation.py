import asyncio
import json

import pytest

from atnos.production.project_profile import ProductionOrder
from atnos.production.script_adaptation import bind_claims, compiled_claims, draft_prompt, number_words, writer_input
from atnos.production.spoken_numbers import numeric_mentions
from atnos.production.steps.order_script import AdaptedScript, ask_model, reviewed_source_facts
from test_finance_four_intake import supported_intake
from test_production_order_store import store


def context_for(store, index=1):
    intake = supported_intake()
    intake.scenario_index = index
    intake.original_scenario = 'Temat: test\n"Narracja po polsku."\n[WIZUALNIE: stary polski napis]'
    state = store.create(ProductionOrder(topic="Compound growth" if index == 1 else "Selected finance topic", intake=intake))
    return store.context(state["id"])


@pytest.mark.parametrize("value", [0, 19, 24, 100, 952, 149000, 28260, 2.9, 1000000])
def test_number_word_roundtrip(value):
    assert numeric_mentions(number_words(value))[0]["value"] == value


def test_compiler_keeps_exact_pln_arithmetic_and_filters_other_topics(store):
    context = context_for(store)
    claims = compiled_claims(context)
    assert claims["dca_100_30"].result == pytest.approx(149035.9448663336)
    assert claims["dca_100_30"].spoken_result == "one hundred forty nine thousand"
    assert claims["dca_100_30_paid"].result == 36000
    assert all(c.unit == "PLN" for c in claims.values())
    assert reviewed_source_facts(context) == {}
    source = writer_input(context)
    assert source["source_narration"] == ["Narracja po polsku."]
    assert "WIZUALNIE" not in json.dumps(source)
    assert "ike_2026" not in draft_prompt(context, {})


def test_tax_facts_are_not_arithmetic_claims(store):
    context = context_for(store, 3)
    assert compiled_claims(context) == {}
    assert "ikze_withdrawal" in reviewed_source_facts(context)


def test_inflation_loss_uses_fisher_ratio_not_subtraction(store):
    claims = compiled_claims(context_for(store, 2))
    assert claims["real_purchasing_loss"].result == pytest.approx(2.857142857142858)
    assert claims["real_purchasing_loss"].spoken_result == "two point nine"


def test_bad_reviewed_calculation_is_not_silently_corrected(store):
    context = context_for(store)
    intake = context.artifacts["production_order"]["intake"]
    brief = json.loads(intake["corrected_brief"])
    brief["calculations"][0]["value"] = 999999
    intake["corrected_brief"] = json.dumps(brief)
    with pytest.raises(ValueError, match="disagrees"):
        compiled_claims(context)


def test_unknown_claim_id_cannot_be_filled_by_model_invention(store):
    with pytest.raises(ValueError, match="supplied calculation IDs"):
        bind_claims(context_for(store), {"segments": [{"claim_ids": ["invented"]}]})


def test_adaptation_decoding_disables_thinking_and_records_it(store, monkeypatch):
    context = context_for(store)
    async def model(settings, prompt, **kwargs):
        assert kwargs["think"] is False
        return {"title": "Example"}
    monkeypatch.setattr("atnos.production.steps.order_script.ask_ollama_json", model)
    asyncio.run(ask_model(store.settings, context, "Bounded editing request", AdaptedScript))
    record = json.loads(next((context.artifact_dir / "model_calls").glob("*.json")).read_text())
    assert record["thinking"] is False and record["status"] == "response_received"
