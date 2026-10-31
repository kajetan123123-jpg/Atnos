from __future__ import annotations

import asyncio
import json

import pytest

from atnos.integrations import claim_evidence_gate as gate
from atnos.production.hypothetical_script_intake import compile_supplied_plan, make_hypothetical_intake, produce_supplied_calculations
from atnos.production.order_execution import StageExecutor
from atnos.production.order_store import ProductionBlocked
from atnos.production.steps.order_script import EditorialScript
from test_hypothetical_script_intake import SOURCE, brief, contract, order
from test_production_order_script import script
from test_production_order_store import store


@pytest.fixture
def supplied_script(script):
    payload = script.model_dump(mode="json")
    payload["claims"] = list(compile_supplied_plan(contract().model_dump(mode="json"))["claims"].values())
    payload["segments"][4].update(
        text="If ten dollars earned an assumed eight percent annually for thirty years, this hypothetical example reaches about one hundred one dollars.",
        claim_ids=["c1"],
    )
    return EditorialScript.model_validate(payload).model_dump(mode="json")


def check(tmp_path, script, *, intake=None, receipt=None):
    intake = intake or contract().model_dump(mode="json")
    receipt = compile_supplied_plan(intake) if receipt is None else receipt
    return gate.ensure_media_claim_gate(project="wealth_blueprint", production_id="supplied-calculation-test",
        stage="asset_library", script=script, root=tmp_path, hypothetical_intake=intake, calculation_receipt=receipt)


def test_actual_calculator_receipt_allows_conditional_hypothetical_without_research(tmp_path, supplied_script, monkeypatch):
    monkeypatch.setattr(gate, "research_claim", lambda *a, **k: pytest.fail("Hypothetical evidence must not call research"))
    result = check(tmp_path, supplied_script)
    assert result["blocked"] is False
    ledger = gate.load_ledger(tmp_path, "wealth_blueprint", "supplied-calculation-test")
    binding = ledger["calculator_binding"]
    assert binding["claims"]["c1"]["result"] == pytest.approx(100.6265688907346)
    assert binding["evidence_kind"] == "verified_hypothetical_calculation"
    assert binding["source_scenario_sha256"] == contract().source_scenario_sha256
    assert binding["human_review"] == "not_performed"
    numeric = next(row for row in ledger["claims"] if row["script_location"]["segment_id"] == "s05")
    assert numeric["fact_check_status"] == "NOT_REQUIRED"
    assert numeric["source_status"] == "NONE" and numeric["evidence"] == []
    assert numeric["calculator_evidence"]["claim_ids"] == ["c1"]
    assert "Annual compounding" in numeric["calculator_evidence"]["assumptions"][0]
    assert not ledger["brain_writes"]


@pytest.mark.parametrize("defect", ["rate", "result", "claim_id", "missing_claim", "external_fact_id"])
def test_changed_script_claim_or_evidence_id_cannot_reuse_receipt(tmp_path, supplied_script, defect):
    if defect == "rate":
        supplied_script["claims"][0]["operands"][1] = .09
    elif defect == "result":
        supplied_script["claims"][0]["result"] = 1000
    elif defect == "claim_id":
        supplied_script["segments"][4]["claim_ids"] = ["c2"]
    elif defect == "missing_claim":
        supplied_script["claims"] = []
    else:
        supplied_script["segments"][4]["fact_ids"] = ["market_average"]
    result = check(tmp_path, supplied_script)
    assert result["blocked"] and result["evidence_kind"] == "hypothetical_binding_failed"


@pytest.mark.parametrize("defect", ["rate", "source_digest", "brief_digest", "result"])
def test_receipt_matches_exact_original_assumptions_and_source(tmp_path, supplied_script, defect):
    receipt = compile_supplied_plan(contract().model_dump(mode="json"))
    if defect == "rate":
        receipt["claims"]["c1"]["operands"][1] = .09
    elif defect == "source_digest":
        receipt["source_scenario_sha256"] = "0" * 64
    elif defect == "brief_digest":
        receipt["reviewed_brief_sha256"] = "0" * 64
    else:
        receipt["claims"]["c1"]["result"] = 1000
    assert check(tmp_path, supplied_script, receipt=receipt)["blocked"] is True


@pytest.mark.parametrize("text", [
    "For example, the stock market historically returns eight percent annually.",
    "The S&P five hundred averages eight percent each year.",
    "An investment guarantees one hundred one dollars after thirty years.",
    "For example, ten dollars could become two hundred dollars with the assumed return.",
    "Ten dollars earns eight percent annually and becomes one hundred one dollars after thirty years.",
    "If ten dollars earned an assumed eight percent annually for forty years, it could become one hundred one dollars.",
    "If ten zlotys earned an assumed eight percent annually for thirty years, it could become one hundred one zlotys.",
])
def test_real_market_claim_wrong_amount_time_currency_or_guarantee_stays_blocked(tmp_path, supplied_script, text, monkeypatch):
    monkeypatch.setattr(gate, "research_claim", lambda *a, **k: pytest.fail("No invented or paid research for a hypothetical input"))
    supplied_script["segments"][4]["text"] = text
    result = check(tmp_path, supplied_script)
    assert result["blocked"] is True
    assert result["blocked_claims"]


