from __future__ import annotations

import asyncio
import json
import wave

import pytest

from atnos.production import voice_revision
from atnos.production.editorial_film import frame_ranges
from atnos.production.order_store import file_hash
from atnos.production.steps.common import write_json
from atnos.production.steps import order_voice
from test_production_order_store import store, create
from test_production_order_script import script


def test_pacing_preserves_recorded_samples_and_shifts_word_timings(tmp_path):
    from types import SimpleNamespace
    directory = tmp_path / "audio"
    directory.mkdir()
    source = directory / "part.wav"
    frames = bytes([7,0,7,0]) * 44100
    with wave.open(str(source),"wb") as stream:
        stream.setnchannels(2); stream.setsampwidth(2); stream.setframerate(44100)
        stream.writeframes(frames)
    def part(identity):
        return {"output_file":str(source),"timestamps":[{"segment_id":identity,"start_sec":0,"end_sec":1,"speech_end_sec":.9,"duration_sec":1}],
                "word_timestamps":[{"segment_id":identity,"text":"Hello","start_sec":.1,"end_sec":.9}]}
    context = SimpleNamespace(artifact_dir=tmp_path)
    target,timings,words,duration = order_voice.assemble_audio(context,[part("s1"),part("s2")],pause_seconds=.35)
    assert duration == 2.35 and timings[0]["end_sec"] == timings[1]["start_sec"] == 1.35
    assert words[1]["start_sec"] == pytest.approx(1.45)
    assert timings[0]["speech_end_sec"] == .9
    assert frame_ranges(timings)[-1][1] == round(2.35*30)
    with wave.open(str(target),"rb") as stream:
        assert stream.readframes(44100) == frames
        assert stream.readframes(15435) == b"\0" * 15435 * 4
        assert stream.readframes(44100) == frames
    with pytest.raises(ValueError):
        order_voice.assemble_audio(context,[],pause_seconds=1)


def test_pacing_revalidates_without_new_speech_or_attempt_reset(store, script, monkeypatch):
    job = create(store)["id"]
    context = store.context(job)
    with store.lease(job) as token:
        def record(stage,payload,files=()):
            store.begin(job,token,stage,{"fixture":stage})
            path = context.artifact_dir / f"{stage}.json"
            result = write_json(path,payload)
            store.complete(job,token,stage,payload,[result.artifact_path,*files])
        record("script",script.model_dump(mode="json"))
        for segment in script.segments:
            audio = context.artifact_dir / "audio" / f"{segment.id}.wav"
            audio.parent.mkdir(exist_ok=True)
            with wave.open(str(audio),"wb") as stream:
                stream.setnchannels(2); stream.setsampwidth(2); stream.setframerate(44100)
                stream.writeframes(bytes([3,0,3,0])*round(44100*5.7))
            record(f"voice/{segment.id}",{"output_file":str(audio),"sha256":file_hash(audio),"duration_seconds":5.7,
                "timestamps":[{"segment_id":segment.id,"start_sec":0,"end_sec":5.7,"speech_end_sec":5.5,"duration_sec":5.7}],
                "word_timestamps":[]},[audio])
            record(f"voice_qa/{segment.id}",{"status":"passed","method":"controlled test fixture, not real ASR"})
        for _ in range(3):
            store.begin(job,token,"voice",{"fixture":"unchanged"})
            store.fail(job,token,"voice",category="invalid_output",message="Narration timing outside profile")
    async def normalize(source,target):
        target.write_bytes(source.read_bytes())
        return {"status":"normalized"}
    monkeypatch.setattr(order_voice.voice_synthesis,"_normalize_audio",normalize)
    with pytest.raises(ValueError,match="400"):
        asyncio.run(voice_revision.revalidate_pacing(store.settings,job,.8))
    result = asyncio.run(voice_revision.revalidate_pacing(store.settings,job,.35))
    assert result["duration_seconds"] == pytest.approx(35.95)
    state = store.get(job)
    assert state["provider_calls"] == 0 and state["reserved_cost_units"] == 0
    voice = next(row for row in state["checkpoints"] if row["stage"] == "voice")
    assert voice["attempts"] == 3 and voice["output"]["pacing"]["time_stretch"] is False
    archive = json.loads((context.artifact_dir / "editorial/voice-before-pacing.json").read_text())
    assert archive["status"] == "failed" and archive["attempts"] == 3
    assert state["status"] != "ready"
