import json
import subprocess
import tempfile
from pathlib import Path

import pytest

from atnos.intelligence.storage import connect
from atnos.production.local_stage_recovery import (
    local_ffmpeg_timeout_message,
    local_stage_recovery_evidence,
)
from atnos.production.order_store import ProductionOrderStore
from atnos.production.project_profile import ProductionOrder
from test_support import build_test_settings


class _Store:
    def _valid_files(self, artifact_dir, files_json):
        return files_json == '["verified"]'


def _state(*, stage="editing", category="local_tool_timeout", voice=True, renders=True):
    checkpoints = [
        {"stage": "editing", "status": "failed", "attempts": 1, "files_json": "[]"},
    ]
    if voice:
        checkpoints.append({"stage": "voice", "status": "done", "files_json": '["verified"]'})
    if renders:
        checkpoints.append({"stage": "render/s01", "status": "done", "files_json": '["verified"]'})
    return {
        "status": "blocked",
        "current_step": stage,
        "error": {"category": category, "message": json.dumps({"local_tool_failure": {
            "exception_type": "subprocess.TimeoutExpired",
            "stage": "editing",
            "operation": "ffmpeg_mux",
            "command": [r"E:\Atnos\Tools\ffmpeg.exe", "-i", "input", r"E:\fixture\assets\final_short.mp4"],
            "expected_outputs": [r"E:\fixture\assets\renderer_silent.mp4", r"E:\fixture\assets\final_short.mp4"],
        }})},
        "artifact_dir": r"E:\fixture",
        "checkpoints": checkpoints,
    }


def test_allows_only_verified_local_ffmpeg_editing_timeout():
    evidence = local_stage_recovery_evidence(_Store(), _state())

    assert evidence == {
        "stage": "editing",
        "kind": "verified_local_ffmpeg_timeout",
        "attempts_preserved": 1,
        "verified_upstream_stages": ["voice", "render/s01"],
        "dispatch_scope": "normal_stage_executor_only",
        "external_post_replay": False,
    }


def test_rejects_other_stages_and_non_timeout_errors():
    assert local_stage_recovery_evidence(_Store(), _state(stage="render/s01")) is None
    state = _state()
    detail = json.loads(state["error"]["message"])
    detail["local_tool_failure"]["exception_type"] = "httpx.TimeoutException"
    state["error"]["message"] = json.dumps(detail)
    assert local_stage_recovery_evidence(_Store(), state) is None
    assert local_stage_recovery_evidence(_Store(), _state(category="attempt_limit")) is None


def test_requires_intact_completed_voice_and_every_render_checkpoint():
    assert local_stage_recovery_evidence(_Store(), _state(voice=False)) is None
    state = _state()
    state["checkpoints"][-1]["files_json"] = "[]"
    assert local_stage_recovery_evidence(_Store(), state) is None
    state = _state()
    state["checkpoints"][-1]["status"] = "failed"
    assert local_stage_recovery_evidence(_Store(), state) is None


def test_rejects_command_outside_e_or_unbound_output():
    state = _state()
    detail = json.loads(state["error"]["message"])
    detail["local_tool_failure"]["command"][0] = r"C:\Tools\ffmpeg.exe"
    state["error"]["message"] = json.dumps(detail)
    assert local_stage_recovery_evidence(_Store(), state) is None
    state = _state()
    detail = json.loads(state["error"]["message"])
    detail["local_tool_failure"]["command"][-1] = r"E:\elsewhere\final_short.mp4"
    state["error"]["message"] = json.dumps(detail)
    assert local_stage_recovery_evidence(_Store(), state) is None


