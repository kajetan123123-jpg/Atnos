from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct

import pytest

from atnos.core.config import get_settings
from atnos.production.comfy_channels import illustration_checkpoint as subject
from atnos.production.comfy_channels import model_profiles
from atnos.production.comfy_channels import profiles


def tiny_checkpoint(path: Path, *, keys=None, bad_extent=False):
    keys = keys if keys is not None else subject.REQUIRED_TENSORS
    header = {key: {"dtype": "F16", "shape": [1], "data_offsets": [0, 90 if bad_extent else 2]}
              for key in keys}
    data = json.dumps(header).encode()
    payload = struct.pack("<Q", len(data)) + data + b"\x00\x00"
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest(), len(payload)


def test_full_checkpoint_header_and_hash_without_torch(tmp_path):
    path = tmp_path / "full.safetensors"
    digest, size = tiny_checkpoint(path)
    result = subject.verify_full_sdxl_checkpoint(path, expected_sha256=digest, expected_bytes=size)
    assert result["components"] == ["unet", "clip_l", "clip_g", "vae"]
    assert result["tensor_count"] == 4


@pytest.mark.parametrize("case, message", [
    ("unet_only", "Not a full SDXL"), ("corrupt_extent", "Invalid tensor extent"),
    ("wrong_sha", "SHA256 mismatch"), ("truncated", "missing or truncated"),
])
def test_rejects_incomplete_or_tampered_checkpoint(tmp_path, case, message):
    path = tmp_path / "full.safetensors"
    digest, size = tiny_checkpoint(path,
        keys=[subject.REQUIRED_TENSORS[0]] if case == "unet_only" else None,
        bad_extent=case == "corrupt_extent")
    if case == "wrong_sha":
        digest = "0" * 64
    if case == "truncated":
        size += 1
    with pytest.raises(subject.IllustrationCheckpointError, match=message):
        subject.verify_full_sdxl_checkpoint(path, expected_sha256=digest, expected_bytes=size)


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    settings = get_settings().model_copy(update={"app_root": tmp_path})
    root = tmp_path / "models"
    path = root / "checkpoints" / subject.CHECKPOINT_FILE
    path.parent.mkdir(parents=True)
    path.write_bytes(b"fixture")
    monkeypatch.setattr(profiles, "shared_models_root", lambda _settings: root)
    calls = []
    monkeypatch.setattr(subject, "verify_full_sdxl_checkpoint", lambda path: calls.append(path))
    monkeypatch.setenv(subject.TRIAL_ENV, subject.CANDIDATE_ID)
    payload = {
        "schema_version": "wb-illustration-checkpoint-1", "candidate_id": subject.CANDIDATE_ID,
        "checkpoint_path": str(path), "appearance_approval": "pending", "user_approved": False,
        "source": {"repository": subject.REPOSITORY, "revision": subject.REVISION,
                   "filename": subject.SOURCE_FILE},
        "verification": {"sha256": subject.MODEL_SHA256, "size_bytes": subject.MODEL_BYTES},
        "license": {"commercial_images": True},
    }
    receipt = subject.receipt_path(settings)
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps(payload), encoding="utf-8")
    return settings, payload, receipt, path, calls


def test_candidate_opt_in_keeps_pending_approval_and_verifies_weights(candidate):
    settings, _, receipt, path, calls = candidate
    result = subject.load_opt_in_candidate(settings, "wealth_blueprint")
    assert calls == [path]
    assert result["checkpoint"].endswith(subject.CHECKPOINT_FILE)
    assert result["user_approved"] is False
    assert result["appearance_approval"] == "pending"
    assert result["receipt_sha256"] == hashlib.sha256(receipt.read_bytes()).hexdigest()


@pytest.mark.parametrize("field, value", [("user_approved", True), ("appearance_approval", "approved"),
                                         ("candidate_id", "unverified"), ("checkpoint_path", "E:/elsewhere/model.safetensors")])
def test_tampered_candidate_receipt_is_rejected(candidate, field, value):
    settings, payload, receipt, _, _ = candidate
    payload[field] = value
    receipt.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(subject.IllustrationCheckpointError, match="integrity"):
        subject.load_opt_in_candidate(settings, "wealth_blueprint")


def test_unknown_opt_in_fails_closed_and_never_changes_other_channels(candidate, monkeypatch):
    settings, _, _, _, calls = candidate
    monkeypatch.setenv(subject.TRIAL_ENV, "wrong-model")
    assert subject.load_opt_in_candidate(settings, "wonder_crew") is None
    assert subject.load_opt_in_candidate(settings, "dark_chronicles") is None
    with pytest.raises(subject.IllustrationCheckpointError, match="Unknown"):
        subject.load_opt_in_candidate(settings, "wealth_blueprint")
    assert calls == []


def test_default_model_profile_unchanged_without_explicit_opt_in(candidate, monkeypatch):
    settings, _, _, _, calls = candidate
    monkeypatch.delenv(subject.TRIAL_ENV)
    profile = model_profiles.load_model_profile(settings, "wealth_blueprint")
    assert "illustration_trial" not in profile
    assert "turbo" in profile["primary_model"]
    assert calls == []


@pytest.mark.parametrize("asset_type", ["character", "object", "background", "icon"])
def test_opt_in_uses_quality_for_all_episode_assets_with_no_fallback(candidate, asset_type):
    settings, _, _, _, _ = candidate
    profile = model_profiles.load_model_profile(settings, "wealth_blueprint")
    tier = model_profiles.resolve_generation_tier(profile, channel="wealth_blueprint", asset_type=asset_type)
    assert tier["quality_mode"] is True
    assert tier["tier"] == "quality"
    assert tier["preferred_checkpoint"].endswith(subject.CHECKPOINT_FILE)
    assert tier["fallback_checkpoint"] is None
    assert profile["fallback_model"] == ""
    assert profile["required_quality"] is True


