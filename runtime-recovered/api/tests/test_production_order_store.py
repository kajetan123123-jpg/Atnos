from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from atnos.intelligence.storage import connect
from atnos.production.order_store import OrderConflict, ProductionBlocked, ProductionOrderStore, cost_units
from atnos.production.project_profile import ProductionOrder, load_project_profile
from test_support import build_test_settings


@pytest.fixture
def store(tmp_path):
    settings = build_test_settings(tmp_path)
    with connect(settings) as db:
        db.execute("INSERT INTO intelligence_accounts(account_id,niches,region,last_requested_at) VALUES('yt-wealth-blueprint','[\"finance\"]','US','2026-09-06T00:00:00Z')")
        db.commit()
    return ProductionOrderStore(settings)


def create(store):
    return store.create(ProductionOrder(topic="Build your first savings", direction="Practical and confident"))


def complete_file(store, job, token, stage, value="first", inputs=None):
    inputs = inputs or {"revision": 1}
    assert store.begin(job, token, stage, inputs) is None
    path = store.context(job).artifact_dir / f"{stage}.json"
    path.write_text(json.dumps({"value": value}), encoding="utf-8")
    output = {"value": value, "file": str(path)}
    store.complete(job, token, stage, output, [path])
    return output


def test_one_order_uses_existing_job_and_snapshots_profile(store):
    job = store.create(ProductionOrder(project="Wealth Blueprint", topic="A simple savings plan"), request_key="request-1")
    assert job["status"] == "queued"
    assert job["profile"]["production"]["voice"]["provider"] == "elevenlabs"
    assert job["profile"]["production"]["publish"] is False
    assert job["profile"]["production"]["minimum_practical_share"] >= 1 / 3
    with connect(store.settings) as db:
        assert db.execute("SELECT COUNT(*) FROM pipeline_jobs").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM topic_queue WHERE status='in_production'").fetchone()[0] == 1
    again = store.create(ProductionOrder(project="Wealth Blueprint", topic="A simple savings plan"), request_key="request-1")
    assert again["id"] == job["id"]
    with pytest.raises(OrderConflict):
        store.create(ProductionOrder(topic="Another topic"), request_key="request-1")


def test_topic_brief_can_prepare_script_without_media_or_model_calls(store, monkeypatch):
    import asyncio
    from atnos.production import order_execution

    job = create(store)["id"]
    assert store.get(job)["can_prepare_script"] is True
    seen = []

    async def isolated_preflight(executor, **kwargs):
        seen.append((executor.token, kwargs))
        raise ProductionBlocked("Isolated stop before models, GPU or media")

    monkeypatch.setattr(order_execution, "preflight_order", isolated_preflight)
    result = asyncio.run(order_execution.run_order(store.settings, job, script_only=True))
    assert seen[0][0].startswith("script-only:")
    assert seen[0][1] == {"script_only": True}
    assert result["provider_calls"] == 0 and result["reserved_cost_units"] == 0
    assert not store.get(job)["worker_live"] and not result["checkpoints"]


def test_prepare_script_does_not_admit_an_existing_live_worker(store):
    job = create(store)["id"]
    with store.lease(job, script_only=True):
        assert store.get(job)["can_prepare_script"] is False


def test_script_only_lease_allows_counted_studio_repair_local_inference(store, monkeypatch):
    monkeypatch.setattr("atnos.integrations.claim_evidence_gate.ensure_media_claim_gate",
                        lambda **_: {"blocked": False})
    job = create(store)["id"]
    stage = "script_repair/studio-012345abcdef"
    with store.lease(job, script_only=True) as token:
        assert store.begin(job, token, stage, {"revision": 1}, local_model_call=True) is None
        state = store.get(job)
        assert state["provider_calls"] == 1
        row = next(checkpoint for checkpoint in state["checkpoints"] if checkpoint["stage"] == stage)
        assert row["status"] == "running" and row["attempts"] == 1
        assert state["reserved_cost_units"] == 0


