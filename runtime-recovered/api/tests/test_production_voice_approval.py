from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from atnos.production.channel_profile import WEALTH_BLUEPRINT_PROFILE
from atnos.production.qa_gate import _check_production_voice_approval
from atnos.production.steps.voice_synthesis import build_voice_identity
from atnos.production.voice_approval import (
    ProductionVoiceApprovalError,
    approve_production_voice,
    production_voice_approval_path,
    verify_production_voice_approval,
)
from test_support import create_test_app


def _settings(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ATNOS_VISUAL_BASELINE_SIGNING_KEY", "v" * 64)
    settings = create_test_app(tmp_path).state.settings
    settings.app_env = "development"
    reference = tmp_path / "f5-reference.wav"
    reference.write_bytes(b"RIFF" + b"reference-voice" * 128)
    settings.f5_tts_ref_audio_path = reference
    settings.f5_tts_ref_text = "Reference voice sentence."
    settings.f5_tts_speed = 1.0
    settings.f5_tts_nfe_steps = 32
    return settings


def _manifest(tmp_path: Path) -> dict:
    sample = tmp_path / "narration.wav"
    sample.write_bytes(b"RIFF" + b"approved-sample" * 256)
    return {
        "status": "generated",
        "primary": "f5_tts_single_pass",
        "quality_tier": "primary",
        "production_ready": True,
        "output_file": str(sample),
        "channel_profile": {"key": "wealth_blueprint", "language_code": "en"},
        "voice_profile": {
            "channel_profile_key": "wealth_blueprint",
            "language_code": "en",
            "narration_style": "calm confident finance educator",
            "performance_direction": "confident and conversational",
        },
        "engine_attempts": [{"engine": "f5_tts_http", "status": "generated"}],
    }


def test_voice_identity_changes_when_f5_reference_audio_changes(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path, monkeypatch)
    manifest = _manifest(tmp_path)
    first = build_voice_identity(settings, manifest)

    settings.f5_tts_ref_audio_path.write_bytes(b"RIFF" + b"different-reference" * 128)
    second = build_voice_identity(settings, manifest)

    assert first["provider_family"] == "f5_tts"
    assert first["reference_audio_sha256"] != second["reference_audio_sha256"]
    assert first["identity_hash"] != second["identity_hash"]


def test_signed_voice_approval_passes_only_for_same_identity(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path, monkeypatch)
    manifest = _manifest(tmp_path)

    approval = approve_production_voice(settings, manifest, reviewer="operator", approval_note="Gate A accepted")
    verified = verify_production_voice_approval(settings, manifest)

    assert production_voice_approval_path(settings).is_file()
    assert approval.approval_status == "production_approved"
    assert verified["approved"] is True
    assert verified["voice_status"] == "production_approved"

    settings.f5_tts_speed = 1.1
    changed = verify_production_voice_approval(settings, manifest)
    assert changed["approved"] is False
    assert changed["status"] == "identity_mismatch"


def test_tampered_voice_approval_is_rejected(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path, monkeypatch)
    manifest = _manifest(tmp_path)
    approve_production_voice(settings, manifest, reviewer="operator")
    path = production_voice_approval_path(settings)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["reviewer"] = "attacker"
    path.write_text(json.dumps(payload), encoding="utf-8")

    verified = verify_production_voice_approval(settings, manifest)

    assert verified["approved"] is False
    assert verified["status"] == "invalid"


def test_failed_f5_attempt_cannot_promote_edge_fallback(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path, monkeypatch)
    manifest = _manifest(tmp_path)
    manifest["primary"] = "elevenlabs_openai_or_f5_tts"
    manifest["quality_tier"] = "cloud_neural_fallback"
    manifest["engine_attempts"] = [
        {"engine": "f5_tts_http", "status": "failed"},
        {"engine": "edge_tts", "status": "generated"},
    ]

    identity = build_voice_identity(settings, manifest)

    assert identity["provider_family"] == "edge_tts"
    with pytest.raises(ProductionVoiceApprovalError, match="not eligible"):
        approve_production_voice(settings, manifest, reviewer="operator")


def test_mixed_segment_providers_cannot_receive_single_voice_approval(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path, monkeypatch)
    manifest = _manifest(tmp_path)
    manifest["primary"] = "elevenlabs_openai_or_f5_tts"
    manifest["engine_attempts"] = [
        {"engine": "f5_tts_http", "status": "generated"},
        {"engine": "edge_tts", "status": "generated"},
    ]

    identity = build_voice_identity(settings, manifest)

    assert identity["provider_family"] == "mixed"
    with pytest.raises(ProductionVoiceApprovalError, match="not eligible"):
        approve_production_voice(settings, manifest, reviewer="operator")


def test_runtime_qa_blocks_until_voice_is_explicitly_approved(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path, monkeypatch)
    manifest = _manifest(tmp_path)

    blocked = _check_production_voice_approval(
        settings,
        manifest,
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    )
    approve_production_voice(settings, manifest, reviewer="operator")
    passed = _check_production_voice_approval(
        settings,
        manifest,
        channel_profile=WEALTH_BLUEPRINT_PROFILE,
    )

    assert blocked["status"] == "fail"
    assert blocked["value"]["voice_status"] == "candidate"
    assert passed["status"] == "pass"
    assert passed["value"]["voice_status"] == "production_approved"
