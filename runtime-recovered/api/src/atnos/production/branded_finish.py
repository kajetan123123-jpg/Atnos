"""Measured narration, composition, QA and packaging for an Atnos brand trial."""
from __future__ import annotations
import asyncio
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import subprocess

import numpy as np
import soundfile as sf

from atnos.production.brand_contract import verified_asset
from atnos.production.branded_production import result
from atnos.production.order_store import ProductionBlocked,file_hash,checkpoint_fingerprints
from atnos.production.project_profile import content_hash
from atnos.production.media_tools import resolve_ffmpeg
from atnos.production.steps.common import write_json
from atnos.production.steps.voice_synthesis import _srt_ts
from atnos.services.kokoro_local_runtime_service import KokoroLocalRuntimeService
from atnos.services.whisper_cpp_subtitle_service import WhisperCppSubtitleService


def run(command,log,timeout=400):
    log.parent.mkdir(parents=True,exist_ok=True)
    with log.open('w',encoding='utf-8') as stream:
        p=subprocess.run([str(c) for c in command],stdout=stream,stderr=stream,timeout=timeout,
                         creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if p.returncode:
        raise RuntimeError('Media operation failed: '+str(log))


def make_voice(settings,root,script,kit):
    """Synthesize each measured utterance and retain the storyboard's intentional pauses."""
    service=KokoroLocalRuntimeService(settings)
    rate=24000
    voice=np.zeros(kit['duration']*rate,dtype=np.float32)
    timings=[]
    evidence=[]
    for scene in script['segments']:
        if not scene['text']:continue
        path=root/'audio'/(scene['id']+'.wav')
        receipt=path.with_suffix('.json')
        # This speed is selected before synthesis, never a post-hoc audio time stretch.
        available=scene['end']-scene['start']-.4
        speed=max(.95,min(1.2,len(scene['text'].split())/(available*2.55)))
        ident=content_hash({'text':scene['text'],'voice':kit['voice'],'speed':speed})
        if receipt.exists():
            r=json.loads(receipt.read_text(encoding='utf-8'))
            if r['input_hash']!=ident or r['sha256']!=file_hash(path):raise ValueError('Stale narration cache')
        else:
            detail=service.synthesize_to_wav(text=scene['text'],output_path=path,voice_name=kit['voice'],lang='en-us',speed=speed)
            if detail['voice_name']!=kit['voice']:raise ValueError('Voice substitution is not allowed')
            write_json(receipt,{'input_hash':ident,'sha256':file_hash(path),'provider':detail})
        raw,sr=sf.read(path,dtype='float32')
        if sr!=rate or raw.ndim!=1:raise ValueError('Unexpected voice sample format')
        active=np.flatnonzero(np.abs(raw)>.004)
        if not len(active):raise ValueError('Silent narration')
        trimmed=raw[max(0,active[0]-int(.055*rate)):min(len(raw),active[-1]+int(.09*rate))]
        duration=len(trimmed)/rate
        if duration>scene['end']-scene['start']-.2:
            raise ValueError(f"{scene['id']}: narration {duration:.2f}s does not fit its fixed scene")
        start=scene['start']+.13
        offset=round(start*rate)
        voice[offset:offset+len(trimmed)]=trimmed
        timings.append({'id':scene['id'],'start':start,'end':start+duration,'text':scene['text']})
        evidence.extend([path,receipt])
    # Original quiet plucked notes and short pencil/token sounds, procedurally synthesized by the renderer.
    axis=np.arange(len(voice))/rate
    bed=np.zeros_like(voice)
    notes=[130.81,196,220,164.81]
    for i,at in enumerate(np.arange(.5,44,1.875)):
        local=axis-at
        env=np.where(local>=0,np.exp(-np.maximum(local,0)*3.5),0)
        bed+=.009*env*(np.sin(2*np.pi*notes[i%4]*local)+.2*np.sin(4*np.pi*notes[i%4]*local))
    rng=np.random.default_rng(60914)
    for at in (3.0,4.3,8.7,9.6,10.5,11.4,12.3,15.2,19.2,25.5,31.4,37.3,42.2):
        local=axis-at
        env=np.where(local>=0,np.exp(-np.maximum(local,0)*35),0)
        bed+=.008*env*rng.normal(size=len(voice))
    bed*=np.minimum(1,axis/.5)*np.minimum(1,(45-axis)/.8)
    sf.write(root/'voice-clean.wav',voice,rate)
    mixed=voice+bed
    sf.write(root/'mix-raw.wav',np.stack([mixed,mixed],axis=1),rate)
    ff=resolve_ffmpeg()
    run([ff,'-y','-v','error','-i',root/'mix-raw.wav','-af','loudnorm=I=-16:TP=-1.5:LRA=8','-ar','48000',root/'mix.wav'],root/'mix.log')
    run([ff,'-y','-v','error','-i',root/'voice-clean.wav','-ar','16000','-ac','1',root/'asr.wav'],root/'asr-convert.log')
    svc=WhisperCppSubtitleService(settings.model_copy(update={'whisper_language':'en'}))
    runtime=svc.runtime_availability()
    if not runtime.available:raise ProductionBlocked('Independent Whisper audio validation is unavailable')
    run(svc.build_command(audio_path=root/'asr.wav',output_path=root/'transcript.srt'),root/'transcript.log')
    heard=' '.join(x.strip() for x in (root/'transcript.srt').read_text(encoding='utf-8').splitlines() if x.strip() and not x.strip().isdigit() and '-->' not in x)
    normal=lambda s:re.findall(r'[a-z]+',s.casefold())
    score=SequenceMatcher(None,normal(script['text']),normal(heard),autojunk=False).ratio()
    if score<.9:raise ValueError(f'Independent voice recognition differs from script: {score:.3f}')
    run([runtime.executable_path,'-m',runtime.model_path,'-f',root/'asr.wav','-l','en','-ojf','-ml','1','-sow','-of',root/'word-times'],root/'word-times.log')
    observations=json.loads((root/'word-times.json').read_text(encoding='utf-8'))['transcription']
    observations=[r for r in observations if normal(r['text']) and r['offsets']['to']>r['offsets']['from']]
    cues=[]
    for scene in timings:
        words=scene['text'].split()
        obs=[r for r in observations if scene['start']-.2<=r['offsets']['from']/1000<=scene['end']+.2]
        norm=lambda s:''.join(normal(s))
        anchors={}
        for match in SequenceMatcher(None,[norm(w) for w in words],[norm(r['text']) for r in obs],autojunk=False).get_matching_blocks():
            for k in range(match.size):
                r=obs[match.b+k];anchors[match.a+k]=(r['offsets']['from']/1000,r['offsets']['to']/1000)
        for i in range(0,len(words),5):
            count=min(5,len(words)-i)
            start=anchors.get(i,(scene['start']+(scene['end']-scene['start'])*i/len(words),0))[0]
            end=anchors.get(i+count-1,(0,scene['start']+(scene['end']-scene['start'])*(i+count)/len(words)))[1]
            start=max(scene['start'],min(start,scene['end']-.08));end=min(scene['end'],max(start+.08,end))
            if cues:cues[-1]['end']=min(cues[-1]['end'],start)
            cues.append({'start':start,'end':end,'text':' '.join(words[i:i+count])})
    if any(c['end']<=c['start'] for c in cues):raise ValueError('Overlapping caption word anchors need review')
    (root/'captions.srt').write_text('\n\n'.join(f"{i+1}\n{_srt_ts(c['start'])} --> {_srt_ts(c['end'])}\n{c['text']}" for i,c in enumerate(cues))+'\n',encoding='utf-8')
    payload={'qa':{'status':'passed','asr_sequence_similarity':score,'recognized_text':heard,'human_listening':'not_performed'},
             'duration_seconds':45,'timings':timings,'cues':cues,'voice':'Kokoro / '+kit['voice'],
             'caption_alignment':'Whisper word anchors constrained by measured scene audio; unmatched words proportionally estimated',
             'audio_source':'Local TTS and original procedural sound bed','no_time_stretch':True}
    evidence.extend(root/name for name in ('voice-clean.wav','mix-raw.wav','mix.wav','asr.wav','transcript.srt','word-times.json','captions.srt'))
    return result(root/'voice.json',payload,evidence)


async def prepare_voice(executor,script):
    ctx=executor.context;kit=ctx.artifacts['project_profile']['brand_kit']['contract']
    async def synthesize():
        return await asyncio.to_thread(make_voice,executor.store.settings,ctx.artifact_dir,script,kit)
    voice=await executor.run('voice',{'script':script,'voice':kit['voice'],'voice_contract':1},synthesize,local_model_call=True)
    ctx.artifacts['voice']=voice
    async def caps():return write_json(ctx.artifact_dir/'captions.json',{'qa':{'status':'passed'},'cues':voice['cues'],'alignment':voice['caption_alignment']})
    await executor.run('captions',{'voice':voice},caps)
    return voice


def check_branded_media(root, script):
    """Keep successful silent decoder logs diagnostic, not mandatory evidence."""
    from atnos.production.qa_gate import probe_media
    video = root/'short.mp4'
    probe = probe_media(video)
    run([resolve_ffmpeg(), '-v', 'error', '-i', video, '-f', 'null', '-'], root/'decode.log')
    if probe.get('width') != 1080 or probe.get('height') != 1920 or abs(probe.get('duration_s', 0)-45) > .1:
        raise ValueError('Unexpected output media dimensions or duration: '+json.dumps(probe))
    samples = root/'decoded-frames'
    samples.mkdir(exist_ok=True)
    evidence = []
    timestamps = {}
    for scene in script['segments']:
        at = scene['end']-.5
        path = samples/(scene['id']+'.png')
        run([resolve_ffmpeg(), '-y', '-v', 'error', '-ss', str(at), '-i', video,
             '-frames:v', '1', path], samples/(scene['id']+'.log'))
        evidence.append(path)
        timestamps[scene['id']] = at
    return result(root/'media_qa.json', {'status': 'passed', 'probe': probe,
        'video_sha256': file_hash(video), 'full_decode': 'passed', 'decoder_exit_code': 0,
        'decoded_sample_timestamps': timestamps}, evidence)


async def finish_branded(executor,script,requests,*,image_override=None,scope='four_pose_trial'):
    from atnos.production.branded_renderer import render
    ctx=executor.context;root=ctx.artifact_dir;kit=ctx.artifacts['project_profile']['brand_kit']['contract']
    images=image_override or {r['asset_id'].removeprefix('alex_'):verified_asset(root,r) for r in requests}
    assets_hash={key:file_hash(path) for key,path in images.items()}
    voice=await prepare_voice(executor,script)
    async def edit():
        payload=await asyncio.to_thread(render,root,kit,script,voice,images)
        return result(root/'edit.json',payload,[root/'short.mp4',*sorted((root/'frames').glob('*.png'))])
    edit=await executor.run('edit',{'script':script,'voice':voice,'assets':assets_hash,'renderer_version':2,'scope':scope},edit)
    async def media():
        return await asyncio.to_thread(check_branded_media, root, script)
    await executor.run('media_qa',{'video_sha256':file_hash(root/'short.mp4'), 'validator_version': 2},media)
    # A local preview file is reviewable before editorial approval. Ready status still requires actual evidence.
    review_path=root/'supervisor-review.json'
    if not review_path.is_file():
        await asyncio.to_thread(executor.store.fail,ctx.job_id,executor.token,'supervisor_review',
            category='awaiting_supervisor_review',message='Atnos wyrenderował próbę; oczekuje oceny obrazu, ciągłości Alexa i zgodności ujęć z briefem.')
        return executor.store.get(ctx.job_id)
    review=json.loads(review_path.read_text(encoding='utf-8'))
    if review.get('status')!='passed' or review.get('video_sha256')!=file_hash(root/'short.mp4') or review.get('assets_sha256')!=assets_hash:
        raise ProductionBlocked('Supervisor review does not approve this exact film and artwork')
    async def semantic():return result(root/'semantic_qa.json',review,[review_path,*images.values(),*(p.with_suffix('.json') for p in images.values())])
    await executor.run('semantic_qa',{'review':review},semantic)
    async def package():
        rows=executor.store.get(ctx.job_id)['checkpoints']
        files={root/entry['path'] for row in rows if row['stage']!='package' for entry in row['files']}
        payload={'status':'passed','output_file':str(root/'short.mp4'),'checkpoint_fingerprints':checkpoint_fingerprints(rows),
                 'production':'Atnos branded production order','external_graphics':'Codex imagegen supervised tool relay',
                 'scope':scope,'publish':False,'brand_kit':ctx.artifacts['project_profile']['brand_kit'],
                 'costs':{'Atnos_API_reserved_usd':executor.store.get(ctx.job_id)['reserved_cost_units']/1e6,'external_tool_usage':'Amount unavailable; do not report as free'}}
        return result(root/'package.json',payload,sorted(files))
    await executor.run('package',{'video_sha256':file_hash(root/'short.mp4'),'review':review},package)
    return await asyncio.to_thread(executor.store.finish,ctx.job_id,executor.token,root/'short.mp4')
