from __future__ import annotations

import pytest

from atnos.intelligence.storage import connect
from atnos.production.order_store import OrderConflict
from test_production_order_store import create, store


def overwritten_plan(store, monkeypatch, *, category="required_assets_missing", complete=True):
    # Isolate storage maintenance from the separate claim-research boundary.
    monkeypatch.setattr("atnos.integrations.claim_evidence_gate.ensure_media_claim_gate",
                        lambda **_: {"blocked": False})
    job = create(store)["id"]
    with store.lease(job) as token:
        store.begin(job, token, "asset_planning", {"script_hash": "frozen-script"}, local_model_call=True)
        path = store.context(job).artifact_dir / "asset_plan.json"
        path.write_text('{"scenes":[{"scene_id":"s01"}]}', encoding="utf-8")
        if complete:
            store.complete(job, token, "asset_planning", {"scenes": [{"scene_id": "s01"}]}, [path])
        # Preserve evidence of already consumed provider budget too.
        store.begin(job, token, "voice", {"revision": 1}, provider_call=True, estimated_cost_usd=.25)
        voice = store.context(job).artifact_dir / "voice.json"
        voice.write_text('{"text":"final voice"}', encoding="utf-8")
        store.complete(job, token, "voice", {"text": "final voice"}, [voice])
        store.fail(job, token, "asset_planning", category=category, message="Scene s01 lacks usable illustration")
    return job, path


@pytest.mark.parametrize("category", ["required_assets_missing", "awaiting_asset_review"])
def test_restore_proven_overwrite_keeps_dependencies_budgets_and_cache(store, monkeypatch, category):
    job, path = overwritten_plan(store, monkeypatch, category=category)
    before = store.get(job)
    plan_before = next(row for row in before["checkpoints"] if row["stage"] == "asset_planning")
    voice_before = next(row for row in before["checkpoints"] if row["stage"] == "voice")
    result = store.restore_overwritten_asset_plan(job, reason="Restore historical dependency-block overwrite")
    assert result["restored"]
    after = store.get(job)
    plan_after = next(row for row in after["checkpoints"] if row["stage"] == "asset_planning")
    assert plan_after["status"] == "done" and plan_after["error_json"] is None
    for key in ("input_hash", "output_json", "files_json", "attempts"):
        assert plan_after[key] == plan_before[key]
    assert next(row for row in after["checkpoints"] if row["stage"] == "voice") == voice_before
    assert (after["status"], after["current_step"], after["error"]) == (
        before["status"], before["current_step"], before["error"])
    assert (after["provider_calls"], after["reserved_cost_units"]) == (2, 250_000)
    assert after["profile_hash"] == before["profile_hash"]
    assert path.read_text(encoding="utf-8") == '{"scenes":[{"scene_id":"s01"}]}'
    audit = [event for event in after["history"] if event["event"] == "dependency_overwrite_restored"]
    assert len(audit) == 1 and audit[0]["detail"] == result["proof"]
    with connect(store.settings) as db:
        step = db.execute("SELECT * FROM pipeline_job_steps WHERE job_id=? AND step='asset_planning'", (job,)).fetchone()
        assert step["status"] == "done" and step["attempt"] == 1 and step["progress"] == 1
    assert store.restore_overwritten_asset_plan(job, reason="Second maintenance call") == {
        "restored": False, "reason": "already_done", "job_id": job}
    assert len([event for event in store.get(job)["history"] if event["event"] == "dependency_overwrite_restored"]) == 1
    with store.lease(job) as token:
        assert store.begin(job, token, "asset_planning", {"script_hash": "frozen-script"},
                           local_model_call=True) == {"scenes": [{"scene_id": "s01"}]}
    final = store.get(job)
    assert (final["provider_calls"], final["reserved_cost_units"]) == (2, 250_000)
    assert next(row for row in final["checkpoints"] if row["stage"] == "asset_planning")["attempts"] == 1


@pytest.mark.parametrize("failure", ["corrupt_file", "real_failure", "missing_completion", "changed_evidence", "new_attempt"])
def test_restore_refuses_unproven_or_stale_plan_without_mutation(store, monkeypatch, failure):
    job, path = overwritten_plan(store, monkeypatch,
                                 category="invalid_output" if failure == "real_failure" else "required_assets_missing",
                                 complete=failure != "missing_completion")
    if failure == "corrupt_file":
        path.write_text('{"scenes":"corrupted"}', encoding="utf-8")
    if failure in {"changed_evidence", "new_attempt"}:
        with connect(store.settings) as db:
            if failure == "changed_evidence":
                db.execute("UPDATE pipeline_job_events SET detail_json='{}' WHERE job_id=? AND event='stage_completed' AND stage='asset_planning'", (job,))
            else:
                db.execute("INSERT INTO pipeline_job_events(job_id,stage,event,detail_json,created_at) VALUES(?,'asset_planning','stage_started','{}','2026-09-30T07:00:00Z')", (job,))
            db.commit()
    before = store.get(job)
    with pytest.raises(OrderConflict):
        store.restore_overwritten_asset_plan(job, reason="Restore historical dependency-block overwrite")
    assert store.get(job) == before


def test_restore_refuses_live_worker_even_with_valid_completion(store, monkeypatch):
    job, _ = overwritten_plan(store, monkeypatch)
    with store.lease(job):
        before = store.get(job)
        with pytest.raises(OrderConflict, match="worker is active"):
            store.restore_overwritten_asset_plan(job, reason="Restore historical dependency-block overwrite")
        assert store.get(job) == before
