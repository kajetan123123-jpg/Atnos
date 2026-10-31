"""Offline contract tests: job-scoped models must control production preflight."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from atnos.production import order_execution
from atnos.production.order_store import ProductionBlocked


@pytest.fixture
def preflight_context(tmp_path, monkeypatch):
    reference = tmp_path / "reference.json"
    reference.write_text("{}", encoding="utf-8")
    config = {
        "script_model": {"generator": "old-writer", "reviewer": "old-reviewer"},
        "reference_profile": reference.name,
        "assets": {"embedding_model": "embedding", "manifests": []},
        "visual_qa": {"model": "vision"},
    }
    settings = SimpleNamespace(data_dir=tmp_path, app_root=tmp_path)
    artifact_dir = tmp_path / "production-artifacts"
    context = SimpleNamespace(job_id="isolated-job", artifacts={
        "project_profile": {"production": config},
        "production_order": {"project": "wealth_blueprint"},
    }, artifact_dir=artifact_dir)
    executor = SimpleNamespace(context=context, store=SimpleNamespace(settings=settings))
    monkeypatch.setattr("atnos.production.channel_contract.validate_frozen", lambda *_: None)
    monkeypatch.setattr(order_execution, "_preflight_full_media_runtime", lambda *_args, **_kwargs: None)
    checks = []
    registered = {"writer": None, "reviewer": None}

    def load(kind):
        def verified(settings_arg, job, *, verify_runtime):
            assert settings_arg is settings and job == "isolated-job"
            assert verify_runtime is True
            checks.append(kind)
            value = registered[kind]
            if isinstance(value, Exception):
                raise value
            return value
        return verified

    monkeypatch.setattr("atnos.production.script_model_lane.load_script_model_lane", load("writer"))
    monkeypatch.setattr("atnos.production.script_review_model_lane.load_script_review_model_lane", load("reviewer"))
    resource_calls = []

    def capture_resources(_guard, *, snapshot_type="runtime", vram_floor_gb=4, persist=True):
        assert persist is False
        resource_calls.append((snapshot_type, vram_floor_gb))
        return SimpleNamespace(to_dict=lambda: {
            "free_ram_mb": 8192, "total_ram_mb": 16384,
            "free_vram_mb": 6144, "total_vram_mb": 8192,
            "free_disk_gb": 120, "disk_path": str(tmp_path),
            "status": "ready", "notes": None,
        })

    monkeypatch.setattr("atnos.services.hardware_guard_service.HardwareGuardService.capture", capture_resources)
    executor.resource_calls = resource_calls
    installed = []
    endpoint_checks = []

    async def endpoint(executor_arg, client, *, allow_start):
        assert executor_arg is executor and allow_start is False
        endpoint_checks.append(True)
        return SimpleNamespace(json=lambda: {"models": [{"name": n} for n in installed]})

    monkeypatch.setattr("atnos.production.model_runtime_startup.ensure_model_endpoint", endpoint)
    return executor, registered, installed, checks, endpoint_checks


def test_registered_writer_and_reviewer_do_not_require_old_default_endpoint(preflight_context):
    executor, registered, _, checks, endpoints = preflight_context
    registered.update(writer={"model": "approved-writer"}, reviewer={"model": "approved-reviewer"})
    asyncio.run(order_execution.preflight_order(executor, allow_model_start=False, script_only=True))
    assert checks == ["writer", "reviewer"]
    assert endpoints == []  # Dedicated endpoints were validated by the lane loaders.


def test_writer_override_keeps_unoverridden_reviewer_required(preflight_context):
    executor, registered, installed, checks, endpoints = preflight_context
    registered["writer"] = {"model": "approved-writer"}
    installed.append("old-reviewer")
    asyncio.run(order_execution.preflight_order(executor, allow_model_start=False, script_only=True))
    assert checks == ["writer", "reviewer"] and endpoints == [True]
    installed.clear()
    with pytest.raises(ProductionBlocked, match="missing local models: old-reviewer$"):
        asyncio.run(order_execution.preflight_order(executor, allow_model_start=False, script_only=True))


def test_invalid_registered_lane_blocks_without_default_fallback(preflight_context):
    executor, registered, installed, _, endpoints = preflight_context
    registered["writer"] = ProductionBlocked("script_model_lane: changed model pin")
    installed.extend(["old-writer", "old-reviewer"])
    with pytest.raises(ProductionBlocked, match="changed model pin"):
        asyncio.run(order_execution.preflight_order(executor, allow_model_start=False, script_only=True))
    assert endpoints == []


def test_unregistered_jobs_keep_frozen_models(preflight_context):
    executor, _, installed, checks, endpoints = preflight_context
    installed.extend(["old-writer", "old-reviewer"])
    asyncio.run(order_execution.preflight_order(executor, allow_model_start=False, script_only=True))
    assert checks == ["writer", "reviewer"] and endpoints == [True]
    installed.remove("old-writer")
    with pytest.raises(ProductionBlocked, match="missing local models: old-writer$"):
        asyncio.run(order_execution.preflight_order(executor, allow_model_start=False, script_only=True))


def test_full_media_still_requires_embedding_and_vision(preflight_context, monkeypatch):
    executor, registered, installed, _, endpoints = preflight_context
    registered.update(writer={"model": "approved-writer"}, reviewer={"model": "approved-reviewer"})
    monkeypatch.setattr(order_execution, "_voice_preflight_settings", lambda *_: executor.store.settings)
    installed.extend(["embedding", "vision"])
    asyncio.run(order_execution.preflight_order(executor, allow_model_start=False))
    assert endpoints == [True]
    readiness = json.loads((executor.context.artifact_dir / "diagnostics" / "runtime-readiness.json").read_text("utf-8"))
    assert readiness["job_id"] == "isolated-job"
    assert readiness["stage"] == "preflight"
    assert readiness["model_status"] == "verified"
    assert readiness["readiness_status"] == "degraded_capacity_requirements_not_profiled"
    assert readiness["required_models"] == ["embedding", "vision"]
    assert readiness["resource_snapshot"]["free_ram_mb"] == 8192
    assert readiness["missing_measurements"] == []
    assert readiness["input_hash"]
    assert readiness["attempt"] == 0 and readiness["provider_call_id"] is None
    assert executor.resource_calls == [("runtime", 4)]
    installed.remove("vision")
    with pytest.raises(ProductionBlocked, match="missing local models: vision$"):
        asyncio.run(order_execution.preflight_order(executor, allow_model_start=False))


def test_preflight_preserves_unknown_resource_measurements_instead_of_inventing_values(preflight_context, monkeypatch):
    executor, registered, installed, _, _ = preflight_context
    registered.update(writer={"model": "approved-writer"}, reviewer={"model": "approved-reviewer"})
    monkeypatch.setattr(order_execution, "_voice_preflight_settings", lambda *_: executor.store.settings)
    installed.extend(["embedding", "vision"])
    monkeypatch.setattr(
        "atnos.services.hardware_guard_service.HardwareGuardService.capture",
        lambda *_args, **_kwargs: SimpleNamespace(to_dict=lambda: {
            "free_ram_mb": 0, "total_ram_mb": 0, "free_vram_mb": None,
            "total_vram_mb": None, "free_disk_gb": None, "disk_path": "E:\\",
            "status": "unknown", "notes": "Live GPU telemetry unavailable.",
        }),
    )
    asyncio.run(order_execution.preflight_order(executor, allow_model_start=False))
    readiness = json.loads((executor.context.artifact_dir / "diagnostics" / "runtime-readiness.json").read_text("utf-8"))
    assert readiness["resource_snapshot"]["free_vram_mb"] is None
    assert readiness["resource_snapshot"]["free_disk_gb"] is None
    assert readiness["missing_measurements"] == ["free_vram_mb", "free_disk_gb"]
    assert readiness["capacity_decision"] == "unknown_stage_requirements_not_fully_profiled"
    assert readiness["readiness_status"] == "degraded_capacity_requirements_not_profiled"


def test_renderer_failure_blocks_before_model_endpoint_or_media_work(preflight_context, monkeypatch):
    executor, registered, installed, _, endpoint_checks = preflight_context
    registered.update(writer={"model": "approved-writer"}, reviewer={"model": "approved-reviewer"})
    monkeypatch.setattr(order_execution, "_voice_preflight_settings", lambda *_: executor.store.settings)

    def fail_renderer(_executor, *, script_only):
        assert script_only is False
        raise ProductionBlocked("Preflight: required Wealth Blueprint Remotion runtime: FFmpeg unavailable")

    monkeypatch.setattr(order_execution, "_preflight_full_media_runtime", fail_renderer)
    with pytest.raises(ProductionBlocked, match="FFmpeg unavailable"):
        asyncio.run(order_execution.preflight_order(executor, allow_model_start=False))
    assert endpoint_checks == []


def test_both_lanes_replace_shared_contract_role_but_keep_fallback(preflight_context, monkeypatch):
    executor, registered, installed, _, _ = preflight_context
    executor.context.artifacts["project_profile"]["production"]["script_model"] = {
        "generator": "old-shared", "reviewer": "old-shared"}
    registered.update(writer={"model": "approved-writer"}, reviewer={"model": "approved-reviewer"})
    contract = {"ollama_models": {
        "script_and_editorial": {"model": "old-shared", "required": True},
        "fast_local_fallback": {"model": "old-shared", "required": True},
    }}
    monkeypatch.setattr("atnos.production.channel_contract.validate_frozen", lambda *_: contract)
    monkeypatch.setattr(order_execution, "_voice_preflight_settings", lambda *_: executor.store.settings)
    installed.extend(["embedding", "vision"])
    with pytest.raises(ProductionBlocked, match="missing local models: old-shared$"):
        asyncio.run(order_execution.preflight_order(executor, allow_model_start=False))
    contract["ollama_models"]["fast_local_fallback"]["model"] = "fast-fallback"
    installed.append("fast-fallback")
    asyncio.run(order_execution.preflight_order(executor, allow_model_start=False))


def test_one_lane_cannot_replace_shared_contract_role(preflight_context, monkeypatch):
    executor, registered, installed, _, _ = preflight_context
    executor.context.artifacts["project_profile"]["production"]["script_model"] = {
        "generator": "old-shared", "reviewer": "old-shared"}
    registered["writer"] = {"model": "approved-writer"}
    contract = {"ollama_models": {
        "script_and_editorial": {"model": "old-shared", "required": True}}}
    monkeypatch.setattr("atnos.production.channel_contract.validate_frozen", lambda *_: contract)
    monkeypatch.setattr(order_execution, "_voice_preflight_settings", lambda *_: executor.store.settings)
    installed.extend(["embedding", "vision"])
    with pytest.raises(ProductionBlocked, match="missing local models: old-shared$"):
        asyncio.run(order_execution.preflight_order(executor, allow_model_start=False))
