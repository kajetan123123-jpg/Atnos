"""Registered local voice corrections and bounded pacing in the existing pipeline."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from atnos.intelligence.storage import connect, json_dumps, utc_now_iso
from atnos.production.models import StepResult
from atnos.production.order_execution import StageExecutor
from atnos.production.order_store import ProductionOrderStore, ProductionBlocked, OrderConflict, _event, file_hash
from atnos.production.steps.common import write_json
from atnos.production.steps.order_voice import finalize_voice
from atnos.production.project_profile import content_hash


VOICE_REVISION_STAGES = frozenset({
    'voice/local_ryan/narration', 'voice/local_ryan_qa/narration', 'voice/local_ryan/alignment',
    'voice', 'captions',
})


class CurrentScriptReviewBinding(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    stage: str = Field(pattern=r'^remotion_script_review/[0-9a-f]{12}/v[0-9]+-p3$')
    input_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    output_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


class LocalVoiceRevision(BaseModel):
    """A selected candidate and one bounded full-take correction, not quality approval."""
    model_config = ConfigDict(extra='forbid', frozen=True)
    expected_canonical_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    expected_original_lane_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    candidate_receipt_path: Path
    candidate_receipt_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    audition_path: Path
    audition_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    selection_path: Path
    selection_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    feedback_id: str = Field(min_length=1)
    feedback_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    current_script_review: CurrentScriptReviewBinding | None = None
    max_expensive_stage_attempts: Literal[1] = 1
    allow_script_repair: Literal[False] = False
    allow_segment_voice_recovery: Literal[False] = False
    paid_calls_authorized: Literal[False] = False
    final_film_approved: Literal[False] = False
    channel_default_changed: Literal[False] = False


def _revision_json(path, digest):
    path = Path(path).resolve()
    if path.drive.casefold() != 'e:' or not path.is_file() or file_hash(path) != digest:
        raise ProductionBlocked('Local voice revision evidence changed or is outside E:')
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise ProductionBlocked('Local voice revision evidence must be a JSON object')
    return value


def prepare_local_voice_revision(store, job_id, *, candidate_receipt_path, audition_path, selection_path, feedback_id):
    """Read and validate a prospective binding; no registration or media execution."""
    from atnos.production.local_voice_lane import _load_base_local_voice_lane
    from atnos.production.order_feedback import ProductionFeedbackStore
    from atnos.production.publication_coordinator import verified_package
    from atnos.production.scoped_script_revision import verified_current_renderer_review
    from atnos.production.steps.order_script import SCRIPT_VALIDATOR_VERSION

    state = verified_package(store, job_id)['state']
    if state['worker_live'] or state['status'] != 'ready':
        raise ProductionBlocked('Prepare a local voice correction only against an idle verified package')
    base = _load_base_local_voice_lane(store.settings, job_id, read_only_snapshot=True)
    if base is None:
        raise ProductionBlocked('The source order has no registered local voice')
    feedback = next((r for r in ProductionFeedbackStore(store.settings).list(job_id) if r['id'] == feedback_id), None)
    if feedback is None:
        raise ProductionBlocked('Selected voice feedback is missing')
    paths = [Path(p).resolve() for p in (candidate_receipt_path, audition_path, selection_path)]
    if any(p.drive.casefold() != 'e:' or not p.is_file() for p in paths):
        raise ProductionBlocked('Local voice revision evidence must exist on E:')
    script = next(r for r in state['checkpoints'] if r['stage'] == 'script')
    review_binding = None
    if script['output'].get('qa', {}).get('review_contract_version') != SCRIPT_VALIDATOR_VERSION:
        review_binding, _ = verified_current_renderer_review(store, state)
    binding = LocalVoiceRevision(
        expected_canonical_sha256=file_hash(Path(state['artifact_dir'])/'script.json'),
        expected_original_lane_digest=base.digest, candidate_receipt_path=paths[0], candidate_receipt_sha256=file_hash(paths[0]),
        audition_path=paths[1], audition_sha256=file_hash(paths[1]), selection_path=paths[2], selection_sha256=file_hash(paths[2]),
        feedback_id=feedback_id, feedback_sha256=content_hash(feedback), current_script_review=review_binding)
    validate_local_voice_revision(store, state, binding, base)
    return binding


def validate_local_voice_revision(store, state, binding, base_lane):
    """Recheck actual candidate, mastering and current user feedback, without inference."""
    from atnos.production.local_voice_lane import NEUTRAL_PRESET, NEUTRAL_INSTRUCTION, MODEL_ID, MODEL_REVISION
    from atnos.production.order_feedback import ProductionFeedbackStore
    from atnos.services.qwen_tts_local_runtime_service import audition_scope_sha256

    binding = LocalVoiceRevision.model_validate(binding)
    root = Path(state['artifact_dir']).resolve()
    from atnos.production.steps.order_script import SCRIPT_VALIDATOR_VERSION
    script = next((r for r in state['checkpoints'] if r['stage'] == 'script'), {})
    if (state['request'].get('project') != 'wealth_blueprint'
            or state['request'].get('delivery_mode') != 'local_only'
            or state['profile']['production']['renderer'] != 'remotion'
            or root.drive.casefold() != 'e:' or not root.is_relative_to((store.settings.data_dir / 'production').resolve())
            or not (root / 'script.json').is_file()
            or file_hash(root / 'script.json') != binding.expected_canonical_sha256
            or script.get('status') != 'done' or not store._valid_files(root, script.get('files_json', '[]'))
            or base_lane is None or base_lane.digest != binding.expected_original_lane_digest
            or base_lane.preset == NEUTRAL_PRESET):
        raise ProductionBlocked('Local voice revision requires the unchanged local WB script and original lane')
    if binding.current_script_review is not None:
        from atnos.production.scoped_script_revision import verified_current_renderer_review
        verified_current_renderer_review(store, state, proof=binding.current_script_review.model_dump())
    elif script.get('output', {}).get('qa', {}).get('review_contract_version') != SCRIPT_VALIDATOR_VERSION:
        raise ProductionBlocked('Local voice revision requires a bound current script review')
    sample = _revision_json(binding.candidate_receipt_path, binding.candidate_receipt_sha256)
    audition = _revision_json(binding.audition_path, binding.audition_sha256)
    selection = _revision_json(binding.selection_path, binding.selection_sha256)
    feedback = next((r for r in ProductionFeedbackStore(store.settings).list(state['id'])
                     if r['id'] == binding.feedback_id), None)
    if (feedback is None or not feedback.get('active') or feedback.get('category') != 'voice'
            or feedback.get('source_job_id') != state['id'] or content_hash(feedback) != binding.feedback_sha256):
        raise ProductionBlocked('Local voice revision requires its current user selection feedback')
    request = _revision_json(sample.get('request_path', ''), sample.get('request_sha256'))
    canonical = json.loads((root/'script.json').read_text(encoding='utf-8'))
    ids = request.get('segment_ids')
    if (not isinstance(ids, list) or not ids or any(not isinstance(k, str) for k in ids)
            or len(set(ids)) != len(ids)):
        raise ProductionBlocked('Selected audition lacks ordered canonical segment IDs')
    selected = [s for s in canonical['segments'] if s['id'] in ids]
    if ([s['id'] for s in selected] != ids or '\n\n'.join(s['text'].strip() for s in selected) != request.get('text')):
        raise ProductionBlocked('Selected audition text differs from its canonical source')
    authorization = request.get('execution_authorization') or {}
    instruction_sha = hashlib.sha256(NEUTRAL_INSTRUCTION.encode('utf-8')).hexdigest()
    if (sample.get('schema_version') != 2 or sample.get('status') != 'candidate_unapproved'
            or sample.get('user_approved') is not False or sample.get('production_enabled') is not False
            or sample.get('source_job_id') != state['id']
            or sample.get('source_script_sha256') != binding.expected_canonical_sha256
            or sample.get('model_id') != MODEL_ID or sample.get('revision') != MODEL_REVISION
            or sample.get('device') != 'cpu' or sample.get('provider') != 'qwen_local'
            or sample.get('speaker') != 'Ryan' or sample.get('preset') != NEUTRAL_PRESET
            or sample.get('instruction') != NEUTRAL_INSTRUCTION or sample.get('instruction_sha256') != instruction_sha
            or sample.get('provider_calls') != 1 or sample.get('cost_usd') != 0
            or request.get('source_job_id') != state['id']
            or request.get('source_script_sha256') != binding.expected_canonical_sha256
            or request.get('schema_version') != 2 or request.get('scope') != 'voice_audition_only'
            or request.get('model_id') != MODEL_ID or request.get('model_revision') != MODEL_REVISION
            or request.get('user_approved') is not False or request.get('production_enabled') is not False
            or request.get('max_new_synthesis_requests') != 1
            or request.get('preserve_current_mastering') is not True
            or authorization.get('status') != 'authorized' or not authorization.get('user_response')
            or authorization.get('scope_sha256') != audition_scope_sha256(request)
            or audition.get('source_job_id') != state['id']
            or audition.get('source_script_sha256') != binding.expected_canonical_sha256
            or audition.get('request_sha256') != sample.get('request_sha256')
            or audition.get('raw_sha256') != sample.get('output_sha256')
            or audition.get('text') != sample.get('text') or request.get('text') != sample.get('text')
            or audition.get('user_approved') is not False or audition.get('production_enabled') is not False
            or audition.get('time_stretch') is not False
            or audition.get('normalization', {}).get('loudness_target_applied') is not True
            or selection.get('status') != 'USER_SELECTED_FOR_FURTHER_REVIEW'
            or selection.get('source_job_id') != state['id'] or selection.get('feedback_id') != binding.feedback_id
            or selection.get('preset') != NEUTRAL_PRESET
            or selection.get('quality_approved') is not False or selection.get('channel_default_changed') is not False
            or selection.get('selected_sample_sha256') != audition.get('sha256')
            or not isinstance(audition.get('sha256'), str) or audition['sha256'] not in feedback.get('note', '')):
        raise ProductionBlocked('Local voice revision lacks genuine matching candidate and user selection provenance')
    for path, digest in ((sample.get('output_path', ''), sample.get('output_sha256')),
                         (audition.get('output_file', ''), audition.get('sha256'))):
        path = Path(path).resolve()
        if path.drive.casefold() != 'e:' or not path.is_file() or file_hash(path) != digest:
            raise ProductionBlocked('Selected local voice WAV changed')
    if Path(selection.get('selected_sample_path', '')).resolve() != Path(audition['output_file']).resolve():
        raise ProductionBlocked('User selection points at a different mastered sample')
    if (Path(audition.get('raw_receipt_path', '')).resolve() != binding.candidate_receipt_path.resolve()
            or Path(selection.get('selected_raw_receipt', '')).resolve() != binding.candidate_receipt_path.resolve()):
        raise ProductionBlocked('Audition and user selection point at a different runtime receipt')
    return {'sample_sha256': audition['sha256'], 'preset': NEUTRAL_PRESET,
            'instruction_sha256': instruction_sha}


def effective_revision_lane(settings, job_id, base_lane):
    """Resolve a registered correction without replacing the immutable original lane."""
    from atnos.production.local_voice_lane import LocalVoiceLane
    from atnos.production.remotion_quality_repair import _verified_refresh_row

    store = ProductionOrderStore(settings)
    state = store.get(job_id)
    rows = [r for r in state.get('checkpoints', [])
            if r['stage'].startswith('remotion_refresh/') and r['status'] == 'done'
            and r['output'].get('local_voice_revision') is not None]
    if not rows:
        return base_lane
    if len(rows) != 1:
        raise ProductionBlocked('Multiple local voice revisions require explicit reconciliation')
    row = rows[0]
    payload = _verified_refresh_row(store, Path(state['artifact_dir']), row, state)
    selected = validate_local_voice_revision(store, state, payload['local_voice_revision'], base_lane)
    body = {k: v for k, v in base_lane.as_dict().items() if k != 'digest'}
    body.update(selected, approval_sha256=file_hash(Path(state['artifact_dir']) / 'renderer_refresh' / payload['digest'][:12] / 'request.json'))
    return LocalVoiceLane(**body, digest=content_hash(body))


def _guard_voice_attempt(refresh, row, stage, inputs, *, cached, local_model_call=False,
                         provider_call=False, external_tool_call=False, estimated_cost_usd=0):
    """Pure scope check shared by the normal executor and its atomic store boundary."""
    if provider_call or external_tool_call or estimated_cost_usd:
        raise ProductionBlocked('Local voice correction prohibits paid or external relays')
    if cached:
        return
    original = next((r for r in refresh['before_checkpoints'] if r['stage'] == stage), None)
    script = next(r['output'] for r in refresh['before_checkpoints'] if r['stage'] == 'script')
    semantic = {'semantic/' + s['id'] for s in script['segments']} | {
        'semantic/cover_portrait', 'semantic/cover_landscape'}
    allowed = VOICE_REVISION_STAGES | semantic | {'remotion_render', 'remotion_mux', 'covers',
                                                'media_qa', 'semantic_qa', 'package'}
    expensive = semantic | {'voice/local_ryan/narration', 'voice/local_ryan_qa/narration',
                            'voice/local_ryan/alignment', 'remotion_render'}
    archive = (re.fullmatch(r'semantic_history/([a-zA-Z0-9_-]+)/[0-9a-f]{12}', stage)
               and not local_model_call and inputs.get('previous_digest')
               and stage.endswith('/' + str(inputs['previous_digest'])[:12]))
    if stage not in allowed and not archive:
        raise ProductionBlocked('Local voice correction requires intact cached upstream or an authorized media stage: ' + stage)
    if local_model_call and stage not in expensive:
        raise ProductionBlocked('Local voice correction does not authorize another model stage: ' + stage)
    if stage not in expensive:
        # Cheap local assembly/validation keeps the existing retry policy. A
        # caption or package fix must not require another TTS/render allowance.
        return
    attempts = row['attempts'] if row else 0
    before = original['attempts'] if original else 0
    if attempts - before >= 1:
        raise ProductionBlocked('Local voice correction stage budget exhausted; inspect before any new execution: ' + stage)


def guard_local_voice_revision(executor, stage, inputs, **flags):
    state = executor.store.get(executor.job_id)
    row = next((r for r in state['checkpoints'] if r['stage'] == stage), None)
    cached = (row and row['status'] == 'done' and row['input_hash'] == content_hash(inputs)
              and executor.store._valid_files(executor.context.artifact_dir, row['files_json']))
    _guard_voice_attempt(executor.context.artifacts['_renderer_refresh'], row, stage, inputs,
                         cached=cached, **flags)


def guard_durable_voice_revision(store, db, job_id, root, row, stage, inputs, **flags):
    """Run inside begin's BEGIN IMMEDIATE so concurrent callers cannot both spend a slot."""
    registered = db.execute("SELECT * FROM pipeline_stage_checkpoints WHERE job_id=? "
        "AND stage GLOB 'remotion_refresh/*' AND status='done' ORDER BY updated_at DESC,rowid DESC LIMIT 1",
        (job_id,)).fetchone()
    if registered is None:
        return
    refresh = json.loads(registered['output_json'])
    if refresh.get('local_voice_revision') is None:
        return
    if (not re.fullmatch(r'[0-9a-f]{64}', str(refresh.get('digest', '')))
            or registered['stage'] != 'remotion_refresh/' + refresh['digest'][:12]
            or refresh.get('job_id') != job_id):
        raise ProductionBlocked('Durable local voice correction registration changed')
    body = {k: v for k, v in refresh.items() if k not in {
        'digest', 'archived_files', 'before_checkpoints', 'stale_downstream_entries'}}
    request = root / 'renderer_refresh' / refresh.get('digest', '')[:12] / 'request.json'
    entries = json.loads(registered['files_json'] or '[]')
    if (refresh.get('scope') != 'local_voice_and_downstream_only'
            or content_hash(body) != registered['input_hash'] or refresh.get('digest') != registered['input_hash']
            or not request.is_file() or json.loads(request.read_text(encoding='utf-8')) != refresh
            or {'path': request.relative_to(root).as_posix(), 'sha256': file_hash(request)} not in entries):
        raise ProductionBlocked('Durable local voice correction registration changed')
    LocalVoiceRevision.model_validate(refresh['local_voice_revision'])
    # A later presentation-only correction may be registered against the newly
    # finished package. Its own registrar still verifies package, lock and lease.
    job = db.execute('SELECT status FROM pipeline_jobs WHERE id=?', (job_id,)).fetchone()
    if (job['status'] == 'ready' and re.fullmatch(r'remotion_refresh/[0-9a-f]{12}', stage)
            and not any(flags.get(k) for k in ('provider_call', 'external_tool_call', 'local_model_call', 'estimated_cost_usd'))):
        return
    previous = dict(row) if row else None
    cached = (previous and previous['status'] == 'done' and previous['input_hash'] == content_hash(inputs)
              and store._valid_files(root, previous['files_json']))
    _guard_voice_attempt(refresh, previous, stage, inputs, cached=cached, **flags)