def test_stale_pass_ledger_cannot_hide_a_current_false_market_claim(tmp_path, supplied_script):
    assert check(tmp_path, supplied_script)["blocked"] is False
    supplied_script["segments"][4]["text"] = "Historically, the stock market returns eight percent annually."
    assert check(tmp_path, supplied_script)["blocked"] is True


def test_plain_hook_principal_is_bound_without_claiming_a_return(tmp_path, supplied_script):
    supplied_script["segments"][0].update(text="This ten dollar purchase has an opportunity cost.", claim_ids=["c1"])
    assert check(tmp_path, supplied_script)["blocked"] is False


def test_roughly_one_hundred_is_allowed_only_when_the_compiler_records_that_rounding(tmp_path, supplied_script):
    data = brief()
    data["calculation_plan"]["examples"][0]["spoken_rounding"] = 10
    intake = make_hypothetical_intake(SOURCE, data, target_duration_seconds=44).model_dump(mode="json")
    receipt = compile_supplied_plan(intake)
    assert receipt["claims"]["c1"]["result"] == pytest.approx(100.6265688907346)
    assert receipt["claims"]["c1"]["spoken_result"] == "one hundred"
    supplied_script["claims"] = list(receipt["claims"].values())
    supplied_script["segments"][4]["text"] = "If ten dollars earned an assumed eight percent annually for thirty years, this hypothetical example reaches roughly one hundred dollars."
    assert check(tmp_path, supplied_script, intake=intake, receipt=receipt)["blocked"] is False


def test_normal_order_media_begin_uses_unchanged_frozen_intake_and_intact_receipt(store, supplied_script, monkeypatch):
    monkeypatch.setattr(gate, "data_root", lambda root=None: store.settings.data_dir)
    monkeypatch.setattr(gate, "research_claim", lambda *a, **k: pytest.fail("No research needed for the actual hypothetical example"))
    job = store.create(order())["id"]
    with store.lease(job) as token:
        executor = StageExecutor(store, job, token)
        asyncio.run(produce_supplied_calculations(executor))
        assert store.begin(job, token, "script", {"test": "normal-script"}) is None
        path = executor.context.artifact_dir / "script.json"
        path.write_text(json.dumps(supplied_script), encoding="utf-8")
        store.complete(job, token, "script", supplied_script, [path])
        assert store.begin(job, token, "asset_library", {"test": "media"}) is None


def test_modified_receipt_file_blocks_normal_media_stage_even_if_database_json_is_intact(store, supplied_script, monkeypatch):
    monkeypatch.setattr(gate, "data_root", lambda root=None: store.settings.data_dir)
    job = store.create(order())["id"]
    with store.lease(job) as token:
        executor = StageExecutor(store, job, token)
        asyncio.run(produce_supplied_calculations(executor))
        assert store.begin(job, token, "script", {"test": "normal-script"}) is None
        path = executor.context.artifact_dir / "script.json"
        path.write_text(json.dumps(supplied_script), encoding="utf-8")
        store.complete(job, token, "script", supplied_script, [path])
        (executor.context.artifact_dir / "script_calculations.json").write_text("{}", encoding="utf-8")
        with pytest.raises(ProductionBlocked, match="FACT_CHECK_BLOCKED"):
            store.begin(job, token, "asset_library", {"test": "media"})


def test_non_hypothetical_gate_behavior_is_not_relaxed(tmp_path):
    result = gate.ensure_media_claim_gate(project="wealth_blueprint", production_id="unrelated-market",
        stage="rendering", root=tmp_path,
        script={"segments": [{"id": "s01", "type": "explanation", "text": "Most Americans cannot cover a $400 emergency expense in cash."}]})
    assert result["blocked"] is True


def test_average_wording_is_detected_before_media_but_not_accepted(supplied_script):
    from atnos.production.hypothetical_claim_evidence import script_binding_issues
    supplied_script["segments"][4]["text"] = "If ten dollars earned an average eight percent annually for thirty years, it could become about one hundred one dollars."
    intake = contract().model_dump(mode="json")
    issues = script_binding_issues(supplied_script, intake, compile_supplied_plan(intake))
    assert [(item["code"], item["segment_ids"]) for item in issues] == [("hypothetical_assumption_wording", ["s05"])]
    assert issues[0]["fields"] == ["text"]


@pytest.mark.parametrize("text", [
    "If ten dollars earned an average nine percent annually for thirty years, it could become about one hundred one dollars.",
    "If ten dollars earned an average eight percent annually for forty years, it could become about one hundred one dollars.",
    "If ten zlotys earned an average eight percent annually for thirty years, it could become about one hundred one zlotys.",
    "Historically the market returns an average eight percent annually, making ten dollars one hundred one dollars.",
    "If ten dollars earned an average eight percent annually for thirty years, it guarantees one hundred one dollars.",
])
def test_average_wording_does_not_excuse_other_evidence_faults(supplied_script, text):
    from atnos.production.hypothetical_claim_evidence import script_binding_issues
    supplied_script["segments"][4]["text"] = text
    intake = contract().model_dump(mode="json")
    issues = script_binding_issues(supplied_script, intake, compile_supplied_plan(intake))
    assert issues and {item["code"] for item in issues} == {"hypothetical_evidence"}