def test_timeout_message_factory_requires_actual_configured_ffmpeg_and_exact_mux_target(monkeypatch):
    import atnos.production.media_tools as media_tools

    exe = r"E:\Atnos\Tools\ffmpeg.exe"
    artifact_dir = Path(r"E:\fixture\order")
    target = artifact_dir / "assets" / "final_short.mp4"
    exc = subprocess.TimeoutExpired([exe, "-i", "in.mp4", str(target)], timeout=180)

    monkeypatch.setattr(media_tools, "resolve_ffmpeg", lambda: r"C:\Tools\ffmpeg.exe")
    assert local_ffmpeg_timeout_message(exc, "editing", artifact_dir) is None
    monkeypatch.setattr(media_tools, "resolve_ffmpeg", lambda: exe)
    assert local_ffmpeg_timeout_message(exc, "render/s01", artifact_dir) is None
    encoded = local_ffmpeg_timeout_message(exc, "editing", artifact_dir)
    assert json.loads(encoded)["local_tool_failure"]["expected_outputs"] == [
        r"E:\fixture\order\assets\final_short.mp4",
        r"E:\fixture\order\assets\renderer_silent.mp4",
    ]


def test_executor_timeout_is_recorded_and_monitor_reserves_normal_dispatch_once(monkeypatch):
    import asyncio

    from atnos.production import media_tools
    from atnos.production.order_autonomy import enable, policy, recover_due
    from atnos.production.order_execution import StageExecutor

    monkeypatch.setattr("atnos.integrations.claim_evidence_gate.ensure_media_claim_gate",
                        lambda **_: {"blocked": False})
    exe = r"E:\Atnos\Tools\ffmpeg.exe"
    monkeypatch.setattr(media_tools, "resolve_ffmpeg", lambda: exe)
    temp_root = Path(r"E:\Atnos\Temp")
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="local-stage-recovery-", dir=temp_root) as temp:
        settings = build_test_settings(Path(temp))
        with connect(settings) as db:
            db.execute("INSERT INTO intelligence_accounts(account_id,niches,region,last_requested_at) VALUES('yt-wealth-blueprint','[\"finance\"]','US','2026-09-06T00:00:00Z')")
            db.commit()
        store = ProductionOrderStore(settings)
        job = store.create(ProductionOrder(topic="A simple savings decision", delivery_mode="local_only"))["id"]
        context = store.context(job)
        with store.lease(job) as token:
            for stage in ("voice", "render/s01"):
                assert store.begin(job, token, stage, {"fixture": stage}) is None
                artifact = context.artifact_dir / (stage.replace("/", "-") + ".json")
                artifact.write_text(json.dumps({"stage": stage}), encoding="utf-8")
                store.complete(job, token, stage, {"stage": stage}, [artifact])
            target = context.artifact_dir / "assets" / "final_short.mp4"
            timeout = subprocess.TimeoutExpired([exe, "-i", "input.mp4", str(target)], timeout=180)

            async def time_out():
                raise timeout

            executor = StageExecutor(store, job, token)
            try:
                asyncio.run(executor.run("editing", {"fixture": "mux"}, time_out))
            except Exception as exc:
                from atnos.production.order_store import ProductionBlocked
                assert isinstance(exc, ProductionBlocked)
            else:
                raise AssertionError("The timed out mux must block at the operation boundary")

        state = store.get(job)
        assert local_stage_recovery_evidence(store, state) is not None
        assert state["provider_calls"] == 0
        before = state["provider_calls"]
        enable(settings, job, reason="User authorizes one bounded local FFmpeg recovery.", max_recoveries=1)
        dispatches = []
        monkeypatch.setattr("atnos.production.order_dispatch.ProductionOrderDispatcher.dispatch",
                            lambda self, job_id: dispatches.append(job_id) or {"id": job_id})
        assert recover_due(settings) == [{"id": job}]
        assert recover_due(settings) == []
        recovered = store.get(job)
        assert dispatches == [job]
        assert recovered["provider_calls"] == before
        assert policy(settings, job)["recoveries_used"] == 1
        reservation = [event for event in recovered["history"] if event["event"] == "autonomous_recovery_reserved"]
        assert len(reservation) == 1
        assert reservation[0]["detail"]["kind"] == "local_tool_recovery"
        assert reservation[0]["detail"]["evidence"]["attempts_preserved"] == 1

        # Even if the ordinary worker returns with the same local timeout at
        # attempt two, eligibility is exhausted and no second reservation occurs.
        with store.lease(job) as token:
            assert store.begin(job, token, "editing", {"fixture": "mux"}) is None
            store.fail(job, token, "editing", category="local_tool_timeout",
                       message=local_ffmpeg_timeout_message(timeout, "editing", context.artifact_dir))
        assert local_stage_recovery_evidence(store, store.get(job)) is None
        assert recover_due(settings) == []
        assert policy(settings, job)["recoveries_used"] == 1
        assert dispatches == [job]

        render_artifact = context.artifact_dir / "render-s01.json"
        render_artifact.write_text("changed after checkpoint", encoding="utf-8")
        assert local_stage_recovery_evidence(store, store.get(job)) is None


