import json

import pytest

from atnos.production.order_store import ProductionBlocked, checkpoint_resolved
from test_production_order_completion import stage, package_stage
from test_production_order_store import create, store
from test_production_order_script import script


@pytest.fixture(autouse=True)
def isolated_script_admission(monkeypatch):
    # These are persistence fixtures, not financial-source or LIVE quality proof.
    from atnos.integrations import claim_evidence_gate
    monkeypatch.setattr(claim_evidence_gate,'ensure_media_claim_gate',lambda **kwargs:{'blocked':False})


def test_scoped_successor_rebinds_previously_retired_branches_without_reset(store,script):
    from atnos.production.project_profile import content_hash
    job=create(store)['id']; root=store.context(job).artifact_dir
    with store.lease(job) as token:
        store.begin(job,token,'script_repair/1',{'old_branch':True},local_model_call=True)
        stage(store,job,token,'script',extra={'qa':{'status':'passed','revision':0,'script_hash':'old-hash'}})
        store.reconcile_script_checkpoints(job,token)
        candidate=script.model_copy(deep=True)
        digest='a'*64
        qa={'status':'passed','revision':2,'script_hash':content_hash(candidate.model_dump(mode='json')),'feedback_hash':digest,'issues':[]}
        payload={**candidate.model_dump(mode='json'),'qa':qa,'provenance':{'scoped_revision':{'feedback_hash':digest}}}
        for name,value in ((f'script_scoped/{digest[:12]}/accepted',payload),(f'script_scoped/{digest[:12]}/review/1',qa),('script',payload)):
            store.begin(job,token,name,{'candidate_revision':name})
            path=root/(name.replace('/','-')+'.json'); path.write_text(json.dumps(value),encoding='utf-8')
            store.complete(job,token,name,value,[path])
        before=store.get(job)
        old=next(row for row in before['checkpoints'] if row['stage']=='script_repair/1')
        assert not checkpoint_resolved(old,before['checkpoints'])
        store.reconcile_script_checkpoints(job,token)
        after=store.get(job); rebound=next(row for row in after['checkpoints'] if row['stage']==old['stage'])
        assert checkpoint_resolved(rebound,after['checkpoints'])
        assert rebound['attempts']==old['attempts']==1 and after['provider_calls']==before['provider_calls']==1
        modified=[dict(row) for row in after['checkpoints']]
        accepted=next(row for row in modified if row['stage'].endswith('/accepted'))
        accepted['input_hash']='stale-candidate'
        assert not checkpoint_resolved(rebound,modified)


def test_abandoned_repair_resolved_only_by_valid_final_script_with_counters_preserved(store):
    job = create(store)["id"]
    with store.lease(job) as token:
        store.begin(job, token, "script_repair/1", {"old_branch": True}, local_model_call=True)
        stage(store, job, token, "script", extra={"qa": {"status": "passed", "revision": 0, "script_hash": "verified-script-hash"}})
        store.reconcile_script_checkpoints(job, token)
        store.reconcile_script_checkpoints(job, token)
        state = store.get(job)
        rows = state["checkpoints"]
        repair = next(row for row in rows if row["stage"] == "script_repair/1")
        assert repair["status"] == "superseded"
        assert repair["attempts"] == 1 and state["provider_calls"] == 1
        assert checkpoint_resolved(repair, rows)
        assert len([event for event in state["history"] if event["event"] == "script_branch_superseded"]) == 1
        assert state["status"] != "ready"
        for name in ("voice", "captions", "media_qa", "semantic_qa"):
            stage(store, job, token, name)
        package = package_stage(store, job, token)
        assert store.finish(job, token, package)["status"] == "ready"


def test_failed_final_script_cannot_retire_any_dependency(store):
    job = create(store)["id"]
    with store.lease(job) as token:
        store.begin(job, token, "script_repair/1", {})
        stage(store, job, token, "script", extra={"qa": {"status": "failed", "revision": 0, "script_hash": "bad"}})
        with pytest.raises(ProductionBlocked, match="final review"):
            store.reconcile_script_checkpoints(job, token)
        assert next(row for row in store.get(job)["checkpoints"] if row["stage"] == "script_repair/1")["status"] == "running"


@pytest.mark.parametrize("stage_name", ["voice", "media_qa", "script", "script_repair/0"])
def test_superseded_cannot_skip_required_or_current_branch(stage_name):
    final = {"stage": "script", "status": "done", "input_hash": "input", "output_json": json.dumps({"qa": {
        "status": "passed", "revision": 0, "script_hash": "hash"}})}
    row = {"stage": stage_name, "status": "superseded", "output_json": json.dumps({"superseded_by": {
        "stage": "script", "input_hash": "input", "script_hash": "hash"}})}
    assert not checkpoint_resolved(row, [final, row])
