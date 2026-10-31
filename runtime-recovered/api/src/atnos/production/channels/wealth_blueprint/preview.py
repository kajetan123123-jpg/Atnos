"""Bounded review export using Atnos scripts, recorded speech, planner, adapter and renderer.

No source order, approval, final MP4 or publication state is altered.
"""
from __future__ import annotations
import asyncio
import json
import math
from pathlib import Path
from types import SimpleNamespace
import wave
from atnos.core.config import get_settings
from atnos.production.order_store import ProductionOrderStore
from atnos.production.channels.wealth_blueprint.planner import WealthBlueprintScenePlanner
from atnos.production.channels.wealth_blueprint.scene_plan_models import ScenePlannerInput, NarrationSegment, NarrationWord
from atnos.production.channels.wealth_blueprint.manifest_adapters import scene_plan_to_project_manifest
from atnos.production.project_manifest import file_sha256, save_project_manifest
from atnos.production.remotion_renderer import render_project_manifest
from atnos.production.steps.order_voice import assemble_audio
from atnos.production.steps.voice_synthesis import _normalize_audio
from atnos.production.steps.assembler import _mux_remotion_video

def _source_order(source: Path, settings):
    order = ProductionOrderStore(settings).get(source.name)
    if Path(order['artifact_dir']).resolve() != source.resolve() or order['request']['project'] != 'wealth_blueprint':
        raise ValueError('Review source must be the registered WB production directory')
    return order


