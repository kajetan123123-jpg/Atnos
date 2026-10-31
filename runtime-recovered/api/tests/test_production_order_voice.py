from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
import wave

import pytest

from atnos.production.models import StepResult
from atnos.production.order_execution import StageExecutor
from atnos.production.order_store import file_hash
from atnos.production.steps.common import write_json
from atnos.production.steps import order_voice
from test_production_order_script import script
from test_production_order_store import store, create


@pytest.mark.parametrize("project", ["wealth_blueprint", "children_stories", "horror_stories"])
def test_order_voice_uses_frozen_profile_and_keeps_legacy_configuration_unchanged(store, project):
    from atnos.production.steps import voice_synthesis
    settings = store.settings.model_copy(update={"elevenlabs_voice_id": "global-voice",
                                                "elevenlabs_voice_id_wealth_blueprint": "legacy-wealth"})
    config = create(store)["profile"]["production"]["voice"]
    config = {**config, "voice_id": "frozen-" + project}
    context = SimpleNamespace(artifacts={"project_profile": {
        "project": project, "production": {"language": "en", "voice": config}}})
    scoped = order_voice.voice_settings(settings, config)
    selected = order_voice.order_voice_profile(context)
    assert selected["channel_profile_key"] == project
    assert selected["language_code"] == "en"
    assert voice_synthesis._preferred_elevenlabs_voice_id(scoped, voice_profile=selected) == config["voice_id"]
    assert settings.elevenlabs_voice_id == "global-voice"
    assert scoped.elevenlabs_voice_id_wealth_blueprint == "legacy-wealth"


@pytest.mark.parametrize("invalid", ["", None, "../another-voice", "voice?output_format=other"])
def test_invalid_frozen_voice_does_not_fall_back_to_another_channel(store, invalid):
    from atnos.production.steps import voice_synthesis
    settings = store.settings.model_copy(update={"elevenlabs_voice_id": "global-voice"})
    with pytest.raises(ValueError, match="voice ID"):
        voice_synthesis._preferred_elevenlabs_voice_id(settings, voice_profile={"elevenlabs_voice_id": invalid})


@pytest.mark.parametrize("expected,recognized,passed", [
    ("You save twenty dollars each week.", "You save $20 each week.", True),
    ("You save one thousand and forty dollars.", "You save one thousand and four dollars.", False),
    ("Choose a savings account for your next transfer.", "This is an unrelated hallucination about your account.", False),
])
def test_speech_comparison_checks_number_values(expected, recognized, passed):
    assert (order_voice.speech_match(expected, recognized, [])["status"] == "passed") == passed


def test_missing_pronunciation_term_blocks_voice():
    result = order_voice.speech_match("Use the Fidelity account for saving.", "Use the facility account for saving.", ["Fidelity"])
    assert result["status"] == "failed"
    assert result["missing_terms"] == ["Fidelity"]


def test_natural_synthesis_pacing_handles_real_short_script_without_stretching(store):
    from copy import deepcopy
    profile = create(store)['profile']['production']
    profile['voice']['pacing_mode'] = 'script_length'
    before = deepcopy(profile)
    config, evidence = order_voice.planned_voice_config(profile, [{'text': 'word ' * 91}])
    assert config['speed'] == .815
    assert 35 < 28.42 * 1.12 / config['speed'] < 50
    assert evidence['duration_verified_after_synthesis'] is True
    assert profile == before
    assert order_voice.planned_voice_config(profile, [{'text': 'word ' * 125}])[0]['speed'] == 1.12
    assert order_voice.planned_voice_config(profile, [{'text': 'word ' * 40}])[0]['speed'] == .7
    profile['voice'].pop('pacing_mode')
    assert order_voice.planned_voice_config(profile, [{'text': 'word ' * 91}])[0]['speed'] == 1.12


def test_annotation_not_in_narration_does_not_trigger_paid_voice_regeneration():
    text = "Review your budget monthly and adjust your savings transfer."
    result = order_voice.speech_match(text, text, ["review budget", "celebrate small wins"])
    assert result["status"] == "passed"
    assert result["unused_pronunciation_hints"] == ["review budget", "celebrate small wins"]


def test_only_failed_voice_segment_is_regenerated_and_cached_after_restart(store, script, monkeypatch):
    job = create(store)["id"]
    counters = Counter()
    monkeypatch.setattr(order_voice, "WhisperCppSubtitleService", lambda *_: SimpleNamespace(runtime_availability=lambda: SimpleNamespace(available=True)))
    monkeypatch.setattr(order_voice, "resolve_ffmpeg", lambda: "test-ffmpeg")
    monkeypatch.setattr(order_voice, "voice_settings", lambda settings, config: settings.model_copy(update={"elevenlabs_api_key": "test-key-not-real"}))

    async def synth(settings, context, segment, before, after):
        counters[segment["id"]] += 1
        output = context.artifact_dir / "audio" / segment["id"] / "narration.wav"
        output.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output), "wb") as wav:
            wav.setnchannels(2)
            wav.setsampwidth(2)
            wav.setframerate(44100)
            wav.writeframes(bytes([counters[segment['id']], 0, 0, 0]) * 44100 * 6)
        texts = segment["text"].split()
        words = [{"segment_id": segment["id"], "text": word, "start_sec": i * 6 / len(texts),
                  "end_sec": (i + 1) * 6 / len(texts)} for i, word in enumerate(texts)]
        part = {"output_file": str(output), "sha256": file_hash(output), "duration_seconds": 6,
                "word_timestamps": words, "timestamps": [{"segment_id": segment["id"], "text": segment["text"],
                    "start_sec": 0, "end_sec": 6, "speech_end_sec": 6, "duration_sec": 6}]}
        result = write_json(output.with_suffix(".json"), part)
        return StepResult(result.artifact_name, result.artifact_path, part, result.message, (output,))

    def transcribe(settings, audio, output):
        identity = audio.parent.name
        text = next(s.text for s in script.segments if s.id == identity)
        if identity == "s03" and counters[identity] == 1:
            text = "The wrong narration includes a completely different instruction."
        output.write_text(text, encoding="utf-8")
        return text

    async def normalize(raw, output):
        output.write_bytes(raw.read_bytes())
        return {"status": "normalized"}

    monkeypatch.setattr(order_voice, "synthesize", synth)
    monkeypatch.setattr(order_voice, "transcribe", transcribe)
    monkeypatch.setattr(order_voice.voice_synthesis, "_normalize_audio", normalize)
    async def run(token):
        ex = StageExecutor(store, job, token)
        ex.context.artifacts["script"] = script.model_dump(mode="json")
        # Media now requires the real claim gate's input, not only an in-memory
        # fixture. Persist the reviewed test script through the normal store.
        payload = script.model_dump(mode="json")
        if store.begin(job, token, "script", {"fixture": "voice-recovery"}) is None:
            result = write_json(ex.context.artifact_dir / "script.json", payload)
            store.complete(job, token, "script", payload, [result.artifact_path])
        return await order_voice.produce_voice(ex)
    with store.lease(job) as token:
        first = asyncio.run(run(token))
    assert first["duration_seconds"] == 36
    assert first["qa"]["status"] == "passed"
    assert counters == Counter({"s01": 1, "s02": 1, "s03": 2, "s04": 1, "s05": 1, "s06": 1})
    with store.lease(job) as token:
        second = asyncio.run(run(token))
    assert first == second
    assert sum(counters.values()) == 7
    assert store.get(job)["provider_calls"] == 7
    assert store.get(job)["status"] != "ready"