async def revalidate_pacing(settings, job_id, pause_seconds):
    store = ProductionOrderStore(settings)
    state, context = store.get(job_id), store.context(job_id)
    if state["worker_live"] or state["status"] != "blocked" or state["current_step"] != "voice":
        raise OrderConflict("Only an idle narration blocked on assembly can be paced")
    rows = {row["stage"]: row for row in state["checkpoints"]}
    previous = rows["voice"]
    error = json.loads(previous["error_json"] or "{}")
    if previous["status"] != "failed" or "Narration timing outside profile" not in error.get("message", ""):
        raise ValueError("Pacing cannot override pronunciation, provider or unrelated failures")
    if not 0 < pause_seconds <= .4:
        raise ValueError("Use a natural pause of at most 400 milliseconds")
    if any(stage.startswith(("captions", "render", "semantic")) for stage in rows):
        raise OrderConflict("Cannot alter the timeline of already produced media")
    script = rows["script"]["output"]
    required = ["script", *[key for segment in script["segments"] for key in (f"voice/{segment['id']}",f"voice_qa/{segment['id']}")]]
    for key in required:
        row = rows.get(key,{})
        if row.get("status") != "done" or not store._valid_files(context.artifact_dir,row.get("files_json","[]")):
            raise ValueError(f"Missing intact speech evidence: {key}")
    parts = [rows[f"voice/{segment['id']}"]["output"] for segment in script["segments"]]
    checks = [rows[f"voice_qa/{segment['id']}"]["output"] for segment in script["segments"]]
    if sum(part["duration_seconds"] for part in parts) >= state["profile"]["production"]["duration_seconds"][0]:
        raise ValueError("Only a genuinely short narration benefits from added reading pauses")
    with store.lease(job_id) as token:
        executor = StageExecutor(store,job_id,token)
        executor.context.artifacts["script"] = script
        archive = context.artifact_dir / "editorial" / "voice-before-pacing.json"
        async def render():
            write_json(archive,previous)
            result = await finalize_voice(executor.context,parts,checks,pause_seconds=pause_seconds)
            return StepResult(result.artifact_name,result.artifact_path,result.payload,result.message,(*result.evidence_paths,archive))
        stage = f"voice_pacing/{round(pause_seconds*1000)}"
        payload = await executor.run(stage,{"old_input_hash":previous["input_hash"],"pause_seconds":pause_seconds,
            "part_hashes":[part["sha256"] for part in parts]},render)
        files = next(row["files_json"] for row in store.get(job_id)["checkpoints"] if row["stage"] == stage)
        with connect(settings) as db:
            db.execute("BEGIN IMMEDIATE")
            store._owner(db,job_id,token)
            now = utc_now_iso()
            db.execute("UPDATE pipeline_stage_checkpoints SET status='done',output_json=?,files_json=?,error_json=NULL,updated_at=? WHERE job_id=? AND stage='voice' AND status='failed' AND input_hash=?",
                       (json_dumps(payload),files,now,job_id,previous["input_hash"]))
            db.execute("UPDATE pipeline_job_steps SET status='done',progress=1,message=NULL,completed_at=? WHERE job_id=? AND step='voice'",(now,job_id))
            db.execute("UPDATE pipeline_jobs SET status='queued',current_step='voice',error=NULL,updated_at=? WHERE id=?",(now,job_id))
            _event(db,job_id,"voice","pacing_revalidated",{"pause_seconds":pause_seconds,"duration_seconds":payload["duration_seconds"],
                "original_attempts":previous["attempts"],"archive_sha256":file_hash(archive),"paid_calls_added":0,"time_stretch":False})
            db.commit()
    return {"job_id":job_id,"status":"passed","duration_seconds":payload["duration_seconds"],"paid_calls_added":0}
