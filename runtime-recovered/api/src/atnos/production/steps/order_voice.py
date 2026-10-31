from __future__ import annotations

import asyncio
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from uuid import uuid4
import wave

from atnos.production.media_tools import resolve_ffmpeg
from atnos.production.models import StepResult
from atnos.production.narration_alignment import align_narration
from atnos.production.order_store import file_hash, ProductionBlocked
from atnos.production.project_profile import content_hash
from atnos.production.spoken_numbers import numeric_mentions, NUMBER_WORDS
from atnos.production.steps import voice_synthesis
from atnos.production.steps.common import write_json
from atnos.services.whisper_cpp_subtitle_service import WhisperCppSubtitleService


def speech_match(expected: str, recognized: str, terms: list[str]) -> dict:
    clean = lambda text: re.sub(r"[^a-z0-9 ]", " ", text.casefold().replace("\u2019", "'").replace("'", ""))
    def lexical(text):
        return [w for w in clean(text).split() if w not in NUMBER_WORDS | {"and", "dollar", "dollars", "percent", "point"} and not w.isdigit()]
    wanted, heard = numeric_mentions(expected), numeric_mentions(recognized)
    numbers_match = [m["value"] for m in wanted] == [m["value"] for m in heard]
    left, right = lexical(expected), lexical(recognized)
    ratio = SequenceMatcher(None, left, right, autojunk=False).ratio() if left and right else 0
    expected_text = " ".join(clean(expected).split())
    unused_terms = [term for term in terms if " ".join(clean(term).split()) not in expected_text]
    missing_terms = [term for term in terms if term not in unused_terms and not numeric_mentions(term) and
                     " ".join(clean(term).split()) not in " ".join(clean(recognized).split())]
    return {"status": "passed" if numbers_match and ratio >= .88 and not missing_terms else "failed",
            "sequence_ratio": ratio, "numbers_match": numbers_match,
            "expected_numbers": wanted, "recognized_numbers": heard, "missing_terms": missing_terms,
            "unused_pronunciation_hints": unused_terms,
            "recognized_text": recognized, "engine": "whisper_cpp", "human_listening": "not_performed"}


def voice_settings(settings, config):
    if config.get('provider') == 'qwen_local':
        return settings.model_copy(update={'shorts_allow_cloud_tts': False, 'elevenlabs_api_key': None, 'whisper_language': 'en'})
    return settings.model_copy(update={
        "shorts_tts_backend": "elevenlabs",
        "elevenlabs_voice_id": config["voice_id"], "elevenlabs_model_id": config["model"],
        "elevenlabs_stability": config["stability"], "elevenlabs_similarity_boost": config["similarity"],
        "elevenlabs_style": config["style"], "elevenlabs_speed": config["speed"],
        "elevenlabs_use_speaker_boost": config["speaker_boost"], "whisper_language": "en",
    })


def planned_voice_config(profile, segments):
    """Set natural provider pacing before synthesis; recorded duration remains a gate."""
    config = dict(profile['voice'])
    count = sum(len(segment['text'].split()) for segment in segments)
    reference = profile['word_budget'][1]
    if config.get('pacing_mode', 'fixed') == 'script_length':
        # The profile's maximum speed is calibrated for its longest allowed script.
        # Short scripts get slower delivery, within the provider's supported range.
        config['speed'] = round(max(.7, config['speed'] * min(1, count / reference)), 3)
    return config, {'mode': config.get('pacing_mode', 'fixed'), 'word_count': count,
                    'reference_word_count': reference, 'configured_speed': profile['voice']['speed'],
                    'synthesis_speed': config['speed'], 'duration_verified_after_synthesis': True}


def order_voice_profile(context):
    """Use the order's immutable voice choice, independent of legacy channel aliases."""
    profile = context.artifacts["project_profile"]
    return {"channel_profile_key": profile["project"], "language_code": profile["production"]["language"],
            "elevenlabs_voice_id": profile["production"]["voice"]["voice_id"]}


def decode_pcm(ffmpeg, source: Path, output: Path, *, rate=44100, channels=2):
    subprocess.run([ffmpeg, "-y", "-v", "error", "-i", str(source), "-ar", str(rate), "-ac", str(channels),
                    "-c:a", "pcm_s16le", str(output)], check=True, capture_output=True, timeout=60)


