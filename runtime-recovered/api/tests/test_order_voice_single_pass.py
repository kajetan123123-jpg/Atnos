from __future__ import annotations

import asyncio
from collections import Counter
from copy import deepcopy
import json
from types import SimpleNamespace
import wave

import pytest

from atnos.production.order_execution import StageExecutor
from atnos.production.project_profile import ProductionOrder, content_hash
from atnos.production.steps import order_voice
from atnos.production.steps.common import write_json
from atnos.production.narration_revision import NarrationRevisionRequired
from atnos.production.order_store import ProductionBlocked, checkpoint_resolved
from test_production_order_script import script
from test_production_order_store import store


def character_alignment(segments, duration=40.0):
    chars, starts, ends = [], [], []
    cursor = .25
    for index, segment in enumerate(segments):
        text = segment["text"]
        step = 5.7 / len(text)
        for char in text:
            chars.append(char)
            starts.append(cursor)
            cursor += step
            ends.append(cursor)
        if index < len(segments) - 1:
            for char in "\n\n":
                chars.append(char)
                starts.append(cursor)
                cursor += .375
                ends.append(cursor)
    factor = duration / 40
    return {"characters": chars, "character_start_times_seconds": [t * factor for t in starts],
            "character_end_times_seconds": [t * factor for t in ends]}


def prepare(store, script, monkeypatch, *, duration=40.0, first_asr_wrong=False):
    monkeypatch.setenv("DATA_DIR", str(store.settings.data_dir))
    job = store.create(ProductionOrder(topic="A complete narration test", delivery_mode="local_only", voice_candidate="Roger-v3"))["id"]
    payload = script.model_dump(mode="json")
    payload["qa"] = {"status": "passed", "revision": 0, "script_hash": content_hash(script.model_dump(mode="json"))}
    counters = Counter()
    monkeypatch.setattr(order_voice, "WhisperCppSubtitleService", lambda *_: SimpleNamespace(runtime_availability=lambda: SimpleNamespace(available=True)))
    monkeypatch.setattr(order_voice, "resolve_ffmpeg", lambda: "test-ffmpeg")
    original_settings = order_voice.voice_settings
    monkeypatch.setattr(order_voice, "voice_settings", lambda settings, config: original_settings(settings, config).model_copy(update={"elevenlabs_api_key": "test-key-not-real", "shorts_allow_cloud_tts": True}))

    async def provider(settings, *, text, output_path, voice_profile, with_timestamps, **kwargs):
        counters["provider"] += 1
        assert settings.elevenlabs_model_id == "eleven_v3"
        assert voice_profile["elevenlabs_voice_id"] == "CwhRBWXzGAHq8TQ4Fs17"
        assert text == "\n\n".join(s["text"] for s in payload["segments"])
        assert with_timestamps is True
        assert not kwargs.get("previous_text") and not kwargs.get("next_text")
        output_path.write_bytes(b"provider-recording-" + str(counters["provider"]).encode())
        return {"ok": True, "engine": "elevenlabs", "model_id": "eleven_v3", "voice_id": "CwhRBWXzGAHq8TQ4Fs17",
                "alignment": character_alignment(payload["segments"], duration)}

    def decode(ffmpeg, raw, pcm):
        counters["decode"] += 1
        with wave.open(str(pcm), "wb") as audio:
            audio.setnchannels(2)
            audio.setsampwidth(2)
            audio.setframerate(44100)
            audio.writeframes(bytes([counters["provider"], 0, 0, 0]) * round(44100 * duration))

    def transcribe(settings, recording, output):
        counters["asr"] += 1
        text = "\n\n".join(s["text"] for s in payload["segments"])
        if first_asr_wrong and counters["asr"] == 1:
            text = "An unrelated and incorrect narration was heard."
        output.write_text(text, encoding="utf-8")
        return text

    async def normalize(raw, output):
        counters["normalize"] += 1
        output.write_bytes(raw.read_bytes())
        return {"status": "normalized"}

    monkeypatch.setattr(order_voice.voice_synthesis, "_try_elevenlabs_tts", provider)
    monkeypatch.setattr(order_voice, "decode_pcm", decode)
    monkeypatch.setattr(order_voice, "transcribe", transcribe)
    monkeypatch.setattr(order_voice.voice_synthesis, "_normalize_audio", normalize)

    async def run(token):
        executor = StageExecutor(store, job, token)
        executor.context.artifacts["script"] = payload
        if store.begin(job, token, "script", {"fixture": "single-pass-voice"}) is None:
            result = write_json(executor.context.artifact_dir / "script.json", payload)
            store.complete(job, token, "script", payload, [result.artifact_path])
        return await order_voice._produce_voice_once(executor)

    return job, payload, counters, run


