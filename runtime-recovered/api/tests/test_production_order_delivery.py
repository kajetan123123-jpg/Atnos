from __future__ import annotations

import asyncio
import json
from pathlib import Path

from PIL import Image
import pytest

from atnos.production.order_execution import StageExecutor
from atnos.production.order_store import ProductionBlocked
from atnos.production.steps import order_delivery
from test_production_order_store import store, create
from test_production_order_script import script
from test_production_order_completion import stage
from atnos.production.steps.common import write_json


def _seed_checked_script(store, job, token, script):
    # Exercise delivery QA after real script admission, rather than failing
    # first at the unrelated missing claim-ledger guard. No gate is mocked.
    assert store.begin(job, token, 'script', {}) is None
    result=write_json(store.context(job).artifact_dir/'script.json',script.model_dump(mode='json'))
    store.complete(job,token,'script',result.payload,[result.artifact_path])


def test_covers_are_real_images_and_reused(store, script):
    job = create(store)["id"]
    with store.lease(job) as token:
        _seed_checked_script(store,job,token,script)
        ex = StageExecutor(store, job, token)
        ex.context.artifacts["script"] = script.model_dump()
        result = asyncio.run(order_delivery.produce_covers(ex))
        assert Image.open(result["portrait"]).size == (1080, 1920)
        assert Image.open(result["landscape"]).size == (1280, 720)
        assert asyncio.run(order_delivery.produce_covers(ex)) == result
    assert next(r for r in store.get(job)["checkpoints"] if r["stage"] == "covers")["attempts"] == 1


@pytest.mark.parametrize("vision_status", ["pass", "fail", "unavailable", "skipped"])
def test_semantic_gate_really_calls_each_scene_and_cover_and_fails_closed(store, script, monkeypatch, vision_status):
    job = create(store)["id"]
    calls = []
    def extract(video, output, **kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (270, 480), "green").save(output)
        return output
    def inspect(settings, path, **kwargs):
        calls.append(path)
        contract = kwargs["required_visual_contract"]
        assert "Subtitles change over time" in contract
        assert "Equivalent numeric forms" in contract
        assert "Missing required objects" in contract
        return {"status": vision_status, "contract_satisfied": vision_status == "pass",
                "confidence": 9, "violations": []}
    monkeypatch.setattr(order_delivery.visual_semantic_audit, "extract_storyboard_contact_sheet", extract)
    monkeypatch.setattr(order_delivery.visual_semantic_audit, "audit_visual_contract_compliance", inspect)
    monkeypatch.setattr(order_delivery.visual_semantic_audit, "audit_visual_semantics",
        lambda *a, **k: {"status": "pass", "passed": True})
    with store.lease(job) as token:
        _seed_checked_script(store,job,token,script)
        ex = StageExecutor(store, job, token)
        ex.context.artifacts["script"] = script.model_dump()
        asyncio.run(order_delivery.produce_covers(ex))
        clip = ex.context.artifact_dir / "test_fixture.mp4"
        clip.write_bytes(b"not real video; extraction is mocked in this contract test")
        ex.context.artifacts["edit"] = {"clips": [{"segment_id": "s01", "path": str(clip)}]}
        ex.context.artifacts["visuals"] = {"scenes": [{"segment_id": "s01", "narration": script.segments[0].text,
            "kind": "footage", "semantic_goal": "Show banking", "headline": "Your next step"}]}
        result = asyncio.run(order_delivery.produce_semantic_qa(ex))
        assert (result["status"] == "passed") == (vision_status == "pass")
        assert len(result["reports"]) == 3
        assert len(calls) == 1


def test_package_cannot_skip_qa(store, script):
    job = create(store)["id"]
    with store.lease(job) as token:
        _seed_checked_script(store,job,token,script)
        ex = StageExecutor(store, job, token)
        with pytest.raises(ProductionBlocked):
            asyncio.run(order_delivery.produce_package(ex))
    assert store.get(job)["status"] == "blocked"
    assert "incomplete QA" in store.get(job)["error"]
