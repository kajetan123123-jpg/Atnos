"""Contract checks for bounded, unapproved Qwen voice comparisons (no model loads)."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import uuid
import wave

import pytest

from atnos.production.local_voice_lane import NEUTRAL_INSTRUCTION
from atnos.services import qwen_tts_local_runtime_service as qwen


def _write_json(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def comparison(monkeypatch):
    root = Path(r"E:\Atnos\App\tmp") / f"qwen-comparison-contract-{uuid.uuid4().hex}"
    report = root / "reports" / "wb-batch-20261013"
    comparison_root = report / "voice-comparison"
    job = qwen.COMPARISON_SOURCE_JOB_ID
    script_root = root / "data" / "production" / "wealth_blueprint"
    source = _write_json(script_root / job / "script.json", {"segments": [
        {"id": "s01", "text": "You think one account covers both needs."},
        {"id": "s02", "text": "But money has two distinct jobs right now."},
        {"id": "s03", "text": "Name the emergency reserve separately."},
    ]})
    hold = _write_json(report / "quality-hold.json", {
        "batch_id": "wb-batch-20261013", "status": "USER_REJECTED_APPEARANCE_AND_VOICE",
        "hold_new_dispatch_and_delivery": True,
        "user_feedback": "The new voice sounds slurred.",
        "affected_delivered_jobs": [job],
    })
    model_dir = root / "model"
    _write_json(model_dir / "config.json", {"talker_config": {
        "spk_id": {"ryan": 3061, "aiden": 2861, "dylan": 2878},
        "spk_is_dialect": {"ryan": False, "aiden": False, "dylan": "beijing_dialect"},
    }})
    provenance = _write_json(model_dir / "model-provenance.json", {
        "model_id": qwen.MODEL_ID, "revision": qwen.MODEL_REVISION,
        "license": "apache-2.0", "verified_files": True,
    })
    python = root / "runtime" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(qwen, "COMPARISON_ROOT", comparison_root)
    monkeypatch.setattr(qwen, "COMPARISON_HOLD_PATH", hold)
    monkeypatch.setattr(qwen, "COMPARISON_SCRIPT_ROOT", script_root)
    monkeypatch.setattr(qwen, "MODEL_DIR", model_dir)
    monkeypatch.setattr(qwen, "PROVENANCE_PATH", provenance)
    monkeypatch.setattr(qwen, "RUNTIME_PYTHON", python)
    monkeypatch.setattr(qwen, "_verified_model_files", lambda: True)
    text = "You think one account covers both needs. But money has two distinct jobs right now."
    request = {
        "schema_version": 1, "status": "candidate_unapproved", "user_approved": False,
        "production_enabled": False, "cost_usd": 0,
        "quality_hold_path": str(hold.resolve()), "quality_hold_sha256": _sha(hold),
        "user_feedback": "The new voice sounds slurred.",
        "source_job_id": job, "source_script_path": str(source.resolve()),
        "source_script_sha256": _sha(source), "segment_ids": ["s01", "s02"], "text": text,
        "model_id": qwen.MODEL_ID, "model_revision": qwen.MODEL_REVISION, "language": "English",
        "samples": [
            {"sample_id": key, "speaker": speaker, "preset": preset}
            for key, (speaker, preset) in qwen.COMPARISON_SAMPLES.items()
        ],
    }
    request_file = comparison_root / "request.json"
    _write_json(request_file, request)
    calls: list[dict] = []

    def child(command, input_text, environment, cwd, timeout_seconds):
        payload = json.loads(input_text)
        calls.append(payload)
        assert command[0] == str(python)
        assert environment["HF_HUB_OFFLINE"] == "1"
        assert environment["CUDA_VISIBLE_DEVICES"] == ""
        output = Path(payload["output_path"])
        with wave.open(str(output), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(8000)
            wav.writeframes(b"\x00\x00" * 800)
        worker = {
            "model_id": qwen.MODEL_ID, "revision": qwen.MODEL_REVISION,
            "device": "cpu", "torch_cuda_available": False,
            "output_path": str(output), "speaker": payload["speaker"],
            "language": payload["language"], "instruction": payload["instruction"],
            "sample_rate_hz": 8000, "channels": 1, "duration_seconds": 0.1,
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(worker), "")

    monkeypatch.setattr(qwen, "_run_qwen_child", child)
    try:
        yield request_file, request, calls, root
    finally:
        resolved = root.resolve()
        if resolved.parent == Path(r"E:\Atnos\App\tmp").resolve():
            shutil.rmtree(resolved, ignore_errors=True)


def _run(request_file: Path, sample_id: str, *, sha: str | None = None, output: Path | None = None):
    destination = output or request_file.parent / "samples" / f"{sample_id}.wav"
    return asyncio.run(qwen.QwenTTSLocalRuntimeService().synthesize_comparison(
        request_path=request_file, request_sha256=sha or _sha(request_file),
        sample_id=sample_id, output_path=destination,
    ))


def test_three_candidate_variants_keep_shared_script_text_and_receipts(comparison):
    request_file, request, calls, _ = comparison
    receipts = [_run(request_file, sample_id) for sample_id in qwen.COMPARISON_SAMPLES]
    assert len(calls) == 3
    assert {call["text"] for call in calls} == {request["text"]}
    assert [(call["speaker"], call["instruction"]) for call in calls] == [
        ("Ryan", NEUTRAL_INSTRUCTION),
        ("Ryan", qwen.COMPARISON_CLEAR_INSTRUCTION),
        ("Aiden", qwen.COMPARISON_CLEAR_INSTRUCTION),
    ]
    for receipt in receipts:
        assert receipt["status"] == "candidate_unapproved"
        assert receipt["user_approved"] is False and receipt["production_enabled"] is False
        assert receipt["cost_usd"] == 0
        assert receipt["request_sha256"] == _sha(request_file)
        assert receipt["output_sha256"] == _sha(Path(receipt["output_path"]))
        assert receipt["source_script_sha256"] == request["source_script_sha256"]
        assert receipt["model_provenance_sha256"] == _sha(Path(receipt["model_provenance_path"]))
        assert json.loads(Path(receipt["output_path"]).with_suffix(".json").read_text(encoding="utf-8")) == receipt
    assert [receipt["preset"] for receipt in receipts] == [
        "ryan-conversational-v2-neutral", "clear_conversation_v1", "clear_conversation_v1",
    ]
    with pytest.raises(qwen.QwenTTSLocalRuntimeError, match="already exists"):
        _run(request_file, "ryan_neutral")
    assert len(calls) == 3


@pytest.mark.parametrize("change, message", [
    (lambda item: item.update(user_approved=True), "unapproved"),
    (lambda item: item.update(production_enabled=True), "unapproved"),
    (lambda item: item.update(text="Invented narration."), "exactly match"),
    (lambda item: item.update(segment_ids=["s02", "s01"]), "ordered"),
    (lambda item: item.update(quality_hold_sha256="0" * 64), "quality hold"),
    (lambda item: item["samples"][0].update(speaker="Dylan"), "allowlisted"),
    (lambda item: item["samples"][0].update(preset="clear_conversation_v1"), "allowlisted"),
    (lambda item: item["samples"].append(dict(item["samples"][0])), "unique"),
])
def test_rejects_tampered_authority_text_or_candidate_before_dispatch(comparison, change, message):
    request_file, request, calls, _ = comparison
    change(request)
    _write_json(request_file, request)
    with pytest.raises(qwen.QwenTTSLocalRuntimeError, match=message):
        _run(request_file, "ryan_neutral")
    assert calls == []


def test_rejects_request_hash_and_output_escape_before_dispatch(comparison):
    request_file, _, calls, root = comparison
    with pytest.raises(qwen.QwenTTSLocalRuntimeError, match="SHA-256 changed"):
        _run(request_file, "ryan_neutral", sha="0" * 64)
    with pytest.raises(qwen.QwenTTSLocalRuntimeError, match="sample-scoped"):
        _run(request_file, "ryan_neutral", output=root / "outside.wav")
    assert calls == []


def test_rejects_overlong_source_excerpt_before_dispatch(comparison):
    request_file, request, calls, _ = comparison
    source = Path(request["source_script_path"])
    script = json.loads(source.read_text(encoding="utf-8"))
    script["segments"][0]["text"] = "word " * 81
    _write_json(source, script)
    request["source_script_sha256"] = _sha(source)
    request["text"] = script["segments"][0]["text"].strip() + " " + script["segments"][1]["text"]
    _write_json(request_file, request)
    with pytest.raises(qwen.QwenTTSLocalRuntimeError, match="80 words"):
        _run(request_file, "ryan_neutral")
    assert calls == []


def test_rejects_changed_source_or_unsupported_local_speaker_config(comparison):
    request_file, request, calls, root = comparison
    source = Path(request["source_script_path"])
    source.write_text(source.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(qwen.QwenTTSLocalRuntimeError, match="source script path or SHA-256 changed"):
        _run(request_file, "ryan_neutral")
    request["source_script_sha256"] = _sha(source)
    _write_json(request_file, request)
    config_path = root / "model" / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["talker_config"]["spk_is_dialect"]["aiden"] = "unexpected_dialect"
    _write_json(config_path, config)
    with pytest.raises(qwen.QwenTTSLocalRuntimeError, match="does not support"):
        _run(request_file, "aiden_clear")
    assert calls == []


@pytest.mark.parametrize("mismatch", ["model_id", "revision", "device", "output_path"])
def test_rejects_worker_identity_mismatch_and_preserves_failure_receipt(comparison, monkeypatch, mismatch):
    request_file, _, _, _ = comparison
    def bad_child(command, input_text, *_):
        payload = json.loads(input_text)
        worker = {"model_id": qwen.MODEL_ID, "revision": qwen.MODEL_REVISION, "device": "cpu",
                  "torch_cuda_available": False, "output_path": payload["output_path"],
                  "speaker": payload["speaker"], "language": "English", "instruction": payload["instruction"]}
        worker[mismatch] = "wrong"
        return subprocess.CompletedProcess(command, 0, json.dumps(worker), "")
    monkeypatch.setattr(qwen, "_run_qwen_child", bad_child)
    with pytest.raises(qwen.QwenTTSLocalRuntimeError, match="did not confirm"):
        _run(request_file, "ryan_neutral")
    receipt = json.loads((request_file.parent / "samples" / "ryan_neutral.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "failed"
    assert receipt["request_sha256"] == _sha(request_file)


@pytest.mark.parametrize("preset,instruction", [
    ("ryan-measured-professional-v1", qwen.RYAN_MEASURED_PROFESSIONAL_V1),
    ("ryan-conversational-v2", qwen.RYAN_CONVERSATIONAL_V2),
])
def test_old_preview_preset_contract_still_works_without_model(comparison, monkeypatch, preset, instruction):
    _, _, _, root = comparison
    preview_root = root / "old-preview"
    monkeypatch.setattr(qwen, "PREVIEW_ROOT", preview_root)
    provenance = _write_json(root / "model" / "model-provenance.json", {
        "model_id": qwen.MODEL_ID, "revision": qwen.MODEL_REVISION,
        "license": "apache-2.0", "verified_files": True,
    })
    monkeypatch.setattr(qwen, "PROVENANCE_PATH", provenance)
    def preview_child(command, **kwargs):
        payload = json.loads(kwargs["input"])
        assert payload["speaker"] == "Ryan"
        assert payload["instruction"] == instruction
        return subprocess.CompletedProcess(command, 0, json.dumps({
            "model_id": qwen.MODEL_ID, "device": "cpu", "output_path": payload["output_path"],
        }), "")
    monkeypatch.setattr(qwen.subprocess, "run", preview_child)
    result = asyncio.run(qwen.QwenTTSLocalRuntimeService().synthesize_preview(
        text=qwen.EXPECTED_TEXT, output_path=preview_root / "legacy.wav", preset=preset, preview=True,
    ))
    assert result["device"] == "cpu"
    with pytest.raises(qwen.QwenTTSLocalRuntimeError, match="only permits"):
        asyncio.run(qwen.QwenTTSLocalRuntimeService().synthesize_preview(
            text="different", output_path=preview_root / "wrong.wav", preview=True,
        ))
