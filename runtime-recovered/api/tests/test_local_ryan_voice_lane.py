from __future__ import annotations

import asyncio
import hashlib
import wave
from pathlib import Path
from types import SimpleNamespace
from collections import Counter

import pytest

from atnos.production.steps.order_voice import local_ctc_timestamps
from atnos.services.qwen_tts_local_runtime_service import QwenTTSLocalRuntimeError, QwenTTSLocalRuntimeService
from atnos.production.steps import order_voice
from atnos.production.order_store import ProductionBlocked
from test_order_voice_single_pass import prepare
from test_production_order_script import script
from test_production_order_store import store


@pytest.mark.parametrize('always_bad_segment',[False,True])
def test_failed_local_long_take_uses_bounded_independent_segment_recovery(store,script,monkeypatch,always_bad_segment):
    job,payload,_,run=prepare(store,script,monkeypatch)
    lane={'digest':'registered-lane','public_authorized':True,'local_only':True}
    monkeypatch.setattr('atnos.production.local_voice_lane.load_local_voice_lane',lambda *_:SimpleNamespace(as_dict=lambda:lane))
    monkeypatch.setattr('atnos.services.qwen_tts_local_runtime_service.runtime_availability',lambda:{'available':True})
    counts=Counter()
    async def provider(self,*,text,output_path,lane,settings):
        counts['qwen']+=1
        duration=40 if '\n\n' in text else 6.5
        with wave.open(str(output_path),'wb') as audio:
            audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(24000)
            audio.writeframes(b'\x01\x00'*round(24000*duration))
        return {'provider':'qwen_local','cost_usd':0,'lane_digest':lane['digest']}
    def decode(ffmpeg,raw,output,**kwargs):
        with wave.open(str(raw),'rb') as audio:duration=audio.getnframes()/audio.getframerate()
        with wave.open(str(output),'wb') as audio:
            audio.setnchannels(2);audio.setsampwidth(2);audio.setframerate(44100)
            audio.writeframes(b'\x01\x00\x01\x00'*round(44100*duration))
    def transcribe(settings,recording,output):
        sid=output.parent.name
        counts['asr_'+sid]+=1
        heard=next((s['text'] for s in payload['segments'] if s['id']==sid),'Unrelated failed full narration.')
        if sid=='s03' and (always_bad_segment or counts['asr_'+sid]==1):heard='A wrong sentence.'
        output.write_text(heard,encoding='utf-8');return heard
    async def align(audio_path,reference_text,output_path):
        counts['ctc']+=1
        words=reference_text.split(); step=6.2/len(words)
        Path(output_path).write_text('{}',encoding='utf-8')
        return {'duration_sec':6.5,'word_timestamps':[{'text':w,'start_sec':i*step,'end_sec':i*step+step*.8} for i,w in enumerate(words)]}
    monkeypatch.setattr(QwenTTSLocalRuntimeService,'synthesize_production',provider)
    monkeypatch.setattr(order_voice,'decode_pcm',decode)
    monkeypatch.setattr(order_voice,'transcribe',transcribe)
    monkeypatch.setattr('atnos.services.local_ctc_alignment_service.align',align)
    if always_bad_segment:
        with store.lease(job) as token,pytest.raises(ProductionBlocked,match='s03 failed both'):
            asyncio.run(run(token))
        assert counts['qwen']==5 and counts['ctc']==2
        assert not (store.context(job).artifact_dir/'audio_timestamps.json').exists()
    else:
        with store.lease(job) as token:voice=asyncio.run(run(token))
        assert counts['qwen']==8 and counts['ctc']==6
        assert voice['pacing']['synthesis']['max_takes_per_segment']==2
        assert [t['segment_id'] for t in voice['timestamps']]==[s['id'] for s in payload['segments']]
        assert voice['duration_seconds']==pytest.approx(39.6)
        with store.lease(job) as token:cached=asyncio.run(run(token))
        assert cached==voice and counts['qwen']==8 and counts['ctc']==6
        assert store.get(job)['provider_calls']==8


def test_local_ctc_mapping_keeps_source_gaps_and_segment_boundaries():
    segments = [{"id": "s01", "text": "One quiet idea."}, {"id": "s02", "text": "Then another."}]
    aligned = {"duration_sec": 3.0, "word_timestamps": [
        {"text": "One", "start_sec": .2, "end_sec": .5, "confidence": .98},
        {"text": "quiet", "start_sec": .6, "end_sec": 1.0, "confidence": .97},
        {"text": "idea.", "start_sec": 1.1, "end_sec": 1.6, "confidence": .96},
        {"text": "Then", "start_sec": 2.0, "end_sec": 2.3, "confidence": .98},
        {"text": "another.", "start_sec": 2.4, "end_sec": 2.8, "confidence": .96},
    ]}
    timestamps, words, duration = local_ctc_timestamps(segments, aligned, 3.0)
    assert duration == 3.0
    assert timestamps[0]["start_sec"] == 0
    assert timestamps[0]["speech_end_sec"] == 1.6
    assert timestamps[0]["end_sec"] == 2.0
    assert timestamps[1]["start_sec"] == 2.0
    assert timestamps[1]["end_sec"] == 3.0
    assert [word["segment_id"] for word in words] == ["s01"] * 3 + ["s02"] * 2
    assert words[0]["confidence"] == .98


