from __future__ import annotations

import asyncio
import copy
import json

import pytest

from atnos.intelligence.storage import connect
from atnos.production.finance_four_intake import BATCH_ID, prepare_batch, probe_held_workers, split_scenarios
from atnos.production.order_dispatch import ProductionOrderDispatcher, order_status
from atnos.production.order_execution import run_order
from atnos.production.order_intake import ScriptIntake
from atnos.production.order_store import OrderConflict, ProductionBlocked
from atnos.production.project_profile import ProductionOrder, content_hash, load_project_profile, profile_for_order
from test_production_order_store import create, store


def intake(**changes):
    fields = dict(source_attachment_sha256="a" * 64, factcheck_sha256="b" * 64,
                  scenario_index=1, requested_language="en", target_duration_seconds=60,
                  original_scenario="Original Polish scenario, preserved without any generation.",
                  corrected_brief="Reviewed finance corrections, not a production QA approval.")
    return ScriptIntake(**(fields | changes))


def sources(tmp_path):
    attachment = tmp_path / "scenarios.txt"
    attachment.write_text("\n".join(f"Temat: test ~{n} sek.\nA sufficiently long original script to preserve." for n in (60, 50, 75, 55)), encoding="utf-8")
    facts = tmp_path / "facts.json"
    facts.write_text(json.dumps({"language": "en", "calculations": [{"value": 149035.94}],
        "verified_external_facts": [{"source": "https://www.gov.pl/web/rodzina/ike-limit-wplat"}],
        "recipient": "test@example.com"}))
    return attachment, facts


def test_four_durable_ids_idempotent_and_no_spend(store, tmp_path):
    attachment, facts = sources(tmp_path)
    legacy = create(store)
    first = prepare_batch(store.settings, attachment, facts)
    second = prepare_batch(store.settings, attachment, facts)
    assert first["batch_id"] == second["batch_id"] == BATCH_ID
    assert len(set(first["job_ids"])) == 4
    assert first["job_ids"] == second["job_ids"]
    assert store.get(legacy["id"]) == legacy
    assert [entry["target_duration_seconds"] for entry in first["orders"]] == [60, 50, 75, 55]
    for job in first["job_ids"]:
        state = store.get(job)
        assert state["status"] == "blocked" and state["current_step"] == "intake_review"
        assert not state["can_resume"] and not state["worker_live"]
        assert state["reserved_cost_units"] == state["provider_calls"] == 0
        assert state["checkpoints"] == [] and state["upload_package_path"] is None
        assert state["request"]["intake"]["delivery_mode"] == "local_only"


def test_changed_sources_do_not_duplicate_or_modify_orders(store, tmp_path):
    attachment, facts = sources(tmp_path)
    manifest = prepare_batch(store.settings, attachment, facts)
    before = [store.get(job) for job in manifest["job_ids"]]
    facts.write_text(facts.read_text() + " ")
    with pytest.raises(OrderConflict, match="sources changed"):
        prepare_batch(store.settings, attachment, facts)
    assert [store.get(job) for job in manifest["job_ids"]] == before


def test_hold_prevents_dispatch_direct_worker_and_lease_without_calls(store, monkeypatch):
    job = store.create(ProductionOrder(topic="Supplied scenario", intake=intake()))["id"]
    def forbidden(*args, **kwargs):
        pytest.fail("Held intake attempted execution")
    monkeypatch.setattr("atnos.production.order_dispatch.subprocess.Popen", forbidden)
    monkeypatch.setattr("atnos.production.order_execution.preflight_order", forbidden)
    result = ProductionOrderDispatcher(store.settings).dispatch(job)
    assert result["worker_requested"] is False
    assert asyncio.run(run_order(store.settings, job))["status"] == "blocked"
    with pytest.raises(ProductionBlocked, match="supplied_script_contract_pending"):
        with store.lease(job):
            pytest.fail("Held intake acquired execution ownership")
    assert store.get(job)["checkpoints"] == []


@pytest.mark.parametrize("duration", [50, 55, 60, 75])
def test_duration_override_scoped_and_limits_unchanged(store, duration):
    base = load_project_profile(store.settings, "wealth_blueprint")
    original = copy.deepcopy(base)
    order = ProductionOrder(topic="Scoped duration", intake=intake(target_duration_seconds=duration))
    scoped = profile_for_order(base, order)
    assert base == original
    assert scoped["production"]["duration_seconds"] == [duration - 2, duration + 2]
    for key in ("max_cost_usd", "max_provider_calls", "max_attempts_per_element", "required_gates", "voice", "publish"):
        assert scoped["production"][key] == base["production"][key]
    assert store.create(order)["profile"] == scoped | {"feedback_rules": []}


def test_legacy_idempotency_hash_unchanged(store):
    order = ProductionOrder(topic="Legacy exact hash")
    old_request = order.model_dump(mode="json", exclude={"intake", "delivery_mode"})
    state = store.create(order, request_key="legacy-compatible")
    with connect(store.settings) as db:
        row = db.execute("SELECT request_hash,request_json FROM pipeline_job_orders WHERE job_id=?", (state["id"],)).fetchone()
    assert row["request_hash"] == content_hash(old_request)
    assert json.loads(row["request_json"]) == old_request