@pytest.mark.parametrize(("stage", "kwargs"), [
    ("script_repair/studio-nothexchars", {}),
    ("script_repair/studio-012345abcde", {}),
    ("asset_planning", {}),
    ("script_repair/studio-012345abcdef", {"provider_call": True, "estimated_cost_usd": 0.25}),
    ("script_repair/studio-012345abcdef", {"external_tool_call": True}),
    ("script_repair/studio-012345abcdef", {"estimated_cost_usd": 0.25}),
])
def test_script_only_lease_rejects_invalid_stages_and_paid_or_media_work_before_accounting(store, stage, kwargs):
    job = create(store)["id"]
    before = store.get(job)
    with store.lease(job, script_only=True) as token:
        with pytest.raises(ProductionBlocked, match="Script-only admission"):
            store.begin(job, token, stage, {"revision": 1}, **kwargs)
    after = store.get(job)
    assert (after["provider_calls"], after["reserved_cost_units"]) == (
        before["provider_calls"], before["reserved_cost_units"])
    assert not any(row["stage"] == stage for row in after["checkpoints"])


def test_editable_profile_only_affects_future_orders(store):
    first = create(store)
    source = Path(__file__).parents[1] / "config/channel_profiles/wealth_blueprint.json"
    changed = json.loads(source.read_text())
    changed["production"]["voice"]["speed"] = 1.05
    override = store.settings.data_dir / "production-profiles/wealth_blueprint.json"
    override.parent.mkdir(parents=True)
    override.write_text(json.dumps(changed))
    second = create(store)
    assert first["profile_hash"] != second["profile_hash"]
    assert store.get(first["id"])["profile"]["production"]["voice"]["speed"] == 1.12
    assert second["profile"]["production"]["voice"]["speed"] == 1.05


def test_local_delivery_scope_is_durable_without_changing_legacy_request_hash(store):
    order = ProductionOrder(topic="Unchanged legacy order")
    first = store.create(order, request_key="legacy-scope")
    assert "delivery_mode" not in first["request"]
    assert store.create(order, request_key="legacy-scope")["id"] == first["id"]
    scoped = ProductionOrder(topic=order.topic, delivery_mode="local_only")
    with pytest.raises(OrderConflict):
        store.create(scoped, request_key="legacy-scope")
    local = store.create(scoped, request_key="new-local-scope")
    assert ProductionOrderStore(store.settings).get(local["id"])["request"]["delivery_mode"] == "local_only"


@pytest.mark.parametrize("project", ["../secrets", "E:/outside", "unknown"])
def test_project_cannot_select_arbitrary_profile_path(store, project):
    with pytest.raises(ValueError):
        load_project_profile(store.settings, project)


def test_live_worker_cannot_be_stolen_even_by_same_process(store):
    job = create(store)["id"]
    with store.lease(job):
        assert store.get(job)["worker_live"] is True
        with pytest.raises(OrderConflict):
            with ProductionOrderStore(store.settings).lease(job):
                pytest.fail("Live lease was stolen")
    assert store.get(job)["worker_live"] is False


def test_explicit_call_extension_preserves_costs_attempts_and_frozen_profile(store, monkeypatch):
    created = create(store)
    job = created["id"]
    _prepare_test_narrative_gate(store, job, monkeypatch)
    with connect(store.settings) as db:
        import json
        row = db.execute("SELECT profile_json FROM pipeline_job_orders WHERE job_id=?", (job,)).fetchone()
        profile = json.loads(row["profile_json"])
        profile["production"]["max_provider_calls"] = 30
        profile["production"]["max_cost_usd"] = 2.0
        db.execute(
            "UPDATE pipeline_job_orders SET profile_json=?, provider_calls=30, reserved_cost_units=1000000 WHERE job_id=?",
            (json.dumps(profile, ensure_ascii=False), job),
        )
        db.commit()
    before = store.get(job)
    with store.lease(job) as token:
        with pytest.raises(ProductionBlocked,match="provider_call_limit"):
            store.begin(job,token,"semantic/s01",{},local_model_call=True)
        with pytest.raises(OrderConflict,match="active"):
            store.authorize_call_limit(job,45,reason="User explicitly approved forty-five calls")
    after = store.authorize_call_limit(job,45,reason="User explicitly approved forty-five calls")
    assert after["profile_hash"] == before["profile_hash"]
    assert after["profile"] == before["profile"]
    assert after["reserved_cost_units"] == 1000000
    assert after["provider_calls"] == 30 and after["can_resume"]
    with store.lease(job) as token:
        assert store.begin(job,token,"semantic/s01",{},local_model_call=True) is None
        assert store.get(job)["provider_calls"] == 31
        with pytest.raises(ProductionBlocked,match="cost_limit"):
            store.begin(job,token,"voice/s02",{},provider_call=True,estimated_cost_usd=1.1)
    assert store.authorize_call_limit(job,45,reason="User explicitly approved forty-five calls")["error"] == "cost_limit"
    for invalid in (True, 100001, 0, 30):
        with pytest.raises(ValueError):
            store.authorize_call_limit(job,invalid,reason="User explicitly approved forty-five calls")


