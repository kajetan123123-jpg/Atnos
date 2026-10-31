import json

import pytest

from atnos.production.order_store import OrderConflict, ProductionBlocked
from atnos.production.project_profile import content_hash
from atnos.production.steps.common import write_json
from atnos.production.steps.order_assets import revalidate_scene_plan
from test_production_order_assets import plan_for
from test_production_order_store import store, create
from test_production_order_script import script


@pytest.mark.parametrize("invalid_plan", [False, True])
def test_revalidation_preserves_attempts_and_checks_existing_evidence(store, script, invalid_plan):
    job_id = create(store)["id"]
    context = store.context(job_id)
    script_payload = script.model_dump(mode="json")
    library = {"assets": []}
    ranking = {"method": "dense_embedding_cosine", "candidates": {}}
    inputs = {"ranking": ranking, "script": script_payload, "profile": context.artifacts["project_profile"]["production"]}
    with store.lease(job_id) as token:
        for name, payload in (("script", script_payload), ("asset_library", library), ("asset_ranking", ranking)):
            store.begin(job_id, token, name, {})
            result = write_json(context.artifact_dir / f"{name}.json", payload)
            store.complete(job_id, token, name, payload, [result.artifact_path])
        for _ in range(3):
            store.begin(job_id, token, "asset_planning", inputs, local_model_call=True)
            store.fail(job_id, token, "asset_planning", category="invalid_output", message="Unsupported chart before renderer fix")
        with pytest.raises(ProductionBlocked):
            store.begin(job_id, token, "asset_planning", inputs, local_model_call=True)
    plan = plan_for(script).model_dump(mode="json")
    if invalid_plan:
        plan["scenes"][0]["segment_id"] = "unknown"
    response = context.artifact_dir / "model_calls" / "original.json"
    write_json(response, {"contract": "ScenePlan", "status": "response_received", "response": plan})
    before = store.get(job_id)
    if invalid_plan:
        with pytest.raises(ValueError, match="exactly once"):
            revalidate_scene_plan(store, job_id)
        after = store.get(job_id)
        assert after["status"] == "blocked"
        assert after["checkpoints"][-1]["status"] == "failed"
    else:
        after = revalidate_scene_plan(store, job_id)
        assert after["status"] == "queued"
        assert after["can_resume"] is True
        assert after["checkpoints"][-1]["input_hash"] == content_hash(inputs)
        assert after["checkpoints"][-1]["status"] == "done"
        assert any(e["event"] == "output_revalidated" for e in after["history"])
        with pytest.raises(OrderConflict):
            revalidate_scene_plan(store, job_id)
    assert after["provider_calls"] == before["provider_calls"] == 3
    assert after["checkpoints"][-1]["attempts"] == 3
    assert after["reserved_cost_units"] == before["reserved_cost_units"]