def test_opt_in_does_not_rewrite_stored_model_profile(candidate):
    settings, _, _, _, _ = candidate
    path = model_profiles.model_profiles_path(settings)
    path.parent.mkdir(exist_ok=True)
    payload = {"channels": {"wealth_blueprint": {"quality_model": "old-approved.safetensors"}}}
    path.write_text(json.dumps(payload), encoding="utf-8")
    original = path.read_bytes()
    profile = model_profiles.load_model_profile(settings, "wealth_blueprint")
    assert profile["quality_model"].endswith(subject.CHECKPOINT_FILE)
    assert path.read_bytes() == original


def test_explicit_checkpoint_pin_cannot_select_optional_flux_or_zimage(tmp_path, monkeypatch):
    from io import BytesIO
    from PIL import Image
    from atnos.services.comfyui_image_generation_service import ComfyUiImageGenerationService
    from atnos.services.hardware_guard_service import HardwareGuardService
    from atnos.services.production_gpu_residency_service import ProductionGpuResidencyService
    from .test_support import build_test_settings

    service = ComfyUiImageGenerationService(build_test_settings(tmp_path))
    monkeypatch.setattr(HardwareGuardService, "require_gpu", classmethod(lambda cls, *args, **kwargs: None))
    monkeypatch.setattr(ProductionGpuResidencyService, "prepare_for_comfy", lambda self: None)
    monkeypatch.setattr(service.runtime, "ensure_server_running", lambda **kwargs: True)
    monkeypatch.setattr(service, "_runtime_incompatible_backends", lambda: set())
    monkeypatch.setattr(service, "_persistently_blocked_backends", lambda: set())
    def forbid_optional():
        raise AssertionError("An explicit checkpoint must not inspect optional backend models")
    monkeypatch.setattr(service, "resolve_z_image_turbo_models", forbid_optional)
    monkeypatch.setattr(service, "resolve_flux2_klein_models", forbid_optional)
    queued = []
    monkeypatch.setattr(service, "_queue_prompt", lambda workflow: queued.append(workflow) or "test-prompt")
    monkeypatch.setattr(service, "_wait_for_image", lambda *args, **kwargs: {"filename": "test.png", "type": "output"})
    buffer = BytesIO()
    Image.new("RGB", (512, 896), (20, 30, 40)).save(buffer, "PNG")
    monkeypatch.setattr(service, "_download_image", lambda meta: buffer.getvalue())
    result = service.generate_background(prompt="illustrated shopping bag", output_path=tmp_path / "result.png",
        width=512, height=896, seed=42, quality_mode=True, preferred_checkpoint=subject.CHECKPOINT_FILE)
    assert result.backend == "checkpoint"
    assert queued[0]["1"]["class_type"] == "CheckpointLoaderSimple"
    assert queued[0]["1"]["inputs"]["ckpt_name"] == subject.CHECKPOINT_FILE
    assert not any(node["class_type"] == "VAEEncode" for node in queued[0].values())


def test_reference_upload_composites_hidden_rgb_and_preserves_source(tmp_path, monkeypatch):
    from io import BytesIO
    from PIL import Image
    from atnos.services.comfyui_image_generation_service import ComfyUiImageGenerationService
    from .test_support import build_test_settings

    source = tmp_path / "alex.png"
    image = Image.new("RGBA", (2, 1), (25, 160, 30, 0))
    image.putpixel((1, 0), (15, 30, 45, 255))
    image.save(source)
    original = source.read_bytes()
    captured = {}
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"name": captured["name"], "subfolder": "", "type": "input"}
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, *, files, data):
            captured["name"], handle, _ = files["image"]
            captured["bytes"] = handle.read()
            return Response()
    monkeypatch.setattr("atnos.services.comfyui_image_generation_service.httpx.Client", Client)
    service = ComfyUiImageGenerationService(build_test_settings(tmp_path))
    uploaded_name = service._upload_input_image(source)
    uploaded = Image.open(BytesIO(captured["bytes"]))
    assert uploaded.mode == "RGB"
    assert uploaded.getpixel((0, 0)) == (255, 255, 255)
    assert uploaded.getpixel((1, 0)) == (15, 30, 45)
    assert source.read_bytes() == original
    assert uploaded_name.startswith("atnos-ref-")
    evidence = service._reference_upload_evidence
    assert evidence["source_sha256"] == hashlib.sha256(original).hexdigest()
    assert evidence["uploaded_sha256"] == hashlib.sha256(captured["bytes"]).hexdigest()
    assert evidence["alpha_composited"] is True


def test_opaque_reference_is_uploaded_without_reencoding(tmp_path, monkeypatch):
    from PIL import Image
    from atnos.services.comfyui_image_generation_service import ComfyUiImageGenerationService
    from .test_support import build_test_settings

    source = tmp_path / "reference.png"
    Image.new("RGB", (2, 1), (10, 20, 30)).save(source)
    original = source.read_bytes()
    captured = []
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"name": "reference.png"}
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, *, files, data):
            captured.append(files["image"][1].read())
            return Response()
    monkeypatch.setattr("atnos.services.comfyui_image_generation_service.httpx.Client", Client)
    service = ComfyUiImageGenerationService(build_test_settings(tmp_path))
    assert service._upload_input_image(source) == "reference.png"
    assert captured == [original]
    assert service._reference_upload_evidence["alpha_composited"] is False