def test_v3_single_recording_preserves_provider_pauses_all_scenes_and_restart_cache(store, script, monkeypatch):
    job, payload, counters, run = prepare(store, script, monkeypatch)
    with store.lease(job) as token:
        first = asyncio.run(run(token))
    assert counters == Counter(provider=1, decode=1, asr=1, normalize=1)
    assert first["single_pass"] is True
    assert first["qa"]["status"] == "passed"
    assert first["qa"]["segments"][0]["scope"] == "full_narration"
    assert first["qa"]["segments"][0]["human_listening"] == "not_performed"
    assert first["duration_seconds"] == 40.0
    assert first["pacing"]["time_stretch"] is False
    assert first["pacing"]["natural_provider_pauses_preserved"] is True
    assert first["source_recording"]["provider_sha256"]
    assert [t["segment_id"] for t in first["timestamps"]] == [s["id"] for s in payload["segments"]]
    assert all(t["single_pass"] for t in first["timestamps"])
    assert first["timestamps"][0]["start_sec"] == 0
    assert first["timestamps"][-1]["end_sec"] == 40
    # The gap belongs to the original recording, not an inserted assembly pause.
    gap = first["timestamps"][1]["start_sec"] - first["timestamps"][0]["speech_end_sec"]
    assert gap == pytest.approx(.75)
    assert first["timestamps"][0]["end_sec"] == first["timestamps"][1]["start_sec"]
    assert [w["text"] for w in first["word_timestamps"]] == [w for s in payload["segments"] for w in s["text"].split()]
    with store.lease(job) as token:
        second = asyncio.run(run(token))
    assert second == first
    assert counters == Counter(provider=1, decode=1, asr=1, normalize=1)
    assert store.get(job)["provider_calls"] == 1
    stages = {s["stage"] for s in store.get(job)["checkpoints"]}
    assert {"voice/narration", "voice_qa/narration", "voice"}.issubset(stages)
    assert "voice/s01" not in stages


def test_v3_failed_independent_asr_regenerates_recording_with_durable_cost_history(store, script, monkeypatch):
    job, _, counters, run = prepare(store, script, monkeypatch, first_asr_wrong=True)
    with store.lease(job) as token:
        voice = asyncio.run(run(token))
    assert counters == Counter(provider=2, decode=2, asr=2, normalize=1)
    assert voice["qa"]["status"] == "passed"
    order = store.get(job)
    assert order["provider_calls"] == 2
    assert order["reserved_cost_units"] > 0
    narration = next(s for s in order["checkpoints"] if s["stage"] == "voice/narration")
    assert narration["attempts"] == 2


@pytest.mark.parametrize("failure", ["too_short", "no_practical_content"])
def test_v3_retains_measured_duration_and_practical_content_gates(store, script, monkeypatch, failure):
    if failure == "no_practical_content":
        for segment in script.segments:
            if segment.type == "practical":
                segment.type = "explanation"
    job, _, counters, run = prepare(store, script, monkeypatch, duration=30 if failure == "too_short" else 40)
    with store.lease(job) as token, pytest.raises(NarrationRevisionRequired) as error:
        asyncio.run(run(token))
    assert counters["provider"] == counters["asr"] == 1
    assert error.value.report["required_duration"] == [35, 50]
    assert error.value.report["required_practical_share"] >= 1 / 3
    assert error.value.report["next_stage"] == "script_repair"
    assert store.get(job)["provider_calls"] == 1
    assert not store.get(job)["status"] == "ready"


