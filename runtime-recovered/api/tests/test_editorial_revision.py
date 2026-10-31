import asyncio
import copy
import json
from pathlib import Path

import pytest

from atnos.intelligence.storage import connect
from atnos.production.editorial_revision import admit, candidate, load_accepted
from atnos.production.order_execution import StageExecutor, produce_script
from atnos.production.order_store import OrderConflict, ProductionBlocked
from atnos.production.project_profile import ProductionOrder
from test_finance_four_intake import supported_intake
from test_production_order_store import store


@pytest.fixture
def revision(store, tmp_path):
    doc = json.loads((Path(__file__).parents[1] / "config" / "finance_four_editorial_20260907.json").read_text())
    contract = supported_intake()
    job = store.create(ProductionOrder(topic="Compound growth", intake=contract))["id"]
    doc["scripts"] = [doc["scripts"][0]]
    doc["scripts"][0]["job_id"] = job
    path = tmp_path / "edit.json"
    path.write_text(json.dumps(doc))
    with store.lease(job) as token:
        store.begin(job, token, "script_draft", {}, local_model_call=True)
        store.fail(job, token, "script_draft", category="invalid_output", message="Previous real writing failed")
    return job, path, doc


def test_explicit_handoff_required_and_invalid_text_not_admitted(store, revision):
    job, path, doc = revision
    with pytest.raises(ValueError, match="explicit"):
        asyncio.run(admit(store.settings, job, path))
    doc["scripts"][0]["segments"][0][1] = "Earn 999999 dollars guaranteed profit."
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="tts_numbers"):
        asyncio.run(admit(store.settings, job, path, approved=True))
    assert load_accepted(store, job) is None


def test_failed_independent_review_keeps_original_checkpoints(store, revision, monkeypatch):
    job, path, _ = revision
    async def reject(*args):
        return {"status":"failed", "issues":[{"code":"misleading", "segment_ids":["s01"], "detail":"Claim needs correction"}]}
    monkeypatch.setattr("atnos.production.steps.order_script.review", reject)
    result = asyncio.run(admit(store.settings, job, path, approved=True))
    assert result["status"] == "rejected"
    assert next(row for row in store.get(job)["checkpoints"] if row["stage"] == "script_draft")["status"] == "failed"
    assert load_accepted(store, job) is None


def test_acceptance_preserves_history_budget_and_requires_intact_archive(store, revision, monkeypatch):
    job, path, _ = revision
    async def accept(*args):
        return {"status":"passed", "issues":[], "semantic_review":"controlled_test_reviewer"}
    monkeypatch.setattr("atnos.production.steps.order_script.review", accept)
    before = store.get(job)
    result = asyncio.run(admit(store.settings, job, path, approved=True))
    assert result["status"] == "accepted"
    current = store.get(job)
    assert current["provider_calls"] == before["provider_calls"] + 1
    assert current["reserved_cost_units"] == before["reserved_cost_units"]
    assert current["request"] == before["request"]
    assert current["status"] == "queued" and current["can_resume"]
    assert asyncio.run(admit(store.settings, job, path, approved=True))["status"] == "already_accepted"
    with connect(store.settings) as db:
        archived = json.loads(db.execute("SELECT archive_json FROM production_editorial_revisions WHERE job_id=?", (job,)).fetchone()[0])
    assert archived[0]["status"] == "failed" and archived[0]["attempts"] == 1
    with store.lease(job) as token:
        script = asyncio.run(produce_script(StageExecutor(store, job, token)))
        assert script["qa"]["status"] == "passed"
    assert store.get(job)["status"] != "ready"
    (store.context(job).artifact_dir / "editorial" / "superseded-checkpoints.json").write_text("tampered")
    with pytest.raises(ProductionBlocked, match="archive"):
        load_accepted(store, job)


@pytest.mark.parametrize("index,target", [(0,60),(1,50),(2,75),(3,55)])
def test_all_real_edits_have_valid_calculations_fact_ids_and_practical_share(store,index,target):
    doc = json.loads((Path(__file__).parents[1] / "config" / "finance_four_editorial_20260907.json").read_text())
    contract = supported_intake()
    contract.scenario_index, contract.target_duration_seconds = index + 1, target
    job = store.create(ProductionOrder(topic="Reviewed finance scenario", intake=contract))["id"]
    doc["scripts"] = [doc["scripts"][index]]
    doc["scripts"][0]["job_id"] = job
    _, issues = candidate(store.context(job), doc)
    assert issues == []