def test_local_ctc_mapping_fails_closed_on_different_words_or_timeline():
    segment = [{"id": "s01", "text": "The approved words."}]
    wrong_words = {"duration_sec": 2.0, "word_timestamps": [
        {"text": "different", "start_sec": .1, "end_sec": .4},
        {"text": "words", "start_sec": .5, "end_sec": .9},
        {"text": "here", "start_sec": 1., "end_sec": 1.3},
    ]}
    with pytest.raises(ValueError, match="word sequence"):
        local_ctc_timestamps(segment, wrong_words, 2.0)
    good_words = {"duration_sec": 2.0, "word_timestamps": [
        {"text": "The", "start_sec": .1, "end_sec": .3},
        {"text": "approved", "start_sec": .4, "end_sec": .9},
        {"text": "words.", "start_sec": 1., "end_sec": 1.4},
    ]}
    with pytest.raises(ValueError, match="different audio timeline"):
        local_ctc_timestamps(segment, good_words, 3.0)


def test_qwen_production_synthesis_rejects_unregistered_lane_before_starting_process():
    service = QwenTTSLocalRuntimeService()
    with pytest.raises(QwenTTSLocalRuntimeError, match="job-scoped Ryan approval lane"):
        asyncio.run(service.synthesize_production(text="Hello.", output_path=__import__("pathlib").Path("E:/unused.wav"), lane={}))


def test_qwen_production_synthesis_rejects_path_outside_registered_job(monkeypatch):
    from atnos.production.local_voice_lane import INSTRUCTION, MODEL_ID, MODEL_REVISION, PRESET
    from atnos.production.project_profile import content_hash
    from atnos.production.order_store import ProductionOrderStore

    lane = {"job_id": "prod-approved", "request_hash": "request", "frozen_profile_hash": "profile",
            "sample_sha256": "sample", "model_id": MODEL_ID, "model_revision": MODEL_REVISION,
            "preset": PRESET, "instruction_sha256": hashlib.sha256(INSTRUCTION.encode()).hexdigest(),
            "approval_sha256": "approval", "public_authorized": True, "local_only": True}
    lane["digest"] = content_hash(lane)
    monkeypatch.setattr("atnos.production.local_voice_lane.load_local_voice_lane",
                        lambda *_: SimpleNamespace(as_dict=lambda: lane))
    monkeypatch.setattr(ProductionOrderStore, "get", lambda *_: {"artifact_dir": "E:/Atnos/data/production/wealth_blueprint/prod-approved"})
    settings = SimpleNamespace(data_dir=Path("E:/Atnos/data"))
    with pytest.raises(QwenTTSLocalRuntimeError, match="authorized job artifact root"):
        asyncio.run(QwenTTSLocalRuntimeService().synthesize_production(
            text="Hello.", output_path=Path("E:/Atnos/data/production/wealth_blueprint/prod-other/audio.wav"),
            lane=lane, settings=settings))


