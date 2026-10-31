"""Bounded WB voice candidates through existing local or paid synthesis adapters."""
from __future__ import annotations
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from atnos.production.order_store import ProductionOrderStore
from atnos.production.project_manifest import file_sha256
from atnos.production.project_profile import content_hash
from atnos.production.steps import order_voice, voice_synthesis
from atnos.production.steps.common import write_json


def audition_variants(base: dict, comparison: str):
    common={**base,'speed':1.0,'pacing_mode':'fixed','stability':.5,'similarity':.75,'style':0.,'speaker_boost':False}
    if comparison == 'voices':
        return [('A-Chris',common),('B-Eric',{**common,'voice_id':'cjVigY5qzO86Huf0OWal'})]
    if comparison == 'eric-intonation':
        # Same words, model, speed and timbre settings; only stability varies.
        # Listening determines suitability. These are candidates, not a new default.
        eric={**common,'voice_id':'cjVigY5qzO86Huf0OWal'}
        return [('Eric-A-035',{**eric,'stability':.35}),('Eric-B-070',{**eric,'stability':.7})]
    if comparison == 'eric-model':
        # Compare with the stored Eric/v2/.5 baseline. No duplicate paid baseline.
        return [('Eric-v3',{**common,'voice_id':'cjVigY5qzO86Huf0OWal','model':'eleven_v3'})]
    if comparison == 'wb-alternatives':
        # Availability checked against the account's voice catalog on 2026-09-26.
        # Same model/text/settings as the saved Eric-v3 reference; voice only changes.
        # Provider descriptions are selection hints, never proof of naturalness.
        v3={**common,'model':'eleven_v3'}
        return [('Roger-v3',{**v3,'voice_id':'CwhRBWXzGAHq8TQ4Fs17'}),
                ('Brian-v3',{**v3,'voice_id':'nPczCjzI2devNBz1zQrb'})]
    raise ValueError('Unknown voice comparison')