def _local_voice_snapshot(source: Path, order: dict, script: dict, settings) -> dict | None:
    """Validate the completed, single-pass local narration and its independent evidence."""
    registration = source / "voice" / "local_voice_lane.json"
    if not registration.is_file():
        return None

    from atnos.production.local_voice_lane import load_local_voice_lane
    from atnos.production.project_profile import content_hash

    lane = load_local_voice_lane(settings, source.name, read_only_snapshot=True)
    if lane is None:
        raise ValueError("Registered local voice lane is missing its immutable authorization")
    profile = order.get("profile") if isinstance(order.get("profile"), dict) else {}
    style = profile.get("visual_style") if isinstance(profile.get("visual_style"), dict) else None
    if (not style or style.get("id") != "D"
            or order.get("request", {}).get("visual_style_id") != "D"):
        raise ValueError("Local Ryan review requires this order's frozen Wealth Blueprint style D")

    stages = {row.get("stage"): row for row in order.get("checkpoints", [])}

    def completed(stage: str, expected_input_hash: str | None = None) -> dict:
        row = stages.get(stage)
        if not row or row.get("status") != "done":
            raise ValueError(f"Local voice preview requires completed {stage} evidence")
        if expected_input_hash is not None and row.get("input_hash") != expected_input_hash:
            raise ValueError(f"Local voice preview found stale {stage} inputs")
        files = row.get("files") or []
        if not files:
            raise ValueError(f"Local voice preview requires immutable file evidence for {stage}")
        for entry in files:
            path = (source / str(entry.get("path") or "")).resolve()
            if not path.is_relative_to(source.resolve()) or not path.is_file():
                raise ValueError(f"Local voice preview found missing or escaped {stage} evidence")
            if file_sha256(path) != entry.get("sha256"):
                raise ValueError(f"Local voice preview found changed {stage} evidence")
        return row

    def verified_json_output(row: dict, stage: str) -> tuple[dict, Path]:
        # Checkpoint files, already hash-verified above, define the current
        # artifact. A naming convention is not proof of its identity.
        matches = []
        for entry in row.get('files', []):
            path = source / str(entry.get('path') or '')
            if path.suffix.casefold() != '.json':
                continue
            value = json.loads(path.read_text(encoding='utf-8'))
            if value == row.get('output'):
                matches.append((value, path))
        if len(matches) != 1:
            raise ValueError(f'Local voice preview requires one verified JSON output for {stage}')
        return matches[0]

    segments = script.get("segments") or []
    if not segments or any(not str(item.get("text") or "").strip() for item in segments):
        raise ValueError("Local voice preview requires the registered final script")
    script_hash = content_hash({"segments": segments})
    script_row = completed("script")
    script_payload = script_row.get("output") or {}
    if script_payload.get("segments") != segments:
        raise ValueError("Local voice preview script differs from the completed script checkpoint")

    synthesis_inputs = {"lane": lane.as_dict(), "script_hash": script_hash,
                        "text": "\n\n".join(item["text"].strip() for item in segments), "synthesis_contract": 1}
    synthesis = completed("voice/local_ryan/narration", content_hash(synthesis_inputs))
    source_voice = synthesis.get("output") or {}
    raw_audio = Path(str(source_voice.get("output_file") or "")).resolve()
    if (not raw_audio.is_relative_to(source.resolve()) or not raw_audio.is_file()
            or file_sha256(raw_audio) != source_voice.get("sha256")
            or source_voice.get("lane_digest") != lane.digest
            or source_voice.get("timing_source") != "local_ctc_reference_alignment"):
        raise ValueError("Local voice preview source recording is missing or no longer matches its synthesis receipt")
    audio_sha = str(source_voice["sha256"])

    qa_inputs = {"sha256": audio_sha, "script_hash": script_hash, "validator": 3,
                 "scope": "job_scoped_local_voice"}
    qa_row = completed("voice/local_ryan_qa/narration", content_hash(qa_inputs))
    qa, qa_path = verified_json_output(qa_row, 'voice/local_ryan_qa/narration')
    if (qa_row.get("output") != qa or qa.get("status") != "passed"
            or qa.get("scope") != "full_narration" or qa.get("expected_prompt_provided") is not False
            or not str(qa.get("recognized_text") or "").strip()):
        raise ValueError("Local voice preview requires the independent, no-reference-prompt full-narration ASR receipt")

    align_inputs = {"audio_sha256": audio_sha, "script_hash": script_hash,
                    "aligner": "wav2vec2-asr-base-960h", "contract": 1}
    alignment_row = completed("voice/local_ryan/alignment", content_hash(align_inputs))
    alignment, alignment_path = verified_json_output(alignment_row, 'voice/local_ryan/alignment')
    if (alignment_row.get("output") != alignment or alignment.get("audio_sha256") != audio_sha
            or alignment.get("script_hash") != script_hash
            or alignment.get("timing_source") != "local_ctc_reference_alignment"):
        raise ValueError("Local voice preview requires CTC alignment for the exact approved source recording and final script")

    voice_path = source / "audio_timestamps.json"
    voice = json.loads(voice_path.read_text(encoding="utf-8"))
    voice_row = completed("voice")
    if (voice_row.get("output") != voice or voice.get("production_ready") is not True
            or voice.get("timing_source") != "local_ctc_reference_alignment"
            or voice.get("qa", {}).get("status") != "passed"
            or not voice.get("qa", {}).get("segments")
            or voice["qa"]["segments"][0] != qa):
        raise ValueError("Local voice preview requires the completed production voice artifact")
    source_recording = voice.get("source_recording") or {}
    if (Path(str(source_recording.get("output_file") or "")).resolve() != raw_audio
            or source_recording.get("sha256") != audio_sha):
        raise ValueError("Final voice manifest is not bound to the independently checked source recording")
    final_audio = Path(str(voice.get("output_file") or "")).resolve()
    duration = voice.get("duration_seconds")
    if (not final_audio.is_relative_to(source.resolve()) or not final_audio.is_file()
            or file_sha256(final_audio) != voice.get("sha256")
            or not isinstance(duration, (int, float)) or not math.isfinite(float(duration)) or duration <= 0):
        raise ValueError("Normalized final narration is missing, changed or has an invalid duration")
    with wave.open(str(final_audio), "rb") as recording:
        pcm_duration = recording.getnframes() / recording.getframerate()
        if recording.getcomptype() != "NONE" or abs(pcm_duration - float(duration)) > .03:
            raise ValueError("Final narration is not a PCM recording on the verified CTC timeline")

    timings = voice.get("timestamps") or []
    words = voice.get("word_timestamps") or []
    if alignment.get("timestamps") != timings or alignment.get("word_timestamps") != words:
        raise ValueError("Final voice timings differ from the completed CTC alignment")
    if [item.get("segment_id") for item in timings] != [item["id"] for item in segments]:
        raise ValueError("CTC segment timing does not cover the complete final script in order")
    cursor = 0.0
    expected_words = []
    for segment, timing in zip(segments, timings, strict=True):
        if timing.get("text") != segment["text"]:
            raise ValueError("CTC segment text does not exactly match the final script")
        start, end = timing.get("start_sec"), timing.get("end_sec")
        if (not isinstance(start, (int, float)) or not isinstance(end, (int, float))
                or not math.isfinite(float(start)) or not math.isfinite(float(end))
                or not cursor <= float(start) < float(end) <= float(duration)):
            raise ValueError("CTC segment intervals are invalid or outside the final narration")
        cursor = float(end)
        expected_words.extend((segment["id"], word) for word in segment["text"].split())
    if len(words) != len(expected_words):
        raise ValueError("CTC word timings do not cover the complete final script")
    for row, (segment_id, text) in zip(words, expected_words, strict=True):
        start, end = row.get("start_sec"), row.get("end_sec")
        if (row.get("segment_id") != segment_id or row.get("text") != text
                or not isinstance(start, (int, float)) or not isinstance(end, (int, float))
                or not math.isfinite(float(start)) or not math.isfinite(float(end))
                or not 0 <= float(start) < float(end) <= float(duration)):
            raise ValueError("CTC word timings are invalid or do not match the final script")

    return {"mode": "local_single_pass", "lane": lane.as_dict(), "style": style,
            "voice": voice, "voice_path": final_audio, "voice_sha256": str(voice["sha256"]),
            "source_start": 0.0, "qa_sha256": file_sha256(qa_path),
            "alignment_sha256": file_sha256(alignment_path), "script_hash": script_hash}