def add_failed_legacy_clip(store, job, payload):
    with store.lease(job) as token:
        if store.begin(job, token, "script", {"fixture": "single-pass-voice"}) is None:
            result = write_json(store.context(job).artifact_dir / "script.json", payload)
            store.complete(job, token, "script", payload, [result.artifact_path])
        store.begin(job, token, "voice/s01", {"segment": payload["segments"][0], "contract": 1},
                    provider_call=True, estimated_cost_usd=.2)
        store.fail(job, token, "voice/s01", category="dependency_failure", message="HTTP 400: unsupported v3 stitching")


def test_failed_v3_clip_is_explicitly_retired_only_after_full_proven_narration(store, script, monkeypatch):
    job, payload, _, run = prepare(store, script, monkeypatch)
    add_failed_legacy_clip(store, job, payload)
    with store.lease(job) as token, pytest.raises(ProductionBlocked, match="intact script"):
        store.reconcile_single_pass_voice_checkpoints(job, token)
    before = store.get(job)
    previous = next(row for row in before["checkpoints"] if row["stage"] == "voice/s01")
    with store.lease(job) as token:
        asyncio.run(run(token))
    after = store.get(job)
    retired = next(row for row in after["checkpoints"] if row["stage"] == "voice/s01")
    audit = json.loads(retired["output_json"])
    assert retired["status"] == "superseded"  # The rejected provider request never becomes PASS.
    assert retired["attempts"] == previous["attempts"] == 1
    assert retired["input_hash"] == previous["input_hash"]
    assert retired["error_json"] == previous["error_json"]
    assert audit["previous_error"]["message"] == "HTTP 400: unsupported v3 stitching"
    assert audit["previous_status"] == "failed"
    assert set(audit["superseded_by"]["dependencies"]) == {"script", "voice/narration", "voice_qa/narration", "voice"}
    assert checkpoint_resolved(retired, after["checkpoints"])
    assert after["provider_calls"] == before["provider_calls"] + 1 == 2
    assert after["reserved_cost_units"] > before["reserved_cost_units"]

    spoofed = deepcopy(after["checkpoints"])
    bad = next(row for row in spoofed if row["stage"] == "voice/s01")
    forged = json.loads(bad["output_json"])
    forged["superseded_by"]["dependencies"]["voice_qa/narration"]["output_hash"] = "forged"
    bad["output_json"] = json.dumps(forged)
    assert not checkpoint_resolved(bad, spoofed)

    # A legitimate replacement hash is also invalid if its file evidence changes.
    (store.context(job).artifact_dir / "audio/narration/provider.mp3").write_bytes(b"modified after QA")
    with store.lease(job) as token, pytest.raises(ProductionBlocked, match="intact script"):
        store.reconcile_single_pass_voice_checkpoints(job, token)


def test_voice_branch_proof_rebind_preserves_history_after_valid_recording_regeneration(store, script, monkeypatch):
    job, payload, counters, run = prepare(store, script, monkeypatch)
    add_failed_legacy_clip(store, job, payload)
    with store.lease(job) as token:
        first = asyncio.run(run(token))
    original = next(row for row in store.get(job)["checkpoints"] if row["stage"] == "voice/s01")
    proof = json.loads(original["output_json"])["superseded_by"]
    # Normal cache validation notices a changed source and regenerates it; a new
    # independent ASR pass is required before the old branch can point to it.
    (store.context(job).artifact_dir / "audio/narration/provider.mp3").write_bytes(b"invalid cached source")
    with store.lease(job) as token:
        second = asyncio.run(run(token))
    after = store.get(job)
    retired = next(row for row in after["checkpoints"] if row["stage"] == "voice/s01")
    audit = json.loads(retired["output_json"])
    assert counters["provider"] == counters["asr"] == 2
    assert first["source_recording"]["provider_sha256"] != second["source_recording"]["provider_sha256"]
    assert audit["superseded_history"] == [proof]
    assert audit["previous_error"]["message"] == "HTTP 400: unsupported v3 stitching"
    assert audit["attempts_preserved"] == 1
    assert checkpoint_resolved(retired, after["checkpoints"])
    assert after["provider_calls"] == 3