def test_tampered_real_upstream_hash_prevents_monitor_reservation(monkeypatch):
    import asyncio

    from atnos.production import media_tools
    from atnos.production.order_autonomy import enable, policy, recover_due
    from atnos.production.order_execution import StageExecutor
    from atnos.production.order_store import ProductionBlocked

    monkeypatch.setattr("atnos.integrations.claim_evidence_gate.ensure_media_claim_gate",
                        lambda **_: {"blocked": False})
    exe = r"E:\Atnos\Tools\ffmpeg.exe"
    monkeypatch.setattr(media_tools, "resolve_ffmpeg", lambda: exe)
    temp_root = Path(r"E:\Atnos\Temp")
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="local-stage-tamper-", dir=temp_root) as temp:
        settings = build_test_settings(Path(temp))
        with connect(settings) as db:
            db.execute("INSERT INTO intelligence_accounts(account_id,niches,region,last_requested_at) VALUES('yt-wealth-blueprint','[\"finance\"]','US','2026-09-06T00:00:00Z')")
            db.commit()
        store = ProductionOrderStore(settings)
        job = store.create(ProductionOrder(topic="A simple savings decision", delivery_mode="local_only"))["id"]
        context = store.context(job)
        with store.lease(job) as token:
            for stage in ("voice", "render/s01"):
                assert store.begin(job, token, stage, {"fixture": stage}) is None
                artifact = context.artifact_dir / (stage.replace("/", "-") + ".json")
                artifact.write_text(json.dumps({"stage": stage}), encoding="utf-8")
                store.complete(job, token, stage, {"stage": stage}, [artifact])
            target = context.artifact_dir / "assets" / "final_short.mp4"
            timeout = subprocess.TimeoutExpired([exe, "-i", "input.mp4", str(target)], timeout=180)

            async def time_out():
                raise timeout

            try:
                asyncio.run(StageExecutor(store, job, token).run("editing", {"fixture": "mux"}, time_out))
            except ProductionBlocked:
                pass
            else:
                raise AssertionError("The timed out mux must block at the operation boundary")

        state = store.get(job)
        assert next(row for row in state["checkpoints"] if row["stage"] == "editing")["attempts"] == 1
        assert local_stage_recovery_evidence(store, state) is not None
        render_artifact = context.artifact_dir / "render-s01.json"
        render_artifact.write_text("tampered before recovery", encoding="utf-8")
        assert local_stage_recovery_evidence(store, store.get(job)) is None

        enable(settings, job, reason="User authorizes one bounded local FFmpeg recovery.", max_recoveries=1)
        monkeypatch.setattr("atnos.production.order_dispatch.ProductionOrderDispatcher.dispatch",
                            lambda *_: pytest.fail("Tampered upstream evidence must not dispatch"))
        assert recover_due(settings) == []
        assert policy(settings, job)["recoveries_used"] == 0
        assert store.get(job)["provider_calls"] == 0