def prepare_local_voice_audition(settings, job_id: str, output: Path, *,
                                 segment_ids: list[str], feedback_ids: list[str]) -> dict:
    """Write an unapproved draft only: no inference, defaults or production changes."""
    from atnos.services import qwen_tts_local_runtime_service as qwen

    output = output.resolve()
    if (output.drive.casefold() != 'e:' or not output.is_relative_to(qwen.AUDITION_REPORT_ROOT.resolve())
            or output.name != job_id or output.parent.name != 'voice-auditions'):
        raise ValueError('Local audition must use reports/.../voice-auditions/<source-job> on E:')
    source = qwen.order_audition_source(settings, job_id, feedback_ids)
    script = json.loads(Path(source['source_script_path']).read_text(encoding='utf-8'))
    if (not isinstance(segment_ids, list) or not segment_ids
            or any(not isinstance(item, str) or not item for item in segment_ids)
            or len(set(segment_ids)) != len(segment_ids)):
        raise ValueError('Expected unique source segment IDs')
    selected = [item for item in script['segments'] if item.get('id') in segment_ids]
    if ([item.get('id') for item in selected] != segment_ids
            or any(not isinstance(item.get('text'), str) or not item['text'].strip() for item in selected)):
        raise ValueError('Audition must use ordered, complete source thoughts')
    # Preserve the paragraph boundary used by single-pass production narration.
    text = '\n\n'.join(item['text'].strip() for item in selected)
    if len(text) > 700 or len(text.split()) > 80 or any(ord(char) < 32 and char != '\n' for char in text):
        raise ValueError('Audition exceeds 80 words or 700 script characters')
    request = {
        'schema_version': 2, 'status': 'candidate_unapproved', 'user_approved': False,
        'production_enabled': False, 'cost_usd': 0, 'scope': 'voice_audition_only',
        'request_path': str(output / 'request.json'),
        **source, 'feedback_ids': feedback_ids, 'segment_ids': segment_ids, 'text': text,
        'model_id': qwen.MODEL_ID, 'model_revision': qwen.MODEL_REVISION, 'language': 'English',
        'samples': [{'sample_id': 'ryan_neutral', 'speaker': 'Ryan',
                     'preset': qwen.COMPARISON_SAMPLES['ryan_neutral'][1]}],
        'max_new_synthesis_requests': 1, 'preserve_current_mastering': True,
        'max_cpu_threads': 4, 'timeout_seconds': qwen.AUDITION_TIMEOUT_SECONDS,
        'execution_authorization': None,
    }
    output.mkdir(parents=True, exist_ok=True)
    with (output / 'request.json').open('x', encoding='utf-8') as handle:
        json.dump(request, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    return request


async def _create_local_voice_audition(settings, job_id: str, output: Path, request_sha256: str | None):
    from atnos.services import qwen_tts_local_runtime_service as qwen

    if not request_sha256:
        raise ValueError('Local audition requires the exact persisted, authorized request SHA-256')
    request_path = output / 'request.json'
    request = json.loads(request_path.read_text(encoding='utf-8'))
    if request.get('schema_version') != 2 or request.get('source_job_id') != job_id:
        raise ValueError('Local audition source does not match the request')
    final = output / 'samples' / 'ryan_neutral-mastered.wav'
    if final.exists() or (output / 'audition.json').exists():
        raise ValueError('Local audition output already exists; never replace candidates')
    # One request, one exclusive runtime receipt. Failure/uncertainty consumes it;
    # neither this caller nor the adapter retries inference or falls back to paid TTS.
    raw = await qwen.QwenTTSLocalRuntimeService(timeout_seconds=qwen.AUDITION_TIMEOUT_SECONDS).synthesize_comparison(
        request_path=request_path, request_sha256=request_sha256, sample_id='ryan_neutral',
        output_path=output / 'samples' / 'ryan_neutral.wav', settings=settings)
    if final.exists():
        raise ValueError('Local mastered output appeared during synthesis; refusing to replace it')
    normalization = await voice_synthesis._normalize_audio(Path(raw['output_path']), final)
    if normalization.get('status') != 'normalized' or normalization.get('loudness_target_applied') is not True:
        raise ValueError('Local candidate did not preserve the current loudness mastering; inspect raw receipt')
    if file_sha256(Path(raw['output_path'])) != raw['output_sha256']:
        raise ValueError('Local raw candidate changed during mastering')
    qwen._validate_order_audition(request, request_path, settings)
    if file_sha256(request_path) != request_sha256:
        raise ValueError('Local audition request changed during mastering')
    record = {
        'name': 'Ryan-neutral-current-text', 'comparison': 'local-interpretation',
        'source_job_id': job_id, 'source_script_sha256': request['source_script_sha256'],
        'request_path': str(request_path), 'request_sha256': request_sha256,
        'text': request['text'], 'raw_receipt_path': str(Path(raw['output_path']).with_suffix('.json')),
        'raw_sha256': raw['output_sha256'], 'output_file': str(final), 'sha256': file_sha256(final),
        'duration_seconds': voice_synthesis._wav_duration(final), 'normalization': normalization,
        'changed_parameter': 'interpretation_instruction', 'provider_calls': 1, 'cost_usd': 0,
        'time_stretch': False, 'user_approved': False, 'production_enabled': False,
        'publication_allowed': False, 'human_listening': 'not_performed',
        'qa': {'status': 'candidate_unapproved', 'speech_match': 'not_performed'},
    }
    write_json(output / 'audition.json', record)
    return [record]


async def create_voice_audition(settings, job_id: str, output: Path, *, comparison: str = 'voices',
                                request_sha256: str | None = None):
    output=output.resolve()
    if output.drive.upper() != 'E:':
        raise ValueError('Voice audition outputs must stay on E:')
    if comparison == 'local-interpretation':
        return await _create_local_voice_audition(settings, job_id, output, request_sha256)
    output.mkdir(parents=True,exist_ok=True)
    source=ProductionOrderStore(settings).context(job_id)
    if source.artifacts['project_profile']['project'] != 'wealth_blueprint':
        raise ValueError('WB voice audition requires a WB source order')
    script=json.loads((source.artifact_dir/'script.json').read_text(encoding='utf-8'))
    selected=[s for s in script['segments'] if s['id'] in {'s04','s05'}]
    if len(selected)!=2:
        raise ValueError('Expected two complete source thoughts s04 and s05')
    text=' '.join(s['text'] for s in selected)
    if len(text)>700:
        raise ValueError('Audition exceeds the short sample budget')
    base=dict(source.artifacts['project_profile']['production']['voice'])
    variants=audition_variants(base,comparison)
    ledger_path=output/'budget.json'
    ledger=json.loads(ledger_path.read_text()) if ledger_path.exists() else {'maximum_calls':2,'maximum_reserved_usd':2.,'calls':[]}
    results=[]
    for name,config in variants:
        key=content_hash({'text':text,'voice':config,'contract':1})
        prior=next((c for c in ledger['calls'] if c['input_hash']==key),None)
        directory=output/name
        if prior:
            if prior.get('status')!='complete':
                raise ValueError('Previous paid attempt is incomplete; inspect it before retrying')
            record=json.loads((directory/'candidate.json').read_text())
            if file_sha256(Path(record['output_file']))!=record['sha256']:
                raise ValueError('Audition candidate changed after generation')
            results.append(record)
            continue
        estimate=len(text)/1000*config['reservation_usd_per_1000_characters']
        if len(ledger['calls'])>=2 or sum(c['reserved_usd'] for c in ledger['calls'])+estimate>2:
            raise ValueError('Voice audition budget exhausted')
        # Reserve before the request. An uncertain request is never silently repeated.
        attempt={'name':name,'input_hash':key,'reserved_usd':estimate,'status':'reserved'}
        ledger['calls'].append(attempt)
        write_json(ledger_path,ledger)
        scoped=order_voice.voice_settings(settings,config)
        context=SimpleNamespace(artifact_dir=directory,artifacts={'project_profile':{
            'project':'wealth_blueprint','production':{'language':'en','voice':config}}})
        segment={'id':'audition','text':text}
        raw=await order_voice.synthesize(scoped,context,segment,'','')
        final=directory/'candidate.wav'
        normalization=await voice_synthesis._normalize_audio(Path(raw.payload['output_file']),final)
        if normalization.get('status')!='normalized':
            raise ValueError('Candidate normalization failed')
        recognized=await asyncio.to_thread(order_voice.transcribe,scoped,final,directory/'recognized.srt')
        qa=order_voice.speech_match(text,recognized,[])
        record={'name':name,'comparison':comparison,'output_file':str(final),'sha256':file_sha256(final),
            'source_job_id':job_id,'source_script_sha256':file_sha256(source.artifact_dir/'script.json'),
            'text':text,'voice':config,'duration_seconds':voice_synthesis._wav_duration(final),
            'qa':qa,'normalization':normalization,'input_hash':key,
            'human_listening':'not_performed','user_approved':False,'publication_allowed':False,
            'time_stretch':False,'synthesis_mode':'one_request_for_two_complete_thoughts'}
        write_json(directory/'candidate.json',record)
        attempt.update(status='complete',sha256=record['sha256'])
        write_json(ledger_path,ledger)
        results.append(record)
    write_json(output/'audition.json',{'source_job_id':job_id,'comparison':comparison,
        'changed_parameter':{'eric-intonation':'stability','eric-model':'model','voices':'voice_id','wb-alternatives':'voice_id'}[comparison],
        'user_feedback':'Sztuczna intonacja i akcentowanie; dwie poprzednie próbki nadal sztuczne' if comparison=='wb-alternatives' else ('Sztuczna intonacja i akcentowanie' if comparison.startswith('eric-') else ''),
        'candidates':results,'user_approved':False})
    return [{'name':r['name'],'output_file':r['output_file'],'duration_seconds':r['duration_seconds'],'qa':r['qa']['status']} for r in results]