def test_registered_local_lane_runs_one_local_recording_asr_ctc_and_finalize(store, script, monkeypatch):
    job, payload, _, run = prepare(store, script, monkeypatch)
    lane = {"digest": "registered-lane", "public_authorized": True, "local_only": True}
    monkeypatch.setattr("atnos.production.local_voice_lane.load_local_voice_lane", lambda *_: SimpleNamespace(as_dict=lambda: lane))
    monkeypatch.setattr(order_voice, "load_local_voice_lane", lambda *_: SimpleNamespace(as_dict=lambda: lane), raising=False)
    monkeypatch.setattr("atnos.services.qwen_tts_local_runtime_service.runtime_availability", lambda: {"available": True})
    counts = Counter()

    async def synthesize(self, *, text, output_path, lane, settings):
        counts["qwen"] += 1
        with wave.open(str(output_path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(24000)
            audio.writeframes(b"\x00\x00" * 24000 * 40)
        return {"provider": "qwen_local", "lane_digest": lane["digest"], "cost_usd": 0}

    monkeypatch.setattr(QwenTTSLocalRuntimeService, "synthesize_production", synthesize)
    script_words = [word for segment in payload["segments"] for word in segment["text"].split()]

    async def align(audio_path, reference_text, output_path):
        counts["ctc"] += 1
        assert reference_text == "\n\n".join(segment["text"].strip() for segment in payload["segments"])
        times = [{"text": word, "start_sec": i * .4, "end_sec": i * .4 + .3, "confidence": .95}
                 for i, word in enumerate(script_words)]
        Path(output_path).write_text("{}", encoding="utf-8")
        return {"word_timestamps": times, "duration_sec": 40.0, "provenance": {"model": "ctc-test"}}

    monkeypatch.setattr("atnos.services.local_ctc_alignment_service.align", align)
    monkeypatch.setattr("atnos.services.qwen_tts_local_runtime_service.runtime_availability", lambda: {"available": True})
    with store.lease(job) as token:
        voice = asyncio.run(run(token))
    assert counts == Counter(qwen=1, ctc=1)
    assert voice["timing_source"] == "local_ctc_reference_alignment"
    assert voice["single_pass"] is True
    assert voice["provider"]["provider"] == "qwen_local"
    assert voice["qa"]["status"] == "passed"
    assert Path(voice["output_file"]).name == "narration_final.wav"
    assert Path(voice["output_file"]).is_file()
    assert store.get(job)["provider_calls"] == 1
    assert store.get(job)["reserved_cost_units"] == 0
    assert all(timing["timing_source"] == "local_ctc_reference_alignment" for timing in voice["timestamps"])
    assert [timing["segment_id"] for timing in voice["timestamps"]] == [segment["id"] for segment in payload["segments"]]
    assert voice["word_timestamps"][0]["start_sec"] == pytest.approx(0)
    with store.lease(job) as token:
        cached = asyncio.run(run(token))
    assert cached == voice
    assert counts == Counter(qwen=1, ctc=1)
    assert store.get(job)["provider_calls"] == 1
    payload["segments"][0]["text"] = payload["segments"][0]["text"].upper()
    script_words[:] = [word for segment in payload["segments"] for word in segment["text"].split()]
    with store.lease(job) as token:
        revised = asyncio.run(run(token))
    assert counts == Counter(qwen=2, ctc=2)
    assert store.get(job)["provider_calls"] == 2
    assert revised["source_recording"]["output_file"] != voice["source_recording"]["output_file"]
    assert revised["timing_source"] == "local_ctc_reference_alignment"


def test_local_synthesis_resume_keeps_orphaned_wav_and_uses_fresh_attempt_path(store, script, monkeypatch):
    job, payload, _, run = prepare(store, script, monkeypatch)
    lane = {"digest": "registered-lane", "public_authorized": True, "local_only": True}
    monkeypatch.setattr("atnos.production.local_voice_lane.load_local_voice_lane",
                        lambda *_: SimpleNamespace(as_dict=lambda: lane))
    monkeypatch.setattr("atnos.services.qwen_tts_local_runtime_service.runtime_availability",
                        lambda: {"available": True})
    qwen_paths, align_count = [], Counter()

    async def synthesize(self, *, text, output_path, lane, settings):
        qwen_paths.append(output_path)
        with wave.open(str(output_path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(24000)
            audio.writeframes(b"\x01\x00" * 24000 * 40)
        return {"provider": "qwen_local", "lane_digest": lane["digest"], "cost_usd": 0}

    monkeypatch.setattr(QwenTTSLocalRuntimeService, "synthesize_production", synthesize)
    words = [word for segment in payload["segments"] for word in segment["text"].split()]

    async def align(audio_path, reference_text, output_path):
        align_count["ctc"] += 1
        aligned = [{"text": word, "start_sec": i * .4, "end_sec": i * .4 + .3, "confidence": .95}
                   for i, word in enumerate(words)]
        Path(output_path).write_text("{}", encoding="utf-8")
        return {"word_timestamps": aligned, "duration_sec": 40.0, "provenance": {"model": "ctc-test"}}

    monkeypatch.setattr("atnos.services.local_ctc_alignment_service.align", align)
    original_complete = store.complete
    fail_once = {"active": True}

    def interrupt_after_wav(job_id, token, stage, output, files):
        if stage == "voice/local_ryan/narration" and fail_once["active"]:
            fail_once["active"] = False
            return_path = next(Path(path) for path in files if str(path).lower().endswith(".wav"))
            assert return_path.is_file()
            raise RuntimeError("simulated interruption after Qwen wrote WAV, before checkpoint commit")
        return original_complete(job_id, token, stage, output, files)

    monkeypatch.setattr(store, "complete", interrupt_after_wav)
    with store.lease(job) as token, pytest.raises(ProductionBlocked, match="voice/local_ryan/narration"):
        asyncio.run(run(token))
    abandoned_path = qwen_paths[0]
    abandoned_bytes = abandoned_path.read_bytes()
    assert store.get(job)["provider_calls"] == 1
    with store.lease(job) as token:
        recovered = asyncio.run(run(token))
    assert len(qwen_paths) == 2
    assert qwen_paths[1] != abandoned_path
    assert abandoned_path.read_bytes() == abandoned_bytes
    assert qwen_paths[1].is_file()
    assert align_count["ctc"] == 1
    assert recovered["timing_source"] == "local_ctc_reference_alignment"
    assert store.get(job)["provider_calls"] == 2
