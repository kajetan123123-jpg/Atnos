"""Exercise real channel orchestration with isolated, non-GPU image boundaries."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from atnos.production.comfy_channels import service as channel_service
from atnos.production.comfy_channels.cache import ChannelGenerationCache
from atnos.production.comfy_channels.models import GenerationRequest
from atnos.production.comfy_channels.quality_gate import AssetQualityGateResult
from atnos.services.comfyui_image_generation_service import (
    ComfyUiImageGenerationError,
    ComfyUiRuntimeUnavailableError,
)


BASE = "checkpoints/sd_xl_base_1.0_0.9vae.safetensors"
TURBO = "checkpoints/sd_xl_turbo_1.0_fp16.safetensors"


class ImageBoundary:
    def __init__(self, failures=0, actual_checkpoint=None, failure_error=None):
        self.failures = failures
        self.actual_checkpoint = actual_checkpoint
        self.failure_error = failure_error or ComfyUiImageGenerationError(
            "ComfyUI startup timed out: interpreter has no PyTorch"
        )
        self.calls = []

    def generate_background(self, **kwargs):
        self.calls.append(kwargs)
        if self.failures:
            self.failures -= 1
            raise self.failure_error
        path = kwargs["output_path"]
        path.write_bytes(b"isolated image boundary" * 100)
        checkpoint = Path(self.actual_checkpoint or kwargs["preferred_checkpoint"]).name
        evidence_path = path.with_suffix(".evidence.json")
        evidence_path.write_text(json.dumps({
            "models": [checkpoint],
            "workflow": {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}}},
        }), encoding="utf-8")
        return SimpleNamespace(backend="checkpoint", evidence_path=evidence_path, prompt_id="isolated-prompt")


class LeaseBoundary:
    def __init__(self):
        self.acquired = 0
        self.released = 0

    def acquire(self, **kwargs):
        self.acquired += 1
        return "isolated-lease"

    def release(self, lease):
        assert lease == "isolated-lease"
        self.released += 1


@pytest.fixture
def harness(monkeypatch, tmp_path):
    root = tmp_path / "generated"
    profile = SimpleNamespace(channel_id="wealth_blueprint", output_subdir="wealth_blueprint",
                              image_models=[BASE], reference_files=[])
    workflow = SimpleNamespace(workflow_id="wealth_background_v1", workflow_version="v1",
                               title="WB_BACKGROUND", asset_type="background", default_width=768,
                               default_height=1344, require_references=False, allow_transparent_bg=False,
                               forbid_text_in_image=True, backend_preference=["checkpoint"])
    monkeypatch.setattr(channel_service, "load_profile", lambda *args: profile)
    monkeypatch.setattr(channel_service, "find_workflow", lambda *args: workflow)
    monkeypatch.setattr(channel_service, "validate_workflow_for_generation", lambda *args: {"ok": True})
    monkeypatch.setattr(channel_service, "assert_request_isolated", lambda *args: None)
    monkeypatch.setattr(channel_service, "assert_output_path_isolated", lambda *args: None)
    monkeypatch.setattr(channel_service, "generated_root", lambda *args: root)
    monkeypatch.setattr(channel_service, "_register_wealth_asset_brain", lambda *args, **kwargs: "pending-boundary")
    monkeypatch.setattr("atnos.production.comfy_channels.model_profiles.load_model_profile", lambda *args: {
        "fast_model": TURBO, "quality_model": BASE, "primary_model": TURBO,
        "quality_fallback_model": TURBO, "fallback_model": TURBO,
        "quality_asset_types": ["object"], "fast_asset_types": ["background"],
    })
    monkeypatch.setattr("atnos.production.comfy_channels.quality_gate.run_asset_quality_gate",
                        lambda **kwargs: AssetQualityGateResult(passed=True, verdict="VALIDATED", checks=[]))
    svc = channel_service.ChannelComfyGenerationService.__new__(channel_service.ChannelComfyGenerationService)
    svc.settings = SimpleNamespace()
    svc.images = ImageBoundary()
    svc.lock = LeaseBoundary()
    return svc, root


def request(style="A", asset_type="background"):
    fields = dict(channel="wealth_blueprint", workflow="WB_BACKGROUND", subject="open paper envelope",
                  composition="One envelope in the middle", asset_type=asset_type, seed=17)
    if style:
        fields.update(visual_style_id=style, visual_style_prompt="Mature hand-drawn navy ink editorial illustration",
                      visual_style_tags=[f"wb-style-{style.lower()}"])
    return GenerationRequest(**fields)


@pytest.mark.parametrize("style", ["A", "B", "C", "D"])
def test_required_style_runtime_unavailable_is_not_style_quality_failure(harness, style):
    svc, root = harness
    svc.images.failures = 1
    svc.images.failure_error = ComfyUiRuntimeUnavailableError("ComfyUI runtime did not become ready")
    with pytest.raises(channel_service.ChannelComfyRuntimeUnavailableError, match="COMFY_RUNTIME_UNAVAILABLE") as error:
        svc.generate(request(style))
    assert len(svc.images.calls) == 1
    assert svc.images.calls[0]["preferred_checkpoint"] == BASE
    assert svc.images.calls[0]["quality_mode"] is True
    assert "WB_STYLE_QUALITY_BLOCKED" not in str(error.value)
    failure_files = list(root.rglob("runtime_failure.json"))
    assert len(failure_files) == 1
    evidence = json.loads(failure_files[0].read_text())
    assert evidence["code"] == "COMFY_RUNTIME_UNAVAILABLE"
    assert evidence["image_generated"] is False
    assert evidence["requested_checkpoint"] == BASE
    assert evidence["fallback_allowed"] is False
    assert evidence["fallback_used"] is False
    assert not list(root.rglob("metadata.json"))
    assert not list(root.rglob("_cache.json"))
    assert not list(root.rglob("generation_failure.json"))
    assert svc.lock.acquired == svc.lock.released == 1


def test_required_style_checkpoint_failure_remains_style_quality_block(harness):
    svc, root = harness
    svc.images.failures = 1
    svc.images.failure_error = ComfyUiImageGenerationError("checkpoint loading failed")
    with pytest.raises(channel_service.ChannelComfyGenerationError, match="WB_STYLE_QUALITY_BLOCKED"):
        svc.generate(request("A"))
    receipts = list(root.rglob("generation_failure.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text())["code"] == "WB_STYLE_QUALITY_BLOCKED"
    assert not list(root.rglob("runtime_failure.json"))
    assert len(svc.images.calls) == 1
    assert not list(root.rglob("metadata.json"))
    assert svc.lock.released == 1


def test_required_quality_success_keeps_actual_and_requested_tier(harness):
    svc, _ = harness
    meta = svc.generate(request())
    assert meta.model == BASE
    assert meta.generation_settings["generation_tier"] == "quality"
    assert meta.generation_settings["requested_generation_tier"] == "quality"
    assert meta.generation_settings["quality_mode"] is True
    assert meta.generation_settings["fallback_used"] is False
    assert meta.generation_settings["requested_checkpoint"] == BASE
    assert meta.generation_settings["approved"] is False
    assert svc.lock.released == 1


def test_required_quality_checks_actual_graph_not_declared_tier(harness):
    svc, root = harness
    svc.images.actual_checkpoint = TURBO
    with pytest.raises(channel_service.ChannelComfyGenerationError, match="Generated graph"):
        svc.generate(request())
    assert len(svc.images.calls) == 1
    assert not list(root.rglob("metadata.json"))
    assert svc.lock.released == 1


def test_required_quality_cache_cannot_reuse_legacy_fast_candidate(harness):
    svc, root = harness
    meta = svc.generate(request())
    cache = ChannelGenerationCache(root / "wealth_blueprint" / "_cache.json")
    old = meta.model_dump(mode="json")
    old["model"] = TURBO
    old["generation_settings"].update(quality_mode=False, generation_tier="quality", fallback_used=True)
    cache.put(meta.fingerprint, old)
    original_cache = cache.path.read_bytes()
    with pytest.raises(channel_service.ChannelComfyGenerationError, match="Cached candidate"):
        svc.generate(request())
    assert len(svc.images.calls) == 1
    assert cache.path.read_bytes() == original_cache
    assert svc.lock.acquired == svc.lock.released == 1


def test_required_quality_valid_cache_reuses_without_gpu(harness):
    svc, _ = harness
    first = svc.generate(request())
    cached = svc.generate(request())
    assert cached.cache_hit is True
    assert cached.output_path == first.output_path
    assert len(svc.images.calls) == 1
    assert svc.lock.acquired == svc.lock.released == 1


def test_required_quality_cache_checks_graph_even_if_metadata_claims_quality(harness):
    svc, _ = harness
    meta = svc.generate(request())
    evidence_path = Path(meta.generation_settings["evidence_path"])
    evidence = json.loads(evidence_path.read_text())
    evidence["models"] = [Path(TURBO).name]
    evidence["workflow"]["1"]["inputs"]["ckpt_name"] = Path(TURBO).name
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(channel_service.ChannelComfyGenerationError, match="Generated graph"):
        svc.generate(request())
    assert len(svc.images.calls) == 1


def test_unstyled_wb_quality_fallback_retains_requested_and_actual_tier(harness, monkeypatch):
    svc, _ = harness
    # An older ordinary WB editorial workflow retains its explicit fallback.
    monkeypatch.setattr(channel_service, "find_workflow", lambda *args: SimpleNamespace(
        workflow_id="wealth_editorial_v1", workflow_version="v1", title="WB_EDITORIAL_SCENE",
        asset_type="object", default_width=768, default_height=1344, require_references=False,
        allow_transparent_bg=False, forbid_text_in_image=True, backend_preference=["checkpoint"]))
    svc.images.failures = 1
    meta = svc.generate(request(style=None, asset_type="object"))
    assert [call["preferred_checkpoint"] for call in svc.images.calls] == [BASE, TURBO]
    assert meta.model == TURBO
    assert meta.generation_settings["generation_tier"] == "fast"
    assert meta.generation_settings["requested_generation_tier"] == "quality"
    assert meta.generation_settings["requested_checkpoint"] == BASE
    assert meta.generation_settings["fallback_used"] is True
    assert "no PyTorch" in meta.generation_settings["fallback_error"]
    assert svc.lock.acquired == svc.lock.released == 1


def test_required_style_without_quality_profile_blocks_before_gpu(harness, monkeypatch):
    svc, _ = harness
    monkeypatch.setattr("atnos.production.comfy_channels.model_profiles.load_model_profile", lambda *args: {
        "primary_model": TURBO, "fallback_model": TURBO,
    })
    with pytest.raises(channel_service.ChannelComfyGenerationError, match="configured quality checkpoint"):
        svc.generate(request())
    assert not svc.images.calls
    assert svc.lock.acquired == svc.lock.released == 0