def test_malformed_batch_rejected_before_any_order_creation(store, tmp_path):
    attachment, facts = sources(tmp_path)
    attachment.write_text(attachment.read_text().replace("~75", "~40"))
    with pytest.raises(ValueError, match="75-second"):
        prepare_batch(store.settings, attachment, facts)
    with connect(store.settings) as db:
        assert db.execute("SELECT COUNT(*) FROM pipeline_job_orders").fetchone()[0] == 0


def test_worker_probes_serial_and_unchanged_held_states(store, tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    attachment, facts = sources(tmp_path)
    batch = prepare_batch(store.settings, attachment, facts)
    calls = []
    def worker(command, **kwargs):
        calls.append(command[-1])
        assert command[-2] == "run"
        return SimpleNamespace(returncode=2, stdout=json.dumps(order_status(store.get(command[-1]))), stderr="")
    monkeypatch.setattr("atnos.production.finance_four_intake.subprocess.run", worker)
    result = probe_held_workers(store.settings, Path(batch["manifest_path"]))
    assert calls == batch["job_ids"]
    assert len(result["workers"]) == 4
    assert all(row["exit_code"] == 2 and row["status"] == "blocked" for row in result["workers"])


def supported_intake():
    from pathlib import Path
    facts = json.loads((Path(__file__).parents[1] / "config" / "finance_four_factcheck_20260906.json").read_text())
    return intake(corrected_brief=json.dumps(facts),
                  fact_source_urls=sorted({fact["source"] for fact in facts["verified_external_facts"]}))


def test_supported_adaptation_runs_local_stage_but_script_preview_cannot_spend(store):
    job = store.create(ProductionOrder(topic="English supplied scenario", intake=supported_intake()))["id"]
    assert store.get(job)["can_resume"] and store.get(job)["can_prepare_script"]
    with store.lease(job, script_only=True) as token:
        assert store.begin(job, token, "script_context", {}) is None
        with pytest.raises(ProductionBlocked, match="Script-only"):
            store.begin(job, token, "voice/s01", {}, provider_call=True, estimated_cost_usd=.1)
    assert store.get(job)["reserved_cost_units"] == 0
    with store.lease(job) as token:
        assert store.begin(job, token, "asset_library", {}) is None


@pytest.mark.parametrize("defect", ["language", "source", "duplicate_id"])
def test_unsupported_adaptation_remains_held(store, defect):
    contract = supported_intake()
    brief = json.loads(contract.corrected_brief)
    if defect == "language":
        contract.requested_language = "pl"
    elif defect == "source":
        brief["verified_external_facts"][0]["source"] = "https://www.gov.pl.evil.example/fact"
    else:
        brief["verified_external_facts"].append(brief["verified_external_facts"][0])
    contract.corrected_brief = json.dumps(brief)
    job = store.create(ProductionOrder(topic="Unsupported supplied scenario", intake=contract))["id"]
    assert not store.get(job)["can_resume"]
    with pytest.raises(ProductionBlocked):
        with store.lease(job):
            pytest.fail("An invalid source was admitted")


def test_valid_intake_dispatch_does_not_duplicate_live_worker(store, monkeypatch):
    job = store.create(ProductionOrder(topic="Live supplied scenario", intake=supported_intake()))["id"]
    def forbidden(*args, **kwargs):
        pytest.fail("A second worker was launched")
    monkeypatch.setattr("atnos.production.order_dispatch.subprocess.Popen", forbidden)
    with store.lease(job):
        assert not ProductionOrderDispatcher(store.settings).dispatch(job).get("worker_requested", False)


def test_batch_lease_excludes_concurrent_runner_and_is_released(store):
    from atnos.production.finance_four_intake import batch_lease
    with batch_lease(store.settings):
        with pytest.raises(OrderConflict, match="live production worker"):
            with batch_lease(store.settings):
                pytest.fail("Concurrent batch acquired the same lease")
    with batch_lease(store.settings):
        pass


def test_batch_serial_execution_and_input_integrity(store, tmp_path, monkeypatch):
    from pathlib import Path
    from atnos.production.finance_four_intake import run_batch
    attachment, _ = sources(tmp_path)
    factcheck = Path(__file__).parents[1] / "config" / "finance_four_factcheck_20260906.json"
    batch = prepare_batch(store.settings, attachment, factcheck)
    calls = []
    active = 0
    async def worker(settings, job_id, *, script_only):
        nonlocal active
        active += 1
        assert active == 1 and not script_only
        await asyncio.sleep(0)
        calls.append(job_id)
        active -= 1
        return store.get(job_id)
    monkeypatch.setattr("atnos.production.order_execution.run_order", worker)
    path = Path(batch["manifest_path"])
    result = asyncio.run(run_batch(store.settings, path))
    assert calls == batch["job_ids"]
    assert result["job_ids"] == batch["job_ids"]
    assert all(store.get(job)["provider_calls"] == 0 for job in calls)
    batch["job_ids"].reverse()
    path.write_text(json.dumps(batch))
    calls.clear()
    with pytest.raises(OrderConflict, match="immutable orders"):
        asyncio.run(run_batch(store.settings, path))
    assert calls == []