def transcribe(settings, audio: Path, output: Path, *, vocabulary=()) -> str:
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("FFmpeg required for voice validation")
    asr_input = output.with_suffix(".wav")
    decode_pcm(ffmpeg, audio, asr_input, rate=16000, channels=1)
    service = WhisperCppSubtitleService(settings)
    command = service.build_command(audio_path=asr_input, output_path=output)
    if vocabulary:
        if len(vocabulary) > 10 or any(not re.fullmatch(r"[A-Za-z]+(?: [A-Za-z]+){0,2}", term) for term in vocabulary):
            raise ValueError("ASR hints may contain short vocabulary terms, never numbers or full narration")
        command += ["--prompt", "Vocabulary: " + ", ".join(vocabulary)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
    if result.returncode or not output.is_file():
        raise RuntimeError("Whisper did not produce a usable transcription")
    return voice_synthesis._srt_caption_text(output.read_text(encoding="utf-8"))


async def synthesize(settings, context, segment, previous, following):
    if not settings.shorts_allow_cloud_tts:
        raise ProductionBlocked("Cloud TTS is disabled; select an authorized local voice before synthesis")
    directory = context.artifact_dir / "audio" / segment["id"]
    directory.mkdir(parents=True, exist_ok=True)
    raw, pcm = directory / "provider.mp3", directory / "narration.wav"
    provider = await voice_synthesis._try_elevenlabs_tts(settings, text=segment["text"], output_path=raw,
        voice_profile=order_voice_profile(context), with_timestamps=True,
        previous_text=previous, next_text=following)
    if provider.get("ok") is not True:
        raise RuntimeError(f"ElevenLabs did not generate this segment: {provider.get('error', 'empty output')}")
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("FFmpeg unavailable")
    await asyncio.to_thread(decode_pcm, ffmpeg, raw, pcm)
    duration = voice_synthesis._wav_duration(pcm)
    timestamps, words = align_narration([segment], provider.get("alignment") or {}, duration=duration)
    payload = {"output_file": str(pcm), "sha256": file_hash(pcm), "duration_seconds": duration,
               "timestamps": timestamps, "word_timestamps": words, "provider": provider,
               "text": segment["text"], "timing_source": "provider_character_alignment"}
    result = write_json(directory / "voice.json", payload)
    return StepResult(result.artifact_name, result.artifact_path, payload, result.message, (pcm, raw))


async def synthesize_narration(settings, context, segments):
    """Let v3 perform complete thoughts together, retaining its natural pauses."""
    if not settings.shorts_allow_cloud_tts:
        raise ProductionBlocked("Cloud TTS is disabled; select an authorized local voice before synthesis")
    directory = context.artifact_dir / "audio" / "narration"
    directory.mkdir(parents=True, exist_ok=True)
    text = "\n\n".join(segment["text"].strip() for segment in segments)
    raw, pcm = directory / "provider.mp3", directory / "narration.wav"
    provider = await voice_synthesis._try_elevenlabs_tts(
        settings, text=text, output_path=raw, voice_profile=order_voice_profile(context), with_timestamps=True)
    if provider.get("ok") is not True:
        raise RuntimeError(f"ElevenLabs did not generate the complete narration: {provider.get('error', 'empty output')}")
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("FFmpeg unavailable")
    await asyncio.to_thread(decode_pcm, ffmpeg, raw, pcm)
    duration = voice_synthesis._wav_duration(pcm)
    timestamps, words = align_narration(segments, provider.get("alignment") or {}, duration=duration)
    payload = {"output_file": str(pcm), "sha256": file_hash(pcm), "duration_seconds": duration,
               "provider_output_file": str(raw), "provider_sha256": file_hash(raw),
               "timestamps": timestamps, "word_timestamps": words, "provider": provider,
               "text": text, "timing_source": "provider_character_alignment", "single_pass": True}
    result = write_json(directory / "voice.json", payload)
    return StepResult(result.artifact_name, result.artifact_path, payload, result.message, (pcm, raw))


async def produce_single_pass_voice(executor, settings, profile, segments, config, pacing):
    context = executor.context
    text = "\n\n".join(segment["text"].strip() for segment in segments)
    inputs = {"segments": [{"id": segment["id"], "text": segment["text"]} for segment in segments],
              "text": text, "voice": config, "pacing": pacing, "contract": 1}
    while True:
        part = await executor.run("voice/narration", inputs,
            lambda: synthesize_narration(settings, context, segments), provider_call=True,
            estimated_cost_usd=len(text) / 1000 * config["reservation_usd_per_1000_characters"])

        async def inspect():
            output = context.artifact_dir / "audio" / "narration" / "recognized.srt"
            recognized = await asyncio.to_thread(transcribe, settings, Path(part["output_file"]), output)
            terms = list(dict.fromkeys(term for segment in segments for term in segment.get("pronunciation_terms", [])))
            verdict = speech_match(text, recognized, terms)
            verdict.update(scope="full_narration", segment_ids=[segment["id"] for segment in segments])
            result = write_json(output.with_suffix(".json"), verdict)
            return StepResult(result.artifact_name, result.artifact_path, verdict, result.message, (output,))

        verdict = await executor.run("voice_qa/narration",
            {"sha256": part["sha256"], "provider_sha256": part["provider_sha256"],
             "segments": segments, "validator": 2, "single_pass": True}, inspect)
        if verdict["status"] == "passed":
            break
        await asyncio.to_thread(executor.store.fail, context.job_id, executor.token, "voice/narration",
                                category="pronunciation", message=json.dumps(verdict))

    async def finalize():
        return await finalize_voice(context, [part], [verdict], synthesis_pacing=pacing, single_pass=True)

    voice = await executor.run("voice",
        {"recording": {"sha256": part["sha256"], "provider_sha256": part["provider_sha256"],
                       "timestamps": part["timestamps"], "words": part["word_timestamps"]},
         "profile": profile, "checks": [verdict], "script": segments, "assembly_contract": 3,
         "single_pass": True}, finalize)
    await asyncio.to_thread(executor.store.persist_legacy_artifact, context.job_id, executor.token, "voice", voice)
    await asyncio.to_thread(executor.store.reconcile_single_pass_voice_checkpoints, context.job_id, executor.token)
    context.artifacts["voice"] = voice
    return voice


def assemble_audio(context, parts, *, pause_seconds=0.0):
    if not 0 <= pause_seconds <= .4:
        raise ValueError("Editorial pauses must remain between zero and 400 milliseconds")
    target = context.artifact_dir / "audio" / "narration_assembled.wav"
    offset, timings, words = 0.0, [], []
    with wave.open(str(target), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(44100)
        for index, part in enumerate(parts):
            with wave.open(part["output_file"], "rb") as audio:
                if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (2, 2, 44100):
                    raise ValueError("Inconsistent segment audio format")
                output.writeframes(audio.readframes(audio.getnframes()))
                duration = audio.getnframes() / audio.getframerate()
            timing = dict(part["timestamps"][0])
            for key in ("start_sec", "end_sec", "speech_end_sec"):
                timing[key] += offset
            pause_frames = round(pause_seconds * 44100) if index < len(parts) - 1 else 0
            pause = pause_frames / 44100
            if pause_frames:
                output.writeframes(b"\0" * pause_frames * 4)
                timing["end_sec"] += pause
                timing["duration_sec"] += pause
            timing["single_pass"] = False
            timings.append(timing)
            words.extend({**word, "start_sec": word["start_sec"] + offset, "end_sec": word["end_sec"] + offset}
                         for word in part["word_timestamps"])
            offset += duration + pause
    return target, timings, words, offset


async def produce_voice(executor):
    """Route measured editorial failures to bounded script repair, never assembly retries."""
    from atnos.production.narration_revision import NarrationRevisionRequired
    from atnos.production.project_profile import content_hash
    from atnos.production.steps import order_script
    from atnos.production.steps.order_assets import produce_assets

    context = executor.context
    base_hash = content_hash(context.artifacts['script'])
    revision_path = context.artifact_dir / 'script' / 'after_measured_voice.json'
    if revision_path.is_file() and context.artifacts.get('_renderer_refresh', {}).get('local_voice_revision') is None:
        saved = json.loads(revision_path.read_text(encoding='utf-8'))
        if saved.get('base_hash') == base_hash:
            context.artifacts['script'] = saved['script']
            await produce_assets(executor)
    for revision_index in range(3):
        try:
            return await _produce_voice_once(executor)
        except NarrationRevisionRequired as exc:
            if executor.context.artifacts.get('_renderer_refresh', {}).get('local_voice_revision') is not None:
                raise ProductionBlocked('Local voice correction requires editorial timing review; it cannot rewrite the accepted script') from exc
            if revision_index == 2:
                raise
            current = order_script.EditorialScript.model_validate(context.artifacts['script'])
            issues = [{'code': 'practical_share', 'segment_ids': [s.id for s in current.segments if s.type not in {'hook','cta'}],
                       'detail': json.dumps(exc.report)}]
            if not exc.report['required_duration'][0] <= exc.report['measured_duration'] <= exc.report['required_duration'][1]:
                issues.append({'code':'word_budget','segment_ids':[s.id for s in current.segments],
                    'detail':'Rewrite complete thoughts to fit measured speech duration. Do not stretch audio. ' + json.dumps(exc.report)})

            async def repair():
                fixed = await order_script.repair(executor.store.settings, context, current, issues)
                if " ".join(segment.text for segment in current.segments) == " ".join(segment.text for segment in fixed.segments):
                    raise ProductionBlocked('Measured voice repair must change spoken content; relabelling unchanged text is insufficient')
                return write_json(context.artifact_dir / 'script' / f'measured_repair_{revision_index+1}.json', fixed.model_dump(mode='json'))

            fixed = await executor.run(f'measured_script_repair/{revision_index+1}',
                {'script': current.model_dump(mode='json'), 'measurement': exc.report, 'contract': 1}, repair, local_model_call=True)
            async def review():
                verdict = await order_script.review(executor.store.settings, context, order_script.EditorialScript.model_validate(fixed))
                return write_json(context.artifact_dir / 'script' / f'measured_review_{revision_index+1}.json', verdict)
            verdict = await executor.run(f'measured_script_review/{revision_index+1}', {'script':fixed,'contract':1},review,local_model_call=True)
            if verdict['status'] != 'passed':
                raise ProductionBlocked('Measured voice script repair failed editorial review: ' + json.dumps(verdict))
            context.artifacts['script'] = fixed
            write_json(revision_path, {'base_hash': base_hash, 'script': fixed})
            await asyncio.to_thread(executor.store.persist_legacy_artifact, context.job_id, executor.token, 'script', fixed)
            await produce_assets(executor)


async def _produce_voice_once(executor):
    context = executor.context
    profile = context.artifacts["project_profile"]["production"]
    segments = context.artifacts["script"]["segments"]
    from atnos.production.local_voice_lane import load_local_voice_lane
    lane = await asyncio.to_thread(load_local_voice_lane, executor.store.settings, context.job_id)
    if lane is not None:
        return await produce_local_single_pass_voice(executor, lane.as_dict(), profile, segments)
    config, pacing = planned_voice_config(profile, segments)
    settings = voice_settings(executor.store.settings, config)
    runtime = WhisperCppSubtitleService(settings).runtime_availability()
    if not runtime.available or not settings.elevenlabs_api_key or not resolve_ffmpeg():
        raise ProductionBlocked("Voice requires configured ElevenLabs, FFmpeg and an independent local Whisper validator")
    if str(config["model"]).strip().casefold() == "eleven_v3":
        return await produce_single_pass_voice(executor, settings, profile, segments, config, pacing)
    parts, checks = [], []
    for index, segment in enumerate(segments):
        before = segments[index - 1]["text"] if index else ""
        after = segments[index + 1]["text"] if index + 1 < len(segments) else ""
        stage = f"voice/{segment['id']}"
        inputs = {"segment": segment, "voice": config, "previous": before, "following": after, "contract": 1}
        if pacing['mode'] == 'script_length':
            inputs.update(contract=2, pacing=pacing)
        while True:
            part = await executor.run(stage, inputs, lambda: synthesize(settings, context, segment, before, after),
                provider_call=True, estimated_cost_usd=(len(segment["text"]) + len(before) + len(after)) / 1000 * config["reservation_usd_per_1000_characters"])

            async def inspect():
                output = context.artifact_dir / "audio" / segment["id"] / "recognized.srt"
                recognized = await asyncio.to_thread(transcribe, settings, Path(part["output_file"]), output)
                verdict = speech_match(segment["text"], recognized, segment.get("pronunciation_terms", []))
                result = write_json(output.with_suffix(".json"), verdict)
                return StepResult(result.artifact_name, result.artifact_path, verdict, result.message, (output,))

            verdict = await executor.run(f"voice_qa/{segment['id']}", {"sha256": part["sha256"], "segment": segment, "validator": 2}, inspect)
            if verdict["status"] == "passed":
                break
            await asyncio.to_thread(executor.store.fail, context.job_id, executor.token, stage,
                                    category="pronunciation", message=json.dumps(verdict))
        parts.append(part)
        checks.append(verdict)

    async def merge():
        return await finalize_voice(context, parts, checks, synthesis_pacing=pacing)

    voice = await executor.run("voice", {"parts": [{"sha256": p["sha256"], "words": p["word_timestamps"]} for p in parts],
                                          "profile": profile, "checks": checks, "script": segments, "assembly_contract": 2}, merge)
    await asyncio.to_thread(executor.store.persist_legacy_artifact, context.job_id, executor.token, "voice", voice)
    context.artifacts["voice"] = voice
    return voice


async def produce_local_single_pass_voice(executor, lane, profile, segments):
    """Job-scoped Ryan trial: one full-script recording, independent ASR and real CTC timing."""
    from atnos.services.qwen_tts_local_runtime_service import QwenTTSLocalRuntimeService
    context, settings = executor.context, executor.store.settings.model_copy(update={"whisper_language": "en"})
    text = "\n\n".join(segment["text"].strip() for segment in segments)
    script_hash = content_hash({"segments": segments})
    lane_input = {"lane": lane, "script_hash": script_hash, "text": text, "synthesis_contract": 1}
    runtime = WhisperCppSubtitleService(settings).runtime_availability()
    from atnos.services.qwen_tts_local_runtime_service import runtime_availability as qwen_runtime_availability
    qwen_runtime = qwen_runtime_availability()
    if not runtime.available or not resolve_ffmpeg() or not qwen_runtime.get("available"):
        raise ProductionBlocked("Local Ryan voice requires pinned Qwen, independent local Whisper and FFmpeg runtimes")

    async def synthesize_local():
        directory = context.artifact_dir / "audio" / "narration"
        directory.mkdir(parents=True, exist_ok=True)
        raw = directory / f"qwen_ryan_source_{script_hash[:12]}_{uuid4().hex[:8]}.wav"
        provider = await QwenTTSLocalRuntimeService().synthesize_production(
            text=text, output_path=raw, lane=lane, settings=executor.store.settings)
        duration = voice_synthesis._wav_duration(raw)
        source_sha = file_hash(raw)
        payload = {"output_file": str(raw), "sha256": source_sha, "duration_seconds": duration,
                   "provider_output_file": str(raw), "provider_sha256": source_sha,
                   "timestamps": [], "word_timestamps": [], "provider": provider, "text": text,
                   "timing_source": "local_ctc_reference_alignment", "single_pass": True,
                   "lane_digest": lane["digest"], "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                   "local_inference_calls": 1, "local_inference_cost_usd": 0}
        result = write_json(raw.with_suffix(".json"), payload)
        return StepResult(result.artifact_name, result.artifact_path, payload, result.message, (raw,))

    part = await executor.run("voice/local_ryan/narration", lane_input, synthesize_local,
                              local_model_call=True, estimated_cost_usd=0)

    async def inspect():
        output = context.artifact_dir / "audio" / "narration" / f"local_ryan_recognized_{script_hash[:12]}.srt"
        recognized = await asyncio.to_thread(transcribe, settings, Path(part["output_file"]), output)
        terms = list(dict.fromkeys(term for segment in segments for term in segment.get("pronunciation_terms", [])))
        verdict = speech_match(text, recognized, terms)
        verdict.update(scope="full_narration", segment_ids=[segment["id"] for segment in segments],
                       provider="whisper_cpp", expected_prompt_provided=False)
        result = write_json(output.with_suffix(".json"), verdict)
        return StepResult(result.artifact_name, result.artifact_path, verdict, result.message, (output,))

    verdict = await executor.run("voice/local_ryan_qa/narration",
        {"sha256": part["sha256"], "script_hash": script_hash, "validator": 3,
         "scope": "job_scoped_local_voice"}, inspect)
    if verdict.get("status") != "passed":
        if context.artifacts.get('_renderer_refresh', {}).get('local_voice_revision') is not None:
            raise ProductionBlocked('Local voice correction narration failed speech QA; no automatic segment regeneration')
        return await recover_local_ryan_segments(executor,lane,profile,segments,part,verdict)

    from atnos.services.local_ctc_alignment_service import align as ctc_align

    async def align_recording():
        output = context.artifact_dir / "audio" / "narration" / f"local_ctc_alignment_{script_hash[:12]}.json"
        alignment = await ctc_align(part["output_file"], text, output)
        timestamps, words, duration = local_ctc_timestamps(segments, alignment, part["duration_seconds"])
        payload = {"timestamps": timestamps, "word_timestamps": words, "duration_seconds": duration,
                   "timing_source": "local_ctc_reference_alignment", "audio_sha256": part["sha256"],
                   "script_hash": script_hash, "alignment": alignment}
        result = write_json(output, payload)
        return StepResult(result.artifact_name, result.artifact_path, payload, result.message)

    alignment = await executor.run("voice/local_ryan/alignment",
        {"audio_sha256": part["sha256"], "script_hash": script_hash,
         "aligner": "wav2vec2-asr-base-960h", "contract": 1}, align_recording)
    complete_part = {**part, "timestamps": alignment["timestamps"],
                     "word_timestamps": alignment["word_timestamps"],
                     "duration_seconds": alignment["duration_seconds"]}

    async def finalize():
        return await finalize_voice(context, [complete_part], [verdict], single_pass=True,
                                    timing_source="local_ctc_reference_alignment")

    voice = await executor.run("voice",
        {"recording": {"sha256": part["sha256"], "lane_digest": lane["digest"],
                       "timestamps": alignment["timestamps"], "words": alignment["word_timestamps"]},
         "profile": profile, "checks": [verdict], "script_hash": script_hash,
         "assembly_contract": 4, "single_pass": True, "timing_source": "local_ctc_reference_alignment"}, finalize)
    await asyncio.to_thread(executor.store.persist_legacy_artifact, context.job_id, executor.token, "voice", voice)
    context.artifacts["voice"] = voice
    return voice


async def recover_local_ryan_segments(executor,lane,profile,segments,rejected,failed_review):
    """Bounded recovery of a proven failed long take through existing voice stages.

    Keep the rejected take and ASR. Each short take must independently pass the
    same speech gate before reference alignment or ordinary audio assembly.
    """
    if failed_review.get('status')!='failed' or not rejected.get('sha256'):
        raise ProductionBlocked('Local voice recovery requires the failed recording and independent review')
    from atnos.services.qwen_tts_local_runtime_service import QwenTTSLocalRuntimeService
    from atnos.services.local_ctc_alignment_service import align as ctc_align
    context=executor.context
    settings=executor.store.settings.model_copy(update={'whisper_language':'en'})
    parts,checks=[],[]
    for segment in segments:
        directory=context.artifact_dir/'audio'/'narration'/'recovery'/segment['id']
        directory.mkdir(parents=True,exist_ok=True)
        for take in range(2):
            prefix=f"voice/local_ryan_recovery/{segment['id']}/{take}"
            async def synthesize_segment():
                raw=directory/f'provider_{uuid4().hex[:12]}.wav'
                provider=await QwenTTSLocalRuntimeService().synthesize_production(
                    text=segment['text'],output_path=raw,lane=lane,settings=executor.store.settings)
                pcm=raw.with_name(raw.stem+'_pcm.wav')
                await asyncio.to_thread(decode_pcm,resolve_ffmpeg(),raw,pcm)
                payload={'output_file':str(pcm),'sha256':file_hash(pcm),
                    'provider_output_file':str(raw),'provider_sha256':file_hash(raw),
                    'duration_seconds':voice_synthesis._wav_duration(pcm),'provider':provider,
                    'segment_id':segment['id'],'text':segment['text'],'lane_digest':lane['digest'],
                    'local_inference_calls':1,'local_inference_cost_usd':0}
                result=write_json(pcm.with_suffix('.json'),payload)
                return StepResult(result.artifact_name,result.artifact_path,payload,result.message,(raw,pcm))
            part=await executor.run(prefix,{'segment':segment,'lane_digest':lane['digest'],
                'rejected_recording_sha256':rejected['sha256'],'contract':1},synthesize_segment,
                local_model_call=True,estimated_cost_usd=0)
            async def inspect_segment():
                output=directory/f"recognized_{take}_{part['sha256'][:12]}.srt"
                heard=await asyncio.to_thread(transcribe,settings,Path(part['output_file']),output)
                verdict=speech_match(segment['text'],heard,segment.get('pronunciation_terms',[]))
                verdict.update(scope='recovery_segment',segment_id=segment['id'],expected_prompt_provided=False)
                result=write_json(output.with_suffix('.json'),verdict)
                return StepResult(result.artifact_name,result.artifact_path,verdict,result.message,(output,))
            verdict=await executor.run(prefix+'/qa',{'audio_sha256':part['sha256'],'segment':segment,
                'validator':3},inspect_segment)
            if verdict['status']=='passed':
                break
        else:
            raise ProductionBlocked(f"Local Ryan segment {segment['id']} failed both bounded speech QA attempts")
        async def align_segment():
            output=directory/f"alignment_{part['sha256'][:12]}.json"
            alignment=await ctc_align(part['output_file'],segment['text'],output)
            timings,words,duration=local_ctc_timestamps([segment],alignment,part['duration_seconds'])
            return write_json(output,{'timestamps':timings,'word_timestamps':words,'duration_seconds':duration,
                'alignment':alignment,'audio_sha256':part['sha256']})
        aligned=await executor.run(prefix+'/alignment',{'audio_sha256':part['sha256'],
            'segment':segment,'aligner':'wav2vec2-asr-base-960h','contract':1},align_segment)
        parts.append({**part,**{key:aligned[key] for key in ('timestamps','word_timestamps','duration_seconds')}})
        checks.append(verdict)
    async def finalize():
        return await finalize_voice(context,parts,checks,pause_seconds=.12,
            synthesis_pacing={'mode':'bounded_local_segment_recovery','rejected_source_sha256':rejected['sha256'],
                              'max_takes_per_segment':2,'lane_digest':lane['digest']},
            timing_source='local_ctc_reference_alignment')
    voice=await executor.run('voice',{'parts':[{'sha256':p['sha256'],'words':p['word_timestamps']} for p in parts],
        'script':segments,'profile':profile,'checks':checks,'lane_digest':lane['digest'],
        'assembly_contract':5,'recovery_from':rejected['sha256']},finalize)
    await asyncio.to_thread(executor.store.persist_legacy_artifact,context.job_id,executor.token,'voice',voice)
    context.artifacts['voice']=voice
    return voice


def local_ctc_timestamps(segments, alignment, expected_duration):
    """Bind reference CTC word timings to ordered script segments without rescaling."""
    words = alignment.get("word_timestamps")
    duration = alignment.get("duration_sec", alignment.get("duration_seconds"))
    if (not isinstance(words, list) or not words or not isinstance(duration, (int, float))
            or abs(float(duration) - float(expected_duration)) > .03):
        raise ValueError("Local CTC result is incomplete or describes a different audio timeline")
    expected = [word for segment in segments for word in str(segment["text"]).split()]
    clean = lambda value: re.sub(r"[^a-z0-9]", "", str(value).casefold())
    if len(words) != len(expected) or [clean(row.get("text")) for row in words] != [clean(word) for word in expected]:
        raise ValueError("Local CTC word sequence does not match the final script")
    cursor = 0.0
    bound_words = []
    for word, source in zip(expected, words, strict=True):
        start, end = source.get("start_sec"), source.get("end_sec")
        if (not isinstance(start, (int, float)) or not isinstance(end, (int, float))
                or not (cursor <= start < end <= float(duration))):
            raise ValueError("Local CTC timestamps are non-monotonic or outside the source recording")
        bound_words.append({"text": word, "start_sec": float(start), "end_sec": float(end),
                            "confidence": source.get("confidence"), "segment_id": None})
        cursor = float(end)
    timestamps, offset = [], 0
    for index, segment in enumerate(segments):
        count = len(str(segment["text"]).split())
        if count < 1:
            raise ValueError("Local CTC cannot align an empty script segment")
        end_index = offset + count
        chunk = bound_words[offset:end_index]
        for row in chunk:
            row["segment_id"] = segment["id"]
        edit_end = bound_words[end_index]["start_sec"] if end_index < len(bound_words) else float(duration)
        start = chunk[0]["start_sec"] if index else 0.0
        if edit_end <= start:
            raise ValueError("Local CTC produced an empty edit segment")
        timestamps.append({"segment_id": segment["id"], "text": segment["text"],
                           "start_sec": start, "end_sec": edit_end, "duration_sec": edit_end - start,
                           "speech_end_sec": chunk[-1]["end_sec"], "engine": "local_ctc",
                           "timing_source": "local_ctc_reference_alignment", "single_pass": True})
        offset = end_index
    return timestamps, bound_words, float(duration)


async def finalize_voice(context, parts, checks, *, pause_seconds=0.0, synthesis_pacing=None, single_pass=False,
                         timing_source="provider_character_alignment"):
    profile = context.artifacts["project_profile"]["production"]
    segments = context.artifacts["script"]["segments"]
    if not checks or any(check["status"] != "passed" for check in checks):
        raise ValueError("Every recorded segment must pass independent speech QA")
    if single_pass:
        if len(parts) != 1 or not parts[0].get("single_pass") or pause_seconds:
            raise ValueError("A single provider recording must retain its original timeline and pauses")
        part = parts[0]
        raw, timestamps, words, duration = Path(part["output_file"]), part["timestamps"], part["word_timestamps"], part["duration_seconds"]
        if [timing["segment_id"] for timing in timestamps] != [segment["id"] for segment in segments]:
            raise ValueError("Complete provider recording must cover every script segment in order")
    else:
        raw, timestamps, words, duration = await asyncio.to_thread(assemble_audio, context, parts, pause_seconds=pause_seconds)
    target = context.artifact_dir / "audio" / "narration_final.wav"
    normalization = await voice_synthesis._normalize_audio(raw, target)
    if normalization.get("status") != "normalized" or abs(voice_synthesis._wav_duration(target) - duration) > .03:
        raise ValueError("Narration normalization failed or changed the timeline")
    low, high = profile["duration_seconds"]
    practical_ids = {s["id"] for s in segments if s["type"] == "practical"}
    practical = sum(t["duration_sec"] for t in timestamps if t["segment_id"] in practical_ids) / duration
    from atnos.production.narration_revision import NarrationRevisionRequired, timing_revision_report
    revision = timing_revision_report(segments, timestamps, duration, profile)
    if revision:
        write_json(context.artifact_dir / "script" / "measured_voice_revision.json", revision)
        raise NarrationRevisionRequired(revision)
    payload = {"output_file": str(target), "sha256": file_hash(target), "timestamps": timestamps, "word_timestamps": words,
               "duration_seconds": duration, "timing_source": timing_source, "production_ready": True,
               "qa": {"status": "passed", "segments": checks, "practical_time_share": practical}, "normalization": normalization,
               "pacing": {"between_scene_pause_seconds": pause_seconds, "time_stretch": False,
                          **({'synthesis': synthesis_pacing} if synthesis_pacing else {})}}
    if single_pass:
        payload.update(single_pass=True, provider=part["provider"],
                       source_recording={"output_file": part["output_file"], "sha256": part["sha256"],
                                         "provider_output_file": part["provider_output_file"],
                                         "provider_sha256": part["provider_sha256"]})
        payload["pacing"]["natural_provider_pauses_preserved"] = True
    result = write_json(context.artifact_dir / "audio_timestamps.json", payload)
    return StepResult(result.artifact_name, result.artifact_path, payload, result.message, (target,))
