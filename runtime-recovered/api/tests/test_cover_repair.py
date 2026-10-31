import asyncio
import copy
from pathlib import Path

import pytest

from atnos.production import cover_repair
from atnos.production.order_execution import StageExecutor
from atnos.production.order_store import ProductionBlocked, ProductionOrderStore, file_hash
from atnos.production.steps import order_delivery, order_media, order_repair
from test_production_order_script import script
from test_production_order_store import create, store


@pytest.fixture(autouse=True)
def isolated_cover_prerequisites(monkeypatch, store):
    # These synthetic unit fixtures exercise cover repair after claim admission.
    # Financial-source validation has its own tests; this is never LIVE evidence.
    from atnos.integrations import claim_evidence_gate
    monkeypatch.setattr(claim_evidence_gate,'ensure_media_claim_gate',lambda **kwargs: {'blocked':False})
    # The bounded-call assertion explicitly exercises the limited contract.
    store.settings=store.settings.model_copy(update={'atnos_local_work_unlimited':False})


def model(monkeypatch, calls, *, invalid=False):
    async def select(*args, **kwargs):
        calls.append(1)
        return {"headline_key": "unapproved" if invalid else "title", "detail_key": "s03", "reason": "Use the more specific title rather than a repeated generic headline."}
    monkeypatch.setattr(cover_repair, "ask_model", select)


def report_for(covers):
    return {"status": "failed", "sha256": covers["sha256"]["portrait"],
            "verdict": {"status": "fail", "blockers": ["Headline does not clearly convey the lesson"]}}


def test_repairs_only_failed_orientation_and_restores_without_model_call(store, script, monkeypatch):
    job, calls = create(store)["id"], []
    model(monkeypatch, calls)
    with store.lease(job) as token:
        ex = StageExecutor(store, job, token)
        ex.context.artifacts.update(script=script.model_dump(), voice={"sha": "unchanged"}, edit={"sha": "unchanged"})
        original = copy.deepcopy(asyncio.run(order_delivery.produce_covers(ex)))
        result = asyncio.run(cover_repair.repair_cover(ex, "portrait", report_for(original)))
        assert result["visual_qa"] == "not_started"
        assert ex.context.artifacts["covers"]["landscape"] == original["landscape"]
        assert file_hash(Path(original["landscape"])) == original["sha256"]["landscape"]
        assert ex.context.artifacts["voice"] == {"sha": "unchanged"}
        assert ex.context.artifacts["edit"] == {"sha": "unchanged"}
        assert file_hash(Path(result["path"])) != original["sha256"]["portrait"]
    restored = ProductionOrderStore(store.settings)
    with restored.lease(job) as token:
        ex = StageExecutor(restored, job, token)
        ex.context.artifacts["script"] = script.model_dump()
        assert asyncio.run(order_delivery.produce_covers(ex))["portrait"] == result["path"]
    assert len(calls) == 1 and restored.get(job)["provider_calls"] == 1
    assert restored.get(job)["status"] != "ready"


@pytest.mark.parametrize("defect", ["unavailable", "wrong_hash", "invalid_choice"])
def test_bad_repair_fails_closed_with_bounded_calls(store, script, monkeypatch, defect):
    job, calls = create(store)["id"], []
    model(monkeypatch, calls, invalid=defect == "invalid_choice")
    with store.lease(job) as token:
        ex = StageExecutor(store, job, token)
        ex.context.artifacts["script"] = script.model_dump()
        original = asyncio.run(order_delivery.produce_covers(ex))
        report = report_for(original)
        if defect == "unavailable":
            report["verdict"]["status"] = "unavailable"
        if defect == "wrong_hash":
            report["sha256"] = "incorrect"
        with pytest.raises(ProductionBlocked):
            asyncio.run(cover_repair.repair_cover(ex, "portrait", report))
    limit=store.get(job)['profile']['production']['max_attempts_per_element']
    assert len(calls) == (limit if defect == "invalid_choice" else 0)
    assert store.get(job)["status"] != "ready"


def test_unlimited_local_cover_work_still_pauses_repeated_invalid_selection(store,script,monkeypatch):
    store.settings=store.settings.model_copy(update={'atnos_local_work_unlimited':True})
    job,calls=create(store)['id'],[]
    model(monkeypatch,calls,invalid=True)
    with store.lease(job) as token:
        ex=StageExecutor(store,job,token)
        ex.context.artifacts['script']=script.model_dump()
        original=asyncio.run(order_delivery.produce_covers(ex))
        with pytest.raises(ProductionBlocked,match='local_retry_paused'):
            asyncio.run(cover_repair.repair_cover(ex,'portrait',report_for(original)))
    assert len(calls)==3
    assert store.get(job)['provider_calls']==3


def test_cover_only_qa_retry_does_not_render_movie(store, script, monkeypatch):
    job, calls = create(store)["id"], []
    model(monkeypatch, calls)
    reviews = []
    async def media(ex):
        return {"status": "passed"}
    async def semantic(ex):
        reviews.append(1)
        return {"status": "failed", "reports": {"cover_portrait": report_for(ex.context.artifacts["covers"])}} if len(reviews) == 1 else {"status": "passed"}
    async def forbidden_render(ex):
        raise AssertionError("A cover repair must not rerender the video")
    monkeypatch.setattr(order_media, "produce_media_qa", media)
    monkeypatch.setattr(order_media, "produce_edit", forbidden_render)
    monkeypatch.setattr(order_delivery, "produce_semantic_qa", semantic)
    with store.lease(job) as token:
        ex = StageExecutor(store, job, token)
        ex.context.artifacts["script"] = script.model_dump()
        asyncio.run(order_delivery.produce_covers(ex))
        assert asyncio.run(order_repair.produce_final_quality(ex))["status"] == "passed"
    assert len(reviews) == 2 and len(calls) == 1
