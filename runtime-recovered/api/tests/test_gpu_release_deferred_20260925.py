import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_support import build_test_settings
from atnos.production.orchestrator import ProductionOrchestrator
from atnos.production.steps.video_generator import _prepare_wealth_image_candidates


@pytest.mark.parametrize("caller", ["legacy_orchestrator", "image_candidates"])
def test_unknown_model_activity_blocks_gpu_handoff(tmp_path, monkeypatch, caller):
    settings = build_test_settings(tmp_path)
    settings.app_env = "development"
    monkeypatch.setattr("atnos.services.hardware_guard_service.HardwareGuardService.release_gpu_pressure",
                        lambda _: {"status": "deferred", "verified_released": False,
                                   "message": "No request drain proof", "stopped_models": []})
    start = Mock(side_effect=AssertionError("Must not start image generation"))
    stop = Mock(side_effect=AssertionError("Must not stop another runtime"))
    monkeypatch.setattr("atnos.production.steps.video_generator.ComfyUiImageGenerationService", start)
    monkeypatch.setattr("atnos.production.orchestrator.F5TTSRuntimeService.stop", stop)
    monkeypatch.setattr("atnos.production.comfy_channels.model_profiles.load_model_profile",
                        lambda *_: {"quality_model": "configured-test-checkpoint"})
    if caller == "legacy_orchestrator":
        context = SimpleNamespace(artifacts={}, artifact_dir=tmp_path)
        operation = ProductionOrchestrator(settings)._prepare_gpu_runtime_for_step(context, "video")
    else:
        operation = _prepare_wealth_image_candidates(settings, [], video_dir=tmp_path,
                                                     topic_title="Test", channel_profile=SimpleNamespace(key="wealth_blueprint"))
    with pytest.raises(RuntimeError, match="No request drain proof"):
        asyncio.run(operation)
    start.assert_not_called()
    stop.assert_not_called()
