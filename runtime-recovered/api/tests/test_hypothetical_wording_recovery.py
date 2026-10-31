"""Regressions for same-order repair of a hypothetical rate misdescribed as an average."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from atnos.integrations import claim_evidence_gate
from atnos.production.hypothetical_script_intake import compile_supplied_plan
from atnos.production.order_execution import StageExecutor, produce_script
from atnos.production.order_store import OrderConflict, ProductionBlocked
from atnos.production.steps import order_script
from atnos.production.steps.common import write_json
from atnos.production.studio_script_recovery import (
    StudioScriptRepair,
    load_recovery_request,
    recovery_snapshot,
    register_script_repair,
)
from test_hypothetical_script_intake import order as hypothetical_order
from test_production_order_script import script
from test_production_order_store import store


BLOCKED_AVERAGE = (
    "If that ten dollars earned an average eight percent a year for thirty years, "
    "it could grow to roughly one hundred one dollars."
)
FIXED_ASSUMPTION = (
    "Assuming a fixed eight percent rate, compounded yearly for thirty years, ten dollars "
    "deposited once, with no additions, could become one hundred one dollars before fees, "
    "taxes, inflation. This is no promise."
)


def test_revision_inputs_keep_assumptions_but_do_not_repeat_historical_source_to_reviewer():
    from types import SimpleNamespace
    order = hypothetical_order().model_dump(mode='json')
    context = SimpleNamespace(artifacts={
        'production_order': order,
        'hypothetical_editorial_guidance': {
            'note': 'Old rejected phrase: ten dollars purchase.',
            'issues': [{'code': 'naturalness', 'segment_ids': ['s01'],
                        'detail': 'The old ten dollars purchase phrase was wrong.'}]},
    })
    writer = order_script.revision_input(context)
    reviewer = order_script.revision_input(context, reviewing=True)
    assert 'original_scenario' not in writer and 'direction' not in writer
    assert 'original_scenario' not in reviewer and 'direction' not in reviewer
    assert 'studio_editorial_request' in writer
    assert 'studio_editorial_request' not in reviewer
    assert writer['calculations'] == reviewer['calculations']
    assert reviewer['source_scenario_sha256'] == order['intake']['source_scenario_sha256']
    assert context.artifacts['production_order'] == order


def _finalized(payload: dict) -> dict:
    spoken = " ".join(segment["text"] for segment in payload["segments"])
    return {**payload, "text": spoken, "word_count": len(spoken.split()),
            "qa": {"status": "passed", "issues": [], "revision": 1}}


def _seed_blocked_average(store, script, *, text=BLOCKED_AVERAGE):
    """Create normal, hashed script/calculator checkpoints, then hit the real media gate."""
    order = hypothetical_order()
    job_id = store.create(order, request_key="hypothetical-average-recovery")["id"]
    state = store.get(job_id)
    intake = state["request"]["intake"]
    receipt = compile_supplied_plan(intake)
    source = script.model_dump(mode="json")
    source["claims"] = list(receipt["claims"].values())
    affected = source["segments"][4]
    affected.update(text=text, claim_ids=["c1"])
    final_script = _finalized(source)

    def checkpoint(token, stage, value, *, local_model_call=False):
        assert store.begin(job_id, token, stage, {"fixture_stage": stage},
                           local_model_call=local_model_call) is None
        filename = stage.replace("/", "-") + ".json"
        path = write_json(Path(state["artifact_dir"]) / "recovery-fixture" / filename, value).artifact_path
        store.complete(job_id, token, stage, value, [path])

    with store.lease(job_id) as token:
        checkpoint(token, "script_calculations", receipt)
        checkpoint(token, "script_draft", source, local_model_call=True)
        checkpoint(token, "script_review/0", {"status": "passed", "issues": [], "revision": 0},
                   local_model_call=True)
        checkpoint(token, "script_repair/1", source, local_model_call=True)
        checkpoint(token, "script_review/1", {"status": "passed", "issues": [], "revision": 1},
                   local_model_call=True)
        checkpoint(token, "script", final_script)
        with pytest.raises(ProductionBlocked, match="FACT_CHECK_BLOCKED"):
            store.begin(job_id, token, "asset_library", {"fixture": "actual hypothetical claim gate"})
    return job_id, receipt, final_script


@pytest.fixture
def blocked_average(store, script, monkeypatch):
    monkeypatch.setattr(claim_evidence_gate, "data_root", lambda root=None: store.settings.data_dir)
    return _seed_blocked_average(store, script)


def test_real_asset_gate_average_only_is_recoverable_and_registration_preserves_order_evidence(
        store, blocked_average):
    job_id, receipt, source = blocked_average
    before = store.get(job_id)
    assert before["status"] == "blocked" and before["current_step"] == "asset_library"
    assert json.loads(before["error"])["category"] == "FACT_CHECK_BLOCKED"
    assert json.loads(before["error"])["blocked_claims"][0]["exact_text"] == BLOCKED_AVERAGE

    snapshot = recovery_snapshot(store, job_id)
    assert snapshot["kind"] == "script" and snapshot["can_repair_script"]
    assert snapshot["issues"][0]["code"] == "hypothetical_assumption_wording"
    assert snapshot["issues"][0]["segment_ids"] == ["s05"]

    registration = register_script_repair(store, job_id, StudioScriptRepair(
        request_key="average-wording-repair-001",
        note="Describe eight percent as a fixed hypothetical annual rate, not an average. State one deposit, annual compounding, the exclusions, and that this is no promise."))
    after = store.get(job_id)
    assert registration["request"]["mode"] == "hypothetical_review"
    assert registration["request"]["hypothetical_evidence"]["calculator_receipt"] == receipt
    assert registration["request"]["hypothetical_evidence"]["source_script"] == {
        key: source[key] for key in ("title", "description", "segments", "claims")}
    assert registration["request"]["hypothetical_evidence"]["source_issues"][0]["code"] == "hypothetical_assumption_wording"
    assert after["id"] == before["id"] == job_id
    assert after["request"] == before["request"] and after["profile_hash"] == before["profile_hash"]
    assert after["provider_calls"] == before["provider_calls"]
    assert after["reserved_cost_units"] == before["reserved_cost_units"]
    assert [(row["stage"], row["attempts"]) for row in after["checkpoints"]] == [
        (row["stage"], row["attempts"]) for row in before["checkpoints"]]
    archived = load_recovery_request(store, job_id)
    for item in archived["archive"]:
        path = Path(after["artifact_dir"]) / item["archive_path"]
        assert path.is_file()
        from atnos.production.order_store import file_hash
        assert file_hash(path) == item["sha256"]
    assert archived["attempts_reset"] is False and archived["budget_reset"] is False


@pytest.mark.parametrize('origin',['studio_user','autonomous_recovery'])
def test_recovery_runs_atnos_model_repair_and_rechecks_the_real_gate(
        store, blocked_average, monkeypatch,origin):
    job_id, receipt, _ = blocked_average
    note = "Use a fixed assumed rate and state that this is one deposit, not a guarantee."
    register_script_repair(store, job_id, StudioScriptRepair(
        request_key="average-model-repair-001", note=note),origin=origin)
    before_model = store.get(job_id)
    calls = []

    async def controlled_model(settings, prompt, **kwargs):
        calls.append((prompt, kwargs))
        if '"passed":true|false' in prompt:
            return {"passed": True, "issues": []}
        original = next(
            row for row in store.get(job_id)["checkpoints"]
            if row["stage"] == "script"
        )["output"]["segments"][4]
        return {"replacements": [{**original, "text": FIXED_ASSUMPTION}]}

    monkeypatch.setattr(order_script, "ask_ollama_json", controlled_model)
    monkeypatch.setattr(order_script, "gather_script_context", lambda *_: {
        "reference_source": {"path": "controlled-test-profile.json", "sha256": "a" * 64},
        "usage": {"reference_samples": 0, "knowledge_matches": 0},
        "reference_analysis": {"sample_count": 0},
    })
    monkeypatch.setattr(claim_evidence_gate, "data_root", lambda root=None: store.settings.data_dir)
    with store.lease(job_id) as token:
        result = asyncio.run(produce_script(StageExecutor(store, job_id, token)))
        assert result["segments"][4]["text"] == FIXED_ASSUMPTION
        assert result["claims"][0]["id"] == "c1"
        assert result["claims"][0]["operands"] == [10, .08, 30]
        assert result["claims"][0]["result"] == pytest.approx(100.6265688907346)
        assert result["qa"]["status"] == "passed"
        assert store.begin(job_id, token, "asset_library", {"fixture": "verify fixed script passes"}) is None
        gate_path = Path(store.context(job_id).artifact_dir) / "asset_library.json"
        store.complete(job_id, token, "asset_library", {"gate": "passed"},
                               [write_json(gate_path, {"gate": "passed"}).artifact_path])

    assert len(calls) == (2 if origin=='studio_user' else 3)
    repair_index=0 if origin=='studio_user' else 1
    if origin=='autonomous_recovery':
        assert '"passed":true|false' in calls[0][0]  # Review actual source first; its financial defect still blocks.
    assert '"passed":true|false' not in calls[repair_index][0]
    assert '"passed":true|false' in calls[-1][0]
    repair_prefix='script_repair/studio-' if origin=='studio_user' else 'script_repair/1'
    assert any(row['stage'].startswith(repair_prefix) and row['status'] == 'done'
               for row in store.get(job_id)['checkpoints'])
    assert any("hypothetical_assumption_wording" in prompt for prompt, _ in calls)
    state = store.get(job_id)
    assert state["id"] == job_id and state["request"] == before_model["request"]
    assert state["profile_hash"] == before_model["profile_hash"]
    assert state["provider_calls"] > before_model["provider_calls"] and state["reserved_cost_units"] == 0
    assert state["status"] != "ready"  # This test repairs/checks the script gate only; it never produces media.
    passed = claim_evidence_gate.ensure_media_claim_gate(
        project="wealth_blueprint", production_id=job_id, stage="asset_library",
        script=result, root=store.settings.data_dir,
        hypothetical_intake=state["request"]["intake"], calculation_receipt=receipt)
    assert passed["blocked"] is False


@pytest.mark.parametrize("text", [
    "If ten dollars earned an assumed nine percent annually for thirty years, it could become one hundred one dollars.",
    "If ten dollars earned an assumed eight percent annually for thirty years, it could become two hundred dollars.",
    "If ten zlotys earned an assumed eight percent annually for thirty years, it could become one hundred one zlotys.",
    "Historically, ten dollars earned an average eight percent a year for thirty years and could become one hundred one dollars.",
    "A guaranteed return of one hundred one dollars follows from ten dollars at an assumed eight percent annually.",
])
def test_non_wording_hypothetical_claim_defects_do_not_enable_recovery(store, script,
                                                                        monkeypatch, text):
    monkeypatch.setattr(claim_evidence_gate, "data_root", lambda root=None: store.settings.data_dir)
    job_id, _, _ = _seed_blocked_average(store, script, text=text)
    snapshot = recovery_snapshot(store, job_id)
    assert not snapshot["can_repair_script"]
    with pytest.raises(OrderConflict):
        register_script_repair(store, job_id, StudioScriptRepair(
            request_key="unsafe-claim-recovery-001", note="Do not change evidence."))


@pytest.mark.parametrize("tamper", ["script", "receipt"])
def test_tampered_hypothetical_checkpoint_evidence_is_integrity_blocked(
        store, script, monkeypatch, tamper):
    monkeypatch.setattr(claim_evidence_gate, "data_root", lambda root=None: store.settings.data_dir)
    job_id, _, _ = _seed_blocked_average(store, script)
    state = store.get(job_id)
    stage = "script" if tamper == "script" else "script_calculations"
    row = next(item for item in state["checkpoints"] if item["stage"] == stage)
    path = Path(state["artifact_dir"]) / row["files"][0]["path"]
    path.write_bytes(path.read_bytes() + b" ")
    snapshot = recovery_snapshot(store, job_id)
    assert snapshot["kind"] == "integrity" and not snapshot["can_repair_script"]
    with pytest.raises(OrderConflict):
        register_script_repair(store, job_id, StudioScriptRepair(
            request_key="tampered-claim-recovery-001", note="Do not use altered evidence."))