def test_restart_after_real_process_exit_keeps_completed_stage(store):
    job = create(store)["id"]
    script = """
import os,sys,json
from pathlib import Path
from test_support import build_test_settings
from atnos.production.order_store import ProductionOrderStore
s=ProductionOrderStore(build_test_settings(Path(sys.argv[1])))
job=sys.argv[2]
with s.lease(job) as token:
    s.begin(job, token, 'script', {'revision':1})
    path=s.context(job).artifact_dir/'script.json'
    path.write_text('{"text":"Completed before crash"}')
    s.complete(job,token,'script',{'text':'Completed before crash'},[path])
    s.begin(job, token, 'voice', {'revision':1})
    os._exit(0)
"""
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(Path(__file__).parent), str(Path(__file__).parents[1] / "src")])}
    result = subprocess.run([sys.executable, "-c", script, str(store.settings.app_root), job], env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    restarted = ProductionOrderStore(store.settings)
    assert restarted.get(job)["can_resume"] is True
    with restarted.lease(job) as token:
        assert restarted.begin(job, token, "script", {"revision": 1}) == {"text": "Completed before crash"}
        complete_file(restarted, job, token, "voice", "resumed")
    attempts = {s["stage"]: s["attempts"] for s in restarted.get(job)["checkpoints"]}
    assert attempts == {"script": 1, "voice": 2}


def test_corrupt_asset_repairs_only_that_element(store):
    job = create(store)["id"]
    with store.lease(job) as token:
        first = complete_file(store, job, token, "scene_a")
        second = complete_file(store, job, token, "scene_b")
    Path(first["file"]).write_text("corrupted")
    with ProductionOrderStore(store.settings).lease(job) as token:
        assert store.begin(job, token, "scene_b", {"revision": 1}) == second
        repaired = complete_file(store, job, token, "scene_a", "repaired")
        assert store.begin(job, token, "scene_a", {"revision": 1}) == repaired
    assert {s["stage"]: s["attempts"] for s in store.get(job)["checkpoints"]} == {"scene_a": 2, "scene_b": 1}


@pytest.mark.parametrize("ui_step,category", [
    ("asset_planning", "required_assets_missing"),
    ("asset_review", "awaiting_asset_review"),
])
def test_dependency_block_preserves_completed_plan_media_and_durable_budgets(store, monkeypatch, ui_step, category):
    # Storage accounting is independent of the separately tested claim research boundary.
    monkeypatch.setattr("atnos.integrations.claim_evidence_gate.ensure_media_claim_gate",
                        lambda **_: {"blocked": False})
    job = create(store)["id"]
    with store.lease(job) as token:
        plan = complete_file(store, job, token, "asset_planning", "planned scene assets")
        # Use real accounting, so a dependency wait cannot erase prior paid calls.
        store.begin(job, token, "voice", {"revision": 1}, provider_call=True, estimated_cost_usd=.25)
        voice_path = store.context(job).artifact_dir / "voice.json"
        voice_path.write_text('{"text":"final voice"}', encoding="utf-8")
        store.complete(job, token, "voice", {"text": "final voice"}, [voice_path])
        complete_file(store, job, token, "captions", "final captions")
        before = store.get(job)
        with connect(store.settings) as db:
            before_steps = [dict(row) for row in db.execute(
                "SELECT * FROM pipeline_job_steps WHERE job_id=? ORDER BY step", (job,))]
        store.block(job, token, ui_step, category=category,
                    message="Scene s01 awaits review; scene s02 has no usable asset")
        after = store.get(job)
        assert (after["status"], after["current_step"]) == ("blocked", ui_step)
        assert json.loads(after["error"])["category"] == category
        assert after["checkpoints"] == before["checkpoints"]
        assert after["reserved_cost_units"] == before["reserved_cost_units"] == 250_000
        assert after["provider_calls"] == before["provider_calls"] == 1
        assert after["profile_hash"] == before["profile_hash"]
        with connect(store.settings) as db:
            after_steps = [dict(row) for row in db.execute(
                "SELECT * FROM pipeline_job_steps WHERE job_id=? ORDER BY step", (job,))]
        assert after_steps == before_steps
    # A fresh store/lease reuses the actual completed plan, with no second attempt.
    resumed = ProductionOrderStore(store.settings)
    assert resumed.get(job)["can_resume"]
    with resumed.lease(job) as token:
        assert resumed.begin(job, token, "asset_planning", {"revision": 1}) == plan
    final = resumed.get(job)
    assert final["checkpoints"] == before["checkpoints"]
    assert (final["provider_calls"], final["reserved_cost_units"]) == (1, 250_000)


def test_attempt_limit_survives_restart_and_never_sets_ready(store, monkeypatch):
    _write_test_budget(store, max_attempts_per_element=3)
    job = create(store)["id"]
    _prepare_test_narrative_gate(store, job, monkeypatch)
    for _ in range(3):
        with ProductionOrderStore(store.settings).lease(job) as token:
            store.begin(job, token, "voice", {"revision": 1})
            store.fail(job, token, "voice", category="pronunciation", message="wrong number")
    with store.lease(job) as token:
        with pytest.raises(ProductionBlocked, match="attempt_limit"):
            store.begin(job, token, "voice", {"revision": 1})
    result = store.get(job)
    assert result["status"] == "blocked"
    assert result["can_resume"] is False
    assert result["checkpoints"][0]["attempts"] == 3


def test_cost_reservation_is_durable_and_cannot_overspend(store, monkeypatch):
    _write_test_budget(store, max_cost_usd=2.0)
    job = create(store)["id"]
    _prepare_test_narrative_gate(store, job, monkeypatch)
    with store.lease(job) as token:
        store.begin(job, token, "voice", {}, estimated_cost_usd=1.5, provider_call=True)
        store.fail(job, token, "voice", category="provider_failure", message="request failed")
    with store.lease(job) as token:
        with pytest.raises(ProductionBlocked, match="cost_limit"):
            store.begin(job, token, "voice", {}, estimated_cost_usd=.6, provider_call=True)
    assert store.get(job)["reserved_cost_units"] == 1_500_000
    assert store.get(job)["provider_calls"] == 1


def _prepare_test_narrative_gate(store, job, monkeypatch):
    # These accounting tests have no financial facts. Classify their controlled
    # narrative with the real gate so media admission does not mask the budget
    # boundary under test. Never disable the production gate or fake sources.
    from atnos.integrations.claim_evidence_gate import build_ledger
    monkeypatch.setenv("DATA_DIR", str(store.settings.data_dir))
    ledger = build_ledger(project="wealth_blueprint", production_id=job,
        script={"segments": [{"id": "s01", "type": "practical",
                               "text": "Review your budget before choosing the next transfer."}]},
        live_research=False)
    assert ledger["gate"]["production_allowed"] is True


def _write_test_budget(store, **limits):
    # This boundary fixture must not depend on the user's changing live limits.
    source = Path(__file__).parents[1] / "config/channel_profiles/wealth_blueprint.json"
    profile = json.loads(source.read_text(encoding="utf-8"))
    profile["production"].update(limits)
    target = store.settings.data_dir / "production-profiles/wealth_blueprint.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(profile), encoding="utf-8")


@pytest.mark.parametrize("cost", [-1, float("nan"), float("inf")])
def test_invalid_cost_cannot_bypass_limit(cost):
    with pytest.raises(ValueError):
        cost_units(cost)


def test_output_outside_package_and_missing_evidence_rejected(store, tmp_path):
    job = create(store)["id"]
    outside = tmp_path / "private-file.txt"
    outside.write_text("should not be included")
    with store.lease(job) as token:
        store.begin(job, token, "script", {})
        for files in ([outside], []):
            with pytest.raises(ValueError):
                store.complete(job, token, "script", {}, files)
        assert store.get(job)["checkpoints"][0]["status"] == "running"


def test_errors_redacted_in_status_and_history(store):
    job = create(store)["id"]
    with store.lease(job) as token:
        store.begin(job, token, "script", {})
        store.fail(job, token, "script", category="provider", message="api_key=super-secret-value Bearer hidden-access-token")
    result = json.dumps(store.get(job))
    assert "super-secret-value" not in result
    assert "hidden-access-token" not in result


def test_unknown_provider_cost_requires_evidence(store):
    job = create(store)["id"]
    with store.lease(job) as token:
        with pytest.raises(ValueError, match="positive upper-bound"):
            store.begin(job, token, "voice", {}, provider_call=True)
