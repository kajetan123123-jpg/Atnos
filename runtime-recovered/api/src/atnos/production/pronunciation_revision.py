"""A scoped currency-pronunciation edit with fresh speech validation and retained failures."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re

import httpx

from atnos.intelligence.storage import connect, json_dumps, utc_now_iso
from atnos.production.models import StepResult
from atnos.production.narration_alignment import align_narration
from atnos.production.order_execution import StageExecutor
from atnos.production.order_store import ProductionOrderStore, OrderConflict, ProductionBlocked, _event, file_hash
from atnos.production.project_profile import content_hash
from atnos.production.steps.common import write_json
from atnos.production.steps import order_voice, voice_synthesis


def alias_alignment(segment, alignment, duration):
    """Keep the original currency caption while timing its equivalent expanded spoken name."""
    spoken = re.sub(r"\bzlotys\b", "Polish zloty", segment["text"])
    if spoken == segment["text"]:
        raise ValueError("This edit only supports the identified zlotys pronunciation")
    normalize = lambda text: re.sub(r"[^\w ]", "", text.casefold())
    if normalize("".join(alignment.get("characters", []))) == normalize(segment["text"]):
        return (*align_narration([segment],alignment,duration=duration), spoken)
    timings, words = align_narration([{**segment,"text":spoken}],alignment,duration=duration)
    revised, cursor = [], 0
    for original in segment["text"].split():
        count = 2 if re.sub(r"\W", "", original) == "zlotys" else 1
        chunk = words[cursor:cursor+count]
        revised.append({**chunk[0],"text":original,"end_sec":chunk[-1]["end_sec"]})
        cursor += count
    if cursor != len(words):
        raise ValueError("Pronunciation alignment cannot be mapped to the original caption")
    timings[0]["text"] = segment["text"]
    return timings,revised,spoken


async def repair_currency(settings,job_id,segment_id, *, approved=False):
    if not approved:
        raise ValueError("Explicit editorial pronunciation correction required")
    store = ProductionOrderStore(settings)
    state, context = store.get(job_id),store.context(job_id)
    stage = f"voice/{segment_id}"
    if state["worker_live"] or state["status"] != "blocked" or state["current_step"] not in {stage,f"voice_pronunciation_qa/{segment_id}"}:
        raise OrderConflict("Only the idle failed voice segment can be edited")
    rows = {row["stage"]:row for row in state["checkpoints"]}
    error = json.loads(rows[stage]["error_json"] or "{}")
    if error.get("category") != "pronunciation":
        raise ValueError("This correction cannot override other failures")
    script = rows["script"]["output"]
    index = next(i for i,s in enumerate(script["segments"]) if s["id"] == segment_id)
    segment = script["segments"][index]
    if "zlotys" not in segment["text"]:
        raise ValueError("No affected currency term")
    config = state["profile"]["production"]["voice"]
    settings = order_voice.voice_settings(settings,config)
    model = settings.data_dir / "models/whisper/ggml-small.en.bin"
    if not model.is_file():
        raise ProductionBlocked("Independent small English Whisper model is required")
    settings = settings.model_copy(update={"whisper_model_path":model})
    before = script["segments"][index-1]["text"] if index else ""
    following = script["segments"][index+1]["text"] if index+1<len(script["segments"]) else ""
    directory = context.artifact_dir / "audio" / f"{segment_id}_pronunciation"
    directory.mkdir(exist_ok=True)
    with store.lease(job_id) as token:
        executor = StageExecutor(store,job_id,token)
        dictionary_path = directory / "dictionary.json"
        if dictionary_path.is_file():
            dictionary = json.loads(dictionary_path.read_text())
            if dictionary.get("status") != "created":
                raise ProductionBlocked("Dictionary creation outcome requires reconciliation, not another POST")
        else:
            write_json(dictionary_path,{"status":"creating"})
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(settings.elevenlabs_base_url.rstrip('/')+'/v1/pronunciation-dictionaries/add-from-rules',
                    headers={"xi-api-key":settings.elevenlabs_api_key}, json={"name":f"Atnos PLN pronunciation {job_id}",
                    "rules":[{"type":"alias","string_to_replace":"zlotys","alias":"Polish zloty"}]})
                response.raise_for_status()
                raw = response.json()
            dictionary = {"status":"created","id":raw["id"],"version_id":raw["version_id"],"alias":{"zlotys":"Polish zloty"}}
            write_json(dictionary_path,dictionary)
        locator = [{"pronunciation_dictionary_id":dictionary["id"],"version_id":dictionary["version_id"]}]
        async def synthesize():
            raw, pcm = directory / "provider.mp3",directory / "narration.wav"
            provider = await voice_synthesis._try_elevenlabs_tts(settings,text=segment["text"],output_path=raw,
                voice_profile=order_voice.order_voice_profile(context),with_timestamps=True,
                previous_text=before,next_text=following,pronunciation_dictionary_locators=locator)
            if provider.get("ok") is not True:
                raise RuntimeError("Pronunciation-aware ElevenLabs generation failed: " + str(provider.get("error")))
            await asyncio.to_thread(order_voice.decode_pcm,order_voice.resolve_ffmpeg(),raw,pcm)
            duration = voice_synthesis._wav_duration(pcm)
            timings,words,spoken = alias_alignment(segment,provider["alignment"],duration)
            payload = {"output_file":str(pcm),"sha256":file_hash(pcm),"duration_seconds":duration,"timestamps":timings,
                "word_timestamps":words,"provider":provider,"text":segment["text"],"spoken_text":spoken,
                "timing_source":"provider_character_alignment","pronunciation_dictionary":dictionary}
            result = write_json(directory / "voice.json",payload)
            return StepResult(result.artifact_name,result.artifact_path,payload,result.message,(pcm,raw,dictionary_path))
        part = await executor.run(f"voice_pronunciation/{segment_id}",{"segment":segment,"voice":config,"dictionary":dictionary},synthesize,
            provider_call=True,estimated_cost_usd=(len(segment["text"])+len(before)+len(following)+40)/1000*config["reservation_usd_per_1000_characters"])
        async def inspect():
            path = directory / "recognized.srt"
            text = await asyncio.to_thread(order_voice.transcribe,settings,Path(part["output_file"]),path,vocabulary=["Polish zloty"])
            verdict = order_voice.speech_match(part["spoken_text"],text,[])
            verdict.update({"asr_model":str(model),"asr_model_sha256":file_hash(model),"equivalent_currency_alias":dictionary["alias"],
                            "asr_vocabulary":["Polish zloty"],"asr_has_narration_prompt":False})
            result = write_json(path.with_suffix(".json"),verdict)
            return StepResult(result.artifact_name,result.artifact_path,verdict,result.message,(path,))
        qa_stage = f"voice_pronunciation_qa/{segment_id}"
        verdict = await executor.run(qa_stage,{"sha256":part["sha256"],"spoken_text":part["spoken_text"],"model_sha256":file_hash(model),"vocabulary":["Polish zloty"]},inspect)
        if verdict["status"] != "passed":
            store.fail(job_id,token,qa_stage,category="pronunciation",message=json.dumps(verdict))
            raise ProductionBlocked("Corrected pronunciation still requires review; no automatic paid retry")
        updated = {row["stage"]:row for row in store.get(job_id)["checkpoints"]}
        archive = directory / "superseded.json"
        write_json(archive,{"voice":rows[stage],"qa":rows[f"voice_qa/{segment_id}"]})
        with connect(settings) as db:
            db.execute("BEGIN IMMEDIATE")
            store._owner(db,job_id,token)
            for target,source,payload,digest in [(stage,f"voice_pronunciation/{segment_id}",part,rows[stage]["input_hash"]),
                (f"voice_qa/{segment_id}",qa_stage,verdict,content_hash({"sha256":part["sha256"],"segment":segment,"validator":2}))]:
                files = [*updated[source]["files"],{"path":archive.relative_to(context.artifact_dir).as_posix(),"sha256":file_hash(archive)}]
                db.execute("UPDATE pipeline_stage_checkpoints SET status='done',input_hash=?,output_json=?,files_json=?,error_json=NULL,updated_at=? WHERE job_id=? AND stage=?",
                    (digest,json_dumps(payload),json_dumps(files),utc_now_iso(),job_id,target))
                db.execute("UPDATE pipeline_job_steps SET status='done',progress=1,message=NULL,completed_at=? WHERE job_id=? AND step=?",(utc_now_iso(),job_id,target))
            db.execute("UPDATE pipeline_jobs SET status='queued',current_step='voice',error=NULL,updated_at=? WHERE id=?",(utc_now_iso(),job_id))
            _event(db,job_id,stage,"currency_pronunciation_corrected",{"dictionary":dictionary,"old_attempts":rows[stage]["attempts"],"archive_sha256":file_hash(archive),"budgets_reset":False})
            db.commit()
    return {"status":"passed","job_id":job_id,"segment_id":segment_id}


async def recheck_currency_asr(settings,job_id,segment_id):
    """Re-evaluate an existing recording with a larger ASR and vocabulary, never new TTS."""
    store=ProductionOrderStore(settings)
    state,context=store.get(job_id),store.context(job_id)
    stage=f"voice/{segment_id}"
    if state["worker_live"] or state["status"] != "blocked" or state["current_step"] != stage:
        raise OrderConflict("Only the idle blocked recording can be rechecked")
    rows={r["stage"]:r for r in state["checkpoints"]}
    original=rows[stage]
    if json.loads(original["error_json"] or "{}").get("category") != "pronunciation":
        raise ValueError("This review cannot override unrelated failures")
    if not store._valid_files(context.artifact_dir,original["files_json"]):
        raise ValueError("The original recording is missing or changed")
    segment=next(s for s in rows["script"]["output"]["segments"] if s["id"] == segment_id)
    if "zlotys" not in segment["text"]:
        raise ValueError("This recheck is scoped to the identified currency vocabulary")
    model=settings.data_dir/"models/whisper/ggml-small.en.bin"
    settings=settings.model_copy(update={"whisper_model_path":model,"whisper_language":"en"})
    part=original["output"]
    with store.lease(job_id) as token:
        executor=StageExecutor(store,job_id,token)
        directory=context.artifact_dir/"audio"/segment_id
        archive=directory/"qa_before_recheck.json"
        async def inspect():
            write_json(archive,{"voice":original,"qa":rows[f"voice_qa/{segment_id}"]})
            path=directory/"rechecked.srt"
            recognized=await asyncio.to_thread(order_voice.transcribe,settings,Path(part["output_file"]),path,vocabulary=["zlotys"])
            verdict=order_voice.speech_match(segment["text"],recognized,segment.get("pronunciation_terms",[]))
            verdict.update({"asr_model_sha256":file_hash(model),"asr_vocabulary":["zlotys"],"asr_has_narration_prompt":False})
            result=write_json(path.with_suffix(".json"),verdict)
            return StepResult(result.artifact_name,result.artifact_path,verdict,result.message,(path,archive))
        review_stage=f"voice_recheck/{segment_id}"
        verdict=await executor.run(review_stage,{"sha256":part["sha256"],"segment":segment,"model_sha256":file_hash(model),"vocabulary":["zlotys"]},inspect)
        if verdict["status"] != "passed":
            store.fail(job_id,token,review_stage,category="pronunciation",message=json.dumps(verdict))
            raise ProductionBlocked("The unchanged recording still fails independent speech review")
        reviewed=next(r for r in store.get(job_id)["checkpoints"] if r["stage"] == review_stage)
        with connect(settings) as db:
            db.execute("BEGIN IMMEDIATE")
            store._owner(db,job_id,token)
            entries=store._files(context.artifact_dir,[archive])
            db.execute("UPDATE pipeline_stage_checkpoints SET status='done',files_json=?,error_json=NULL,updated_at=? WHERE job_id=? AND stage=?",
                (json_dumps([*original["files"],*entries]),utc_now_iso(),job_id,stage))
            db.execute("UPDATE pipeline_stage_checkpoints SET output_json=?,files_json=?,error_json=NULL,updated_at=? WHERE job_id=? AND stage=?",
                (json_dumps(verdict),reviewed["files_json"],utc_now_iso(),job_id,f"voice_qa/{segment_id}"))
            db.execute("UPDATE pipeline_job_steps SET status='done',progress=1,message=NULL,completed_at=? WHERE job_id=? AND step=?",(utc_now_iso(),job_id,stage))
            db.execute("UPDATE pipeline_jobs SET status='queued',current_step='voice',error=NULL,updated_at=? WHERE id=?",(utc_now_iso(),job_id))
            _event(db,job_id,stage,"currency_asr_revalidated",{"recording_sha256_unchanged":part["sha256"],"paid_calls_added":0,"original_attempts":original["attempts"],"archive_sha256":file_hash(archive)})
            db.commit()
    return {"status":"passed","job_id":job_id,"segment_id":segment_id,"paid_calls_added":0}