def _extract_pcm_interval(source: Path, output: Path, start_sec: float, end_sec: float) -> tuple[float, float, float]:
    """Copy one contiguous PCM range; preserve its native sample rate and all internal pauses."""
    with wave.open(str(source), "rb") as recording:
        if recording.getcomptype() != "NONE":
            raise ValueError("Preview extraction requires uncompressed PCM narration")
        params = recording.getparams()
        rate, total_frames = recording.getframerate(), recording.getnframes()
        first = round(start_sec * rate)
        last = round(end_sec * rate)
        if first < 0 or last <= first or last > total_frames:
            raise ValueError("Requested local narration preview range is outside the PCM source")
        recording.setpos(first)
        frames = recording.readframes(last - first)
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), "wb") as excerpt:
        excerpt.setparams(params)
        excerpt.writeframes(frames)
    actual_start, actual_end = first / rate, last / rate
    return actual_start, actual_end, (last - first) / rate


def preview_plan(source: Path, selected_ids: list[str], *, settings=None):
    settings = settings or get_settings()
    order = _source_order(source, settings)
    request = order['request']
    script=json.loads((source/'script.json').read_text(encoding='utf-8'))
    local = _local_voice_snapshot(source, order, script, settings)
    if local:
        parts={"_local_single_pass": local}
        timings={row["segment_id"]:row for row in local["voice"]["timestamps"]}
    else:
        parts={s['id']:json.loads((source/'audio'/s['id']/'voice.json').read_text(encoding='utf-8')) for s in script['segments']}
        timings={}
    cursor=0.
    beats=[]
    for s in script['segments']:
        if local:
            timing=timings[s['id']]
            start,end=float(timing['start_sec']),float(timing['end_sec'])
        else:
            duration=parts[s['id']]['duration_seconds']
            start,end=cursor,cursor+duration
        beats.append(NarrationSegment(id=s['id'],text=s['text'],on_screen=s.get('on_screen',''),
            visual_intent=s.get('visual_intent','') if local else '',start=start,end=end))
        cursor=end
    design_system={'id':'wealth_blueprint_style_v1','visual_profile_id':'wealth_blueprint_visual_v1'}
    planning_profile={'channel':'wealth_blueprint','topic':request['topic'],'direction':request.get('direction',''),
        'optional_notes':request.get('optional_notes','')}
    planner_words=[]
    if local:
        design_system['visual_style_id']=local['style']['id']
        planning_profile['disable_visual_story']=True
        planner_words=[NarrationWord(word=str(row['text']),start=float(row['start_sec']),end=float(row['end_sec']))
                       for row in local['voice']['word_timestamps']]
    full=WealthBlueprintScenePlanner(settings).plan(ScenePlannerInput(script=' '.join(s['text'] for s in script['segments']),segments=beats,
        words=planner_words,profile=planning_profile,
        design_system=design_system,
        available_asset_types=['character','icon','charts','background'],
        claims=script.get('claims') or [],
        segment_claim_map={s['id']:s.get('claim_ids') or [] for s in script['segments']}))
    scenes=[s.model_dump(mode='json') for s in full.scenes if any(i in selected_ids for i in s.narration_segment_ids)]
    actual=[i for s in scenes for i in s['narration_segment_ids']]
    if actual!=selected_ids or not 1<=len(scenes)<=3: raise ValueError('Preview must contain one to three complete consecutive scenes')
    indexes=[next(i for i,s in enumerate(script['segments']) if s['id']==sid) for sid in selected_ids]
    if indexes != list(range(indexes[0], indexes[0]+len(indexes))): raise ValueError('Preview scenes must be consecutive')
    start=float(timings[selected_ids[0]]['start_sec']) if local else scenes[0]['start_sec']
    end=float(timings[selected_ids[-1]]['end_sec']) if local else scenes[-1]['end_sec']
    if local:
        if (abs(float(scenes[0]['start_sec'])-start) > .03
                or abs(float(scenes[-1]['end_sec'])-end) > .03
                or end <= start):
            raise ValueError('The selected scenes do not preserve the verified CTC source timeline')
    for scene in scenes:
        scene['start_sec']-=start;scene['end_sec']-=start
        for cue in [*scene.get('animations',[]),*scene.get('sound_events',[])]:
            cue['at_sec']-=start
    # Retain the original scene positions: a middle statement must not become a CTA
    # merely because it happens to be the last shot of this short review.
    positions={s.scene_id:i for i,s in enumerate(full.scenes)}
    for scene in scenes:
        scene['source_scene_index']=positions[scene['scene_id']]
        scene['source_scene_count']=len(full.scenes)
    return script,parts,{'scenes':scenes},start

def render_review_fragment(source: Path, output: Path, *, selected_ids=None, illustration_pack_path=None, settings=None):
    selected_ids=selected_ids or ['s03','s04','s05']
    source,output=source.resolve(),output.resolve()
    if source.drive.upper()!='E:' or output.drive.upper()!='E:':raise ValueError('Atnos previews must stay on E:')
    if output==source or output.is_relative_to(source):raise ValueError('Review output must not modify the original order')
    settings = settings or get_settings()
    script,parts,plan,source_start=preview_plan(source,selected_ids,settings=settings)
    output.mkdir(parents=True,exist_ok=True);(output/'audio').mkdir(exist_ok=True)
    from atnos.production.channels.wealth_blueprint.visual_decision import storyboard_blocks_render
    blocked=storyboard_blocks_render(plan)
    if blocked:
        (output/'preview-blocked.json').write_text(json.dumps({'reason':blocked,'plan':plan,
            'user_approved':False,'publication_allowed':False},indent=2),encoding='utf-8')
        raise ValueError(blocked)
    context=SimpleNamespace(artifact_dir=output)
    final_audio=output/'audio'/'narration_preview.wav'
    local=parts.get('_local_single_pass')
    if local:
        source_rows={row['segment_id']:row for row in local['voice']['timestamps']}
        source_start=float(source_rows[selected_ids[0]]['start_sec'])
        source_end=float(source_rows[selected_ids[-1]]['end_sec'])
        script_ids=[row['id'] for row in script['segments']]
        first_index=script_ids.index(selected_ids[0])
        # Keep the inter-sentence breath before the selected first line. The
        # preview begins at the previous segment's CTC speech end, not at a
        # word onset; this is still one contiguous source PCM interval.
        audio_source_start=(float(source_rows[script_ids[first_index-1]]['speech_end_sec'])
                            if first_index else 0.0)
        actual_start,actual_end,duration=_extract_pcm_interval(local['voice_path'],final_audio,audio_source_start,source_end)
        if duration>30:raise ValueError('Review fragment exceeds thirty seconds')
        shift=source_start-actual_start
        for scene in plan['scenes']:
            scene['start_sec']+=shift
            scene['end_sec']+=shift
            for cue in [*scene.get('animations',[]),*scene.get('sound_events',[])]:
                cue['at_sec']+=shift
        # Keep the planner's scene boundaries on the exact sample interval used
        # for the excerpt. The source audio remains an untouched contiguous crop.
        plan['scenes'][0]['start_sec']=0.0
        plan['scenes'][-1]['end_sec']=duration
        words=[{**row,'start_sec':float(row['start_sec'])-actual_start,
                'end_sec':float(row['end_sec'])-actual_start}
               for row in local['voice']['word_timestamps'] if row['segment_id'] in selected_ids]
        timestamps=[]
        for sid in selected_ids:
            row=source_rows[sid]
            rebased_start=max(0.0,float(row['start_sec'])-actual_start)
            rebased_end=min(duration,float(row['end_sec'])-actual_start)
            timestamps.append({**row,'start_sec':rebased_start,'end_sec':rebased_end,
                               'duration_sec':rebased_end-rebased_start,
                               'speech_end_sec':min(duration,float(row['speech_end_sec'])-actual_start)})
        voice={'output_file':str(final_audio),'timestamps':timestamps,'word_timestamps':words,
               'duration_seconds':duration,'timing_source':'local_ctc_reference_alignment'}
    else:
        for sid in selected_ids:
            part=parts[sid]
            if file_sha256(Path(part['output_file']))!=part['sha256']:raise ValueError('Recorded speech changed after synthesis')
            qa=json.loads((source/'audio'/sid/'recognized.json').read_text(encoding='utf-8'))
            if qa.get('status')!='passed':raise ValueError('Preview requires independently checked speech')
        raw,timestamps,words,duration=assemble_audio(context,[parts[s] for s in selected_ids])
        if duration>30:raise ValueError('Review fragment exceeds thirty seconds')
        normalization=asyncio.run(_normalize_audio(raw,final_audio))
        if normalization.get('status')!='normalized':raise ValueError('Preview audio normalization failed')
        voice={'output_file':str(final_audio),'timestamps':timestamps,'word_timestamps':words,'duration_seconds':duration}
    from atnos.production.channels.wealth_blueprint.scene_production_assets import resolve_scene_production_assets
    from atnos.production.channels.wealth_blueprint.geometric_assets import is_geometric_placeholder_asset, looks_like_geometric_raster
    asset_scope=output/source.name if local else output
    asset_scope.mkdir(parents=True,exist_ok=True)
    asset_plan=resolve_scene_production_assets(plan=plan,settings=settings,artifact_dir=asset_scope,allow_generate=False,
        visual_style=local['style'] if local else None)
    (output/'scene_production_assets.json').write_text(json.dumps(asset_plan.as_dict(),indent=2),encoding='utf-8')
    if asset_plan.status=='waiting':
        raise ValueError('WAITING_FOR_ASSETS: '+json.dumps(asset_plan.waiting))
    for row in asset_plan.assignments.values():
        if is_geometric_placeholder_asset({'id':row.asset_id,'file_path':row.source_path},resolve_path=Path(row.source_path)) or looks_like_geometric_raster(Path(row.source_path)):
            raise ValueError('geometric_placeholder_blocked_in_review: '+row.asset_id)
    manifest=scene_plan_to_project_manifest(job_id=source.name+'-review',topic=script.get('title','Wealth Blueprint'),
        script=' '.join(s['text'] for s in script['segments'] if s['id'] in selected_ids),plan=plan,
        duration_hint=duration,voice=voice,require_caption_words=True,candidate_preview=True,illustration_pack_path=illustration_pack_path,
        scene_images={sid:row.path for sid,row in asset_plan.assignments.items()},
        scene_asset_meta=asset_plan.as_dict()['assignments'],visual_style=local['style'] if local else None)
    manifest_path=output/'project_manifest.json'
    save_project_manifest(manifest_path,manifest)
    (output/'scene_plan.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
    (output/'audio_timestamps.json').write_text(json.dumps(voice,indent=2),encoding='utf-8')
    silent=output/'preview_silent.mp4'
    render=render_project_manifest(manifest_path,silent)
    if not render.get('ok'):raise ValueError(str(render))
    final=output/'WB-review.mp4'
    mux=_mux_remotion_video(raw_video_path=silent,audio_path=final_audio,output_path=final,target_lufs=-14.)
    if not mux.get('ok'):raise ValueError(str(mux))
    result={'purpose':'candidate_preview','publication_allowed':False,'user_approved':False,
        'source_order':str(source),'source_script_sha256':file_sha256(source/'script.json'),
        'selected_segments':selected_ids,'source_start_seconds':source_start,'duration_seconds':duration,
        'output_file':str(final),'sha256':file_sha256(final),'render':render,'mux':mux,
        'full_order_qa':'not_implied_by_preview','listening_review':'not_performed','normal_speed_motion_review':'not_performed'}
    if local:
        result.update(snapshot_only=True,frozen_profile_hash=local['lane']['frozen_profile_hash'],
            local_voice_lane_digest=local['lane']['digest'],local_voice_lane_snapshot=local['lane'],
            frozen_visual_style=local['style'],visual_style_id=local['style']['id'],
            visual_style_definition_hash=local['style']['definition_hash'],source_voice_sha256=local['voice_sha256'],
            source_asr_sha256=local['qa_sha256'],source_ctc_alignment_sha256=local['alignment_sha256'],
            source_timing_source='local_ctc_reference_alignment',source_end_seconds=source_end)
        result['audio_excerpt_start_seconds']=actual_start
        result['audio_excerpt_end_seconds']=actual_end
    (output/'preview-result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


STYLE_COMPARISON_PRESETS = {
    'painted_editorial': {'name': 'Malarska pracownia', 'reference': 'Distance / craft principles',
                         'background': '#171A3B', 'text': '#FFF2D5'},
    'illustrated_explainer': {'name': 'Ilustrowany mechanizm', 'reference': 'Kurzgesagt / explanatory continuity',
                              'background': '#171A3B', 'text': '#FFF2D5'},
    'pigment_motion': {'name': 'Pigment i kolor', 'reference': 'Solimar / color and material',
                      'background': '#F4EBDD', 'text': '#23284A'},
    'editorial_magazine': {'name': 'Redakcyjny ledger', 'reference': 'The Economist / hierarchy',
                          'background': '#EFE5D1', 'text': '#152C36'},
}


def comparison_narration_matches(manifest, script):
    # The normal planner may group consecutive narration segments into one
    # scene. Preserve every spoken word in order, not the old scene grouping.
    return (' '.join(' '.join(s.narration for s in manifest.scenes).split()) ==
            ' '.join(' '.join(s['text'] for s in script['segments']).split()))


def render_style_comparison(source: Path, output: Path, *, style_id: str, settings=None):
    """Full, explicit comparison export of an intact ready local WB package.

    Reuses verified canonical narration and alignment; never accepts a blocked
    draft or writes source checkpoints. Uses this module's existing renderer
    and mux path. Appearance is a candidate, regardless of source package QA.
    """
    from atnos.production.project_manifest import load_project_manifest, QAResult
    from atnos.production.publication_coordinator import verified_package
    from atnos.production.project_profile import content_hash
    from atnos.production.steps.common import write_json
    from atnos.services.gpu_runtime_lock_service import GpuRuntimeLockService
    from atnos.production.remotion_renderer import REMOTION_RENDERER_VERSION
    if style_id not in STYLE_COMPARISON_PRESETS:
        raise ValueError('Unknown comparison style')
    source, output = source.resolve(), output.resolve()
    if (source.drive.upper() != 'E:' or output.drive.upper() != 'E:'
            or source == output or output.is_relative_to(source)):
        raise ValueError('Comparison output must be separate from the source on E:')
    settings = settings or get_settings()
    store = ProductionOrderStore(settings)
    order = _source_order(source, settings)
    if (order['status'] != 'ready' or order['worker_live']
            or order['request'].get('delivery_mode') != 'local_only'):
        raise ValueError('Style comparison requires an idle ready local WB package')
    package = verified_package(store, source.name)
    script = json.loads((source / 'script.json').read_text('utf-8'))
    local = _local_voice_snapshot(source, order, script, settings)
    if local is None:
        raise ValueError('Style comparison requires verified local narration and CTC evidence')
    manifest = load_project_manifest(source / 'project_manifest.json', verify_hash=True)
    if (not comparison_narration_matches(manifest, script)
            or any(s.scene_type.value != 'CTAScene' and (
                (s.visual_relation or {}).get('kind') != 'reserve_readiness'
                or (s.visual_relation or {}).get('stage') not in {'identify', 'separate', 'review'})
                for s in manifest.scenes)):
        raise ValueError('Comparison requires unchanged narration and supported qualitative purpose scenes')
    source_hashes = {name: file_sha256(source / name) for name in (
        'script.json', 'project_manifest.json', 'audio_timestamps.json')}
    request = {'purpose': 'candidate_style_comparison', 'style_id': style_id,
        'definition': STYLE_COMPARISON_PRESETS[style_id], 'source_order': str(source),
        'source_package_hash': package['hash'], 'source_hashes': source_hashes,
        'voice_sha256': local['voice_sha256'], 'alignment_sha256': local['alignment_sha256'],
        'voice_preset': local['lane']['preset'], 'user_approved': False,
        'source_renderer_version': manifest.renderer_version, 'renderer_version': REMOTION_RENDERER_VERSION,
        'publication_allowed': False, 'new_model_calls_allowed': 0}
    output.mkdir(parents=True, exist_ok=True)
    request_path = output / 'comparison-request.json'
    if request_path.exists():
        raise ValueError('Comparison output already has a request; reconcile instead of overwriting')
    write_json(request_path, request)
    manifest = manifest.model_copy(deep=True)
    manifest.job_id = source.name + '-comparison-' + style_id
    manifest.renderer_version = REMOTION_RENDERER_VERSION
    manifest.review_mode = 'candidate_preview'
    manifest.status = 'candidate_preview'
    manifest.current_step = 'style_comparison'
    manifest.artifacts = []
    manifest.qa = QAResult(status='pending', action='WARN', reasons=['New appearance requires review'])
    manifest.brand = manifest.brand.model_copy(update={
        'comparisonStyle': style_id, 'visualVariant': None, 'captionStyle': 'plain_phrase',
        'background': STYLE_COMPARISON_PRESETS[style_id]['background'],
        'text': STYLE_COMPARISON_PRESETS[style_id]['text']})
    manifest_path = output / 'project_manifest.json'
    save_project_manifest(manifest_path, manifest)
    lock = GpuRuntimeLockService(settings)
    lease = lock.acquire(owner='style-comparison:' + source.name, timeout_s=30)
    try:
        silent = output / 'render-silent.mp4'
        rendered = render_project_manifest(manifest_path, silent)
        if not rendered.get('ok'):
            raise ValueError(str(rendered))
        final = output / ('WB-' + style_id + '.mp4')
        mux = _mux_remotion_video(raw_video_path=silent, audio_path=local['voice_path'],
            output_path=final, target_lufs=-14.)
        if not mux.get('ok'):
            raise ValueError(str(mux))
        if any(file_sha256(source / name) != digest for name, digest in source_hashes.items()):
            raise ValueError('Source changed during comparison; output cannot be accepted')
        result = {**request, 'request_hash': content_hash(request), 'output_file': str(final),
            'sha256': file_sha256(final), 'duration_seconds': manifest.scenes[-1].timing.end_sec,
            'render': rendered, 'mux': mux, 'listening_review': 'not_performed',
            'normal_speed_motion_review': 'not_performed', 'full_order_qa': 'not_implied_by_comparison',
            'source_unchanged': True}
        write_json(output / 'comparison-result.json', result)
        return result
    finally:
        lock.release(lease)


def render_voice_comparison_fragment(source: Path, output: Path, *, sample_id: str, settings=None):
    """A held-order voice/art candidate through the existing planner and Remotion renderer.

    Independent recognition precedes reference-conditioned alignment. This never
    writes an order checkpoint, promotes a voice, or authorizes a final export.
    """
    from atnos.services.qwen_tts_local_runtime_service import COMPARISON_ROOT, COMPARISON_HOLD_PATH
    from atnos.services.local_ctc_alignment_service import align
    from atnos.production.steps.voice_synthesis import _srt_script_match_score
    source, output = source.resolve(), output.resolve()
    if source.drive.upper() != 'E:' or output.drive.upper() != 'E:' or output.is_relative_to(source):
        raise ValueError('Comparison preview must use a separate E: output directory')
    settings = settings or get_settings()
    order = _source_order(source, settings)
    if order['request'].get('delivery_mode') != 'local_only':
        raise ValueError('Comparison is restricted to local_only orders')
    if sample_id not in {'ryan_clear', 'aiden_clear'}:
        raise ValueError('Unsupported unapproved comparison candidate')
    root = COMPARISON_ROOT.resolve()
    request_path, receipt_path = root/'request.json', root/'samples'/f'{sample_id}.json'
    request = json.loads(request_path.read_text(encoding='utf-8'))
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    script = json.loads((source/'script.json').read_text(encoding='utf-8'))
    selected_ids = request['segment_ids']
    selected = [s for s in script['segments'] if s['id'] in selected_ids]
    text = ' '.join(s['text'].strip() for s in selected)
    audio = root/'samples'/f'{sample_id}.wav'
    if (receipt.get('status') != 'candidate_unapproved' or receipt.get('user_approved') is not False
            or receipt.get('production_enabled') is not False or receipt.get('provider') != 'qwen_local'
            or receipt.get('source_job_id') != source.name or request.get('source_job_id') != source.name
            or receipt.get('request_sha256') != file_sha256(request_path)
            or receipt.get('source_script_sha256') != file_sha256(source/'script.json')
            or request.get('source_script_sha256') != receipt.get('source_script_sha256')
            or receipt.get('quality_hold_sha256') != file_sha256(COMPARISON_HOLD_PATH)
            or receipt.get('text') != text or request.get('text') != text
            or receipt.get('segment_ids') != selected_ids or [s['id'] for s in selected] != selected_ids
            or receipt.get('output_sha256') != file_sha256(audio)):
        raise ValueError('Changed or unbound voice comparison evidence')
    verification = json.loads((root/'technical-verification.json').read_text(encoding='utf-8'))
    qa = next((r for r in verification.get('samples', []) if r.get('sample_id') == sample_id), None)
    if (not qa or qa.get('audio_sha256') != file_sha256(audio)
            or qa.get('recognition_prompt_provided') is not False):
        raise ValueError('Independent, no-prompt recognition is required')
    srt = Path(qa['recognized_srt_path'])
    if (not srt.resolve().is_relative_to(root) or file_sha256(srt) != qa['recognized_srt_sha256']
            or not _srt_script_match_score(srt.read_text(encoding='utf-8'), [{'text':text}])['accepted']):
        raise ValueError('Independent recognition does not match the exact candidate text')
    _, _, plan, _ = preview_plan(source, selected_ids, settings=settings)
    from atnos.production.channels.wealth_blueprint.visual_decision import storyboard_blocks_render
    blocked = storyboard_blocks_render(plan)
    if blocked:
        raise ValueError(blocked)
    for scene in plan['scenes']:
        relation = (scene.get('visual_intent') or {}).get('relation') or {}
        if (relation.get('kind') != 'reserve_readiness'
                or relation.get('stage') not in {'identify', 'separate', 'review'}
                or any(o.get('label') == 'PLANNED BILL FUND' for o in relation.get('objects', []))):
            raise ValueError('Unsupported planning-desk context; no illustration fallback')
    if output.exists():
        raise ValueError('Comparison preview output exists; reconcile before another operation')
    output.mkdir(parents=True)
    # This normal aligner accepts the ordinary E: production/<project>/<review-id>
    # artifact directory. It is an explicitly unregistered review, not an order.
    alignment_path = output/'audio'/'comparison-alignment.json'
    alignment = asyncio.run(align(audio, text, alignment_path))
    if alignment.get('provenance', {}).get('audio_sha256') != file_sha256(audio):
        raise ValueError('Alignment is not bound to the current comparison WAV')
    words = alignment['word_timestamps']
    with wave.open(str(audio), 'rb') as recording:
        duration = recording.getnframes()/recording.getframerate()
    if duration > 30 or [w['text'] for w in words] != text.split():
        raise ValueError('Comparison requires a complete aligned clip of at most thirty seconds')
    if len(plan['scenes']) != len(selected):
        raise ValueError('Comparison requires complete, individually represented source scenes')
    annotated, timestamps, cursor = [], [], 0
    for index, (segment, scene) in enumerate(zip(selected, plan['scenes'], strict=True)):
        count = len(segment['text'].split())
        segment_words = words[cursor:cursor+count]
        start = 0.0 if index == 0 else float(segment_words[0]['start_sec'])
        end = duration if index == len(selected)-1 else float(words[cursor+count]['start_sec'])
        old_start, old_end = scene['start_sec'], scene['end_sec']
        for cue in [*scene.get('animations', []), *scene.get('sound_events', [])]:
            fraction = (cue['at_sec']-old_start)/max(.001,old_end-old_start)
            cue['at_sec'] = start+fraction*(end-start)
        scene['start_sec'], scene['end_sec'] = start, end
        timestamps.append({'segment_id':segment['id'],'text':segment['text'],'start_sec':start,'end_sec':end})
        annotated.extend({**word, 'segment_id':segment['id']} for word in segment_words)
        cursor += count
    voice = {'output_file':str(audio),'duration_seconds':duration,'timestamps':timestamps,
             'word_timestamps':annotated,'timing_source':'local_ctc_reference_alignment'}
    style = order['profile']['visual_style']
    manifest = scene_plan_to_project_manifest(job_id=source.name+'-comparison-review',
        topic=script.get('title','Wealth Blueprint'),script=text,plan=plan,duration_hint=duration,
        voice=voice,require_caption_words=True,candidate_preview=True,visual_style=style)
    manifest.brand = manifest.brand.model_copy(update={'presentation':'educational_illustration',
                                                       'illustrationSystem':'wb_planning_desk_painted_v1'})
    manifest_path = output/'project_manifest.json'
    save_project_manifest(manifest_path, manifest)
    (output/'scene_plan.json').write_text(json.dumps(plan,indent=2)+'\n',encoding='utf-8')
    (output/'audio_timestamps.json').write_text(json.dumps(voice,indent=2)+'\n',encoding='utf-8')
    silent, final = output/'preview_silent.mp4', output/'WB-comparison-review.mp4'
    rendered = render_project_manifest(manifest_path,silent)
    if not rendered.get('ok'): raise ValueError(str(rendered))
    (output/'remotion_result.json').write_text(json.dumps(rendered,indent=2)+'\n',encoding='utf-8')
    mux = _mux_remotion_video(raw_video_path=silent,audio_path=audio,output_path=final,target_lufs=-14.)
    if not mux.get('ok'): raise ValueError(str(mux))
    result = {'purpose':'candidate_preview','user_approved':False,'publication_allowed':False,
              'source_order':str(source),'source_script_sha256':file_sha256(source/'script.json'),
              'voice_candidate_sample_id':sample_id,'source_audio_sha256':file_sha256(audio),
              'asr_sha256':file_sha256(srt),'alignment_sha256':file_sha256(alignment_path),
              'selected_segments':selected_ids,'duration_seconds':duration,'output_file':str(final),
              'sha256':file_sha256(final),'render':rendered,'mux':mux,
              'illustration_system':'wb_planning_desk_painted_v1','default_voice_changed':False,
              'order_mutated':False,'full_order_qa':'not_implied_by_preview',
              'listening_review':'not_performed','normal_speed_motion_review':'not_performed'}
    (output/'preview-result.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    return result
