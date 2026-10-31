"""Narrow, job-scoped authorization for the approved local Ryan pilot voice."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from atnos.production.order_store import ProductionOrderStore, file_hash
from atnos.production.project_profile import content_hash


APPROVAL_PATH = Path(r"E:\Atnos\App\reports\wb-10-dollar-pilot-20261005\local-voice\qwen\USER-REVIEW-v2.json")
SAMPLE_PATH = Path(r"E:\Atnos\App\reports\wb-10-dollar-pilot-20261005\local-voice\qwen\wb-opportunity-cost-qwen3-ryan-conversational-v2-candidate.wav")
PRESET = "ryan-conversational-v2"
NEUTRAL_PRESET = "ryan-conversational-v2-neutral"
CLEAR_PRESET = "ryan-clear-conversation-v1"
INTAKE_SAMPLE_RECEIPT = Path(r'E:\Atnos\App\reports\wb-batch-20261013\voice-comparison\samples\ryan_clear.json')
NEUTRAL_INTAKE_ROOT = Path(__file__).resolve().parents[3] / 'config/voice-candidates/ryan-neutral-v1'
LOCAL_INTAKE_CANDIDATES = frozenset({'Ryan-B', 'Ryan-neutral'})
CLEAR_INSTRUCTION = (
    "Clear, alert, conversational English. Crisp articulation, a normal steady pace, and natural "
    "sentence stress. Explain the idea to one person."
)
NEUTRAL_INSTRUCTION = (
    "Explain this idea to one person in clear, relaxed, natural English. "
    "Use warm, understated confidence, gentle emphasis on the meaning of each sentence, "
    "and brief natural pauses. Avoid a promotional, ceremonial or radio-announcer delivery."
)
INSTRUCTION = (
    "Speak in a clear, relaxed, conversational way, as if explaining an idea to one person. "
    "Keep the delivery smooth and natural, with warm, understated confidence. Give gentle emphasis "
    "to the important numbers—ten dollars, eight percent, thirty years, and one hundred one dollars—"
    "without sounding formal, ceremonial, promotional, or like a radio announcer."
)
MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
MODEL_REVISION = "0c0e3051f131929182e2c023b9537f8b1c68adfe"


def instruction_for_preset(preset: str) -> str:
    if preset == PRESET:
        return INSTRUCTION
    if preset == NEUTRAL_PRESET:
        return NEUTRAL_INSTRUCTION
    if preset == CLEAR_PRESET:
        return CLEAR_INSTRUCTION
    raise LocalVoiceLaneError("Unsupported registered local voice preset.")


class LocalVoiceLaneError(RuntimeError):
    """The current order does not satisfy the narrow local voice authorization."""


@dataclass(frozen=True, slots=True)
class LocalVoiceLane:
    job_id: str
    request_hash: str
    frozen_profile_hash: str
    sample_sha256: str
    model_id: str
    model_revision: str
    preset: str
    instruction_sha256: str
    approval_sha256: str
    digest: str
    public_authorized: bool = True
    local_only: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {key: getattr(self, key) for key in self.__dataclass_fields__}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LocalVoiceLaneError(f"Local voice authorization evidence is unavailable: {path.name}") from exc
    if not isinstance(value, dict):
        raise LocalVoiceLaneError("Local voice authorization evidence must be an object.")
    return value


def _lane_body(settings, job_id: str, approval_path: Path, sample_path: Path, *, require_idle: bool,
               read_only_snapshot: bool = False) -> dict[str, Any]:
    approval_path, sample_path = approval_path.resolve(), sample_path.resolve()
    if approval_path.drive.casefold() != "e:" or sample_path.drive.casefold() != "e:":
        raise LocalVoiceLaneError("Local voice approval and sample must remain on E:.")
    receipt = _read_json(approval_path)
    candidate = receipt.get("decision") == "production_requested_candidate_unapproved"
    selected_trial = receipt.get("decision") == "selected_comparison_candidate_for_trial"
    intake_trial = receipt.get('decision') == 'selected_at_intake_for_trial'
    expected_preset = (intake_voice_preset(receipt.get('candidate_id', 'Ryan-B')) if intake_trial
        else CLEAR_PRESET if selected_trial else NEUTRAL_PRESET if candidate else PRESET)
    if (receipt.get("job_id") != job_id
            or receipt.get("decision") not in {"approved_for_pilot_trial", "production_requested_candidate_unapproved", "selected_comparison_candidate_for_trial", 'selected_at_intake_for_trial'}
            or receipt.get("preset") != expected_preset
            or receipt.get("channel_default_changed") is not False
            or receipt.get("frozen_profile_changed") is not False
            or receipt.get("final_film_approved") is not False
            or not str(receipt.get("scope", "")).startswith("This order's trial AV only")):
        raise LocalVoiceLaneError("The user receipt does not authorize this local Ryan pilot.")
    sample_sha = file_hash(sample_path)
    if Path(str(receipt.get("sample", ""))).resolve() != sample_path:
        raise LocalVoiceLaneError("The approved voice sample path changed.")
    if receipt.get("sample_sha256") != sample_sha:
        raise LocalVoiceLaneError("The approved voice sample no longer matches its review receipt.")
    order = ProductionOrderStore(settings).get(job_id)
    request = order["request"]
    if request.get("project") != "wealth_blueprint" or request.get("delivery_mode") != "local_only":
        raise LocalVoiceLaneError("The local voice lane requires this order to remain Wealth Blueprint/local_only.")
    if require_idle:
        if order.get("status") not in {"queued", "blocked"} or order.get("worker_live"):
            raise LocalVoiceLaneError("A local voice lane may only be registered for a stopped, idle order.")
        completed_prefixes = ("voice", "edit", "remotion", "video", "assembly", "render", "package", "upload")
        completed_stages = {"thumbnail", "qa"}
        if any(row.get("status") == "done" and
               (row.get("stage") in completed_stages or
                str(row.get("stage", "")).casefold().startswith(completed_prefixes))
               for row in order.get("checkpoints", [])):
            raise LocalVoiceLaneError("A local voice lane cannot be registered after voice or finished media was completed.")
    if (not require_idle and order.get("status") in {"ready", "script_ready", "cancelled"}
            and not (read_only_snapshot is True and order.get("status") == "ready")):
        raise LocalVoiceLaneError("The registered local voice lane cannot be used by a completed or closed order.")
    request_hash = str(order.get("request_hash") or content_hash(request))
    frozen_profile_hash = str(order.get("profile_hash") or content_hash(order["profile"]))
    if candidate:
        # The production request permits a candidate, never a new user quality
        # approval. Bind the real request and prior sample provenance immutably.
        authority_path = Path(str(receipt.get("production_request_path") or "")).resolve()
        prior_path = Path(str(receipt.get("prior_sample_receipt_path") or "")).resolve()
        if authority_path.drive.casefold() != "e:" or prior_path.drive.casefold() != "e:":
            raise LocalVoiceLaneError("Candidate authorization provenance must remain on E:.")
        authority, prior = _read_json(authority_path), _read_json(prior_path)
        if (receipt.get("request_hash") != content_hash(request)
                or receipt.get("production_request_sha256") != file_hash(authority_path)
                or receipt.get("prior_sample_receipt_sha256") != file_hash(prior_path)
                or authority.get("status") != "USER_REQUESTED_LOCAL_WB_BATCH"
                or authority.get("series_key") != request.get("series_key")
                or not str(authority.get("user_request") or "").strip()
                or authority.get("paid_calls_authorized") is not False
                or prior.get("decision") != "approved_for_pilot_trial"
                or prior.get("preset") != PRESET
                or prior.get("sample_sha256") != sample_sha
                or Path(str(prior.get("sample") or "")).resolve() != sample_path
                or receipt.get("new_sample_approved_by_user") is not False
                or receipt.get("channel_default_changed") is not False
                or "user_answer" in receipt):
            raise LocalVoiceLaneError("Candidate lane lacks truthful current production and prior sample provenance.")
    if selected_trial:
        _verify_selected_comparison_trial(receipt, request, sample_path, sample_sha)
    if intake_trial:
        candidate_id = request.get('voice_candidate')
        selected = intake_voice_sample() if candidate_id == 'Ryan-B' else intake_voice_sample(candidate_id)
        if (candidate_id not in LOCAL_INTAKE_CANDIDATES or request.get('provider_policy') != 'strict_local'
                or receipt.get('candidate_id', 'Ryan-B') != candidate_id
                or receipt.get('request_hash') != content_hash(request)
                or receipt.get('sample_receipt_sha256') != selected['receipt_sha256']
                or receipt.get('quality_approved_by_user') is not False
                or sample_path != Path(selected['sample_path']).resolve()
                or sample_sha != selected['sample_sha256']):
            raise LocalVoiceLaneError('Intake selection does not match this request and pinned candidate; quality approval was not granted.')
    preset = expected_preset
    model_provenance = _read_json(Path(r"E:\Atnos\Tools\Qwen3-TTS-runtime\models\Qwen3-TTS-12Hz-1.7B-CustomVoice\model-provenance.json"))
    if (model_provenance.get("model_id") != MODEL_ID or model_provenance.get("revision") != MODEL_REVISION
            or model_provenance.get("license") != "apache-2.0" or model_provenance.get("verified_files") is not True):
        raise LocalVoiceLaneError("The pinned Qwen model provenance is invalid.")
    return {"job_id": job_id, "request_hash": request_hash, "frozen_profile_hash": frozen_profile_hash,
            "sample_sha256": sample_sha, "model_id": MODEL_ID, "model_revision": MODEL_REVISION,
            "preset": preset, "instruction_sha256": hashlib.sha256(instruction_for_preset(preset).encode("utf-8")).hexdigest(),
            "approval_sha256": file_hash(approval_path), "public_authorized": True, "local_only": True}


def _verify_selected_comparison_trial(receipt, request, sample_path, sample_sha):
    """A real voice comparison plus explicit full-trial request, never default approval."""
    documents = {}
    for key in ('production_request', 'selection', 'sample_receipt'):
        path = Path(str(receipt.get(key+'_path') or '')).resolve()
        if path.drive.casefold() != 'e:' or receipt.get(key+'_sha256') != file_hash(path):
            raise LocalVoiceLaneError('Selected voice trial provenance changed: '+key)
        documents[key] = _read_json(path)
    authority, selection, sample = (documents[k] for k in ('production_request','selection','sample_receipt'))
    instruction_hash = hashlib.sha256(CLEAR_INSTRUCTION.encode('utf-8')).hexdigest()
    comparison_request = Path(str(sample.get('request_path') or '')).resolve()
    if (comparison_request.drive.casefold() != 'e:'
            or sample.get('request_sha256') != file_hash(comparison_request)):
        raise LocalVoiceLaneError('Original voice comparison request changed')
    original = _read_json(comparison_request)
    if (receipt.get('request_hash') != content_hash(request)
            or authority.get('status') != 'USER_REQUESTED_WB_FULL_TRIAL'
            or authority.get('series_key') != request.get('series_key')
            or authority.get('sample_id') != 'ryan_clear'
            or authority.get('sample_sha256') != sample_sha
            or not str(authority.get('user_answer') or '').strip()
            or authority.get('paid_calls_authorized') is not False
            or authority.get('final_film_approved') is not False
            or authority.get('channel_default_changed') is not False
            or receipt.get('new_sample_approved_by_user') is not False
            or selection.get('status') != 'USER_SELECTED_FOR_NEXT_PREVIEW'
            or selection.get('sample_id') != 'ryan_clear'
            or selection.get('sample_sha256') != sample_sha
            or selection.get('sample_receipt_sha256') != receipt.get('sample_receipt_sha256')
            or selection.get('instruction_sha256') != instruction_hash
            or sample.get('status') != 'candidate_unapproved'
            or sample.get('sample_id') != 'ryan_clear' or sample.get('speaker') != 'Ryan'
            or sample.get('provider') != 'qwen_local' or sample.get('model_id') != MODEL_ID
            or sample.get('revision') != MODEL_REVISION
            or sample.get('preset') != 'clear_conversation_v1'
            or sample.get('instruction') != CLEAR_INSTRUCTION
            or sample.get('instruction_sha256') != instruction_hash
            or sample.get('output_sha256') != sample_sha
            or Path(str(sample.get('output_path') or '')).resolve() != sample_path
            or authority.get('source_job_id') != sample.get('source_job_id')
            or authority.get('source_script_sha256') != sample.get('source_script_sha256')
            or original.get('source_script_sha256') != sample.get('source_script_sha256')):
        raise LocalVoiceLaneError('Selected voice trial lacks matching comparison and explicit scope evidence')


def register_local_voice_lane(settings, job_id: str, *, approval_path=APPROVAL_PATH,
                              sample_path=SAMPLE_PATH, authorized: bool = False) -> LocalVoiceLane:
    """Persist explicit, immutable opt-in evidence inside this order's artifacts."""
    if authorized is not True:
        raise LocalVoiceLaneError("Explicit approval is required to register a local voice lane.")
    body = _lane_body(settings, job_id, Path(approval_path), Path(sample_path), require_idle=True)
    lane = LocalVoiceLane(**body, digest=content_hash(body))
    context = ProductionOrderStore(settings).context(job_id)
    if context.artifact_dir.resolve().drive.casefold() != "e:":
        raise LocalVoiceLaneError("Local voice registration artifacts must remain on E:.")
    registration = context.artifact_dir / "voice" / "local_voice_lane.json"
    registration.parent.mkdir(parents=True, exist_ok=True)
    record = {"lane": lane.as_dict(), "approval_path": str(Path(approval_path).resolve()),
              "sample_path": str(Path(sample_path).resolve())}
    encoded = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
    if registration.exists():
        if registration.read_text(encoding="utf-8") != encoded:
            raise LocalVoiceLaneError("An existing local voice registration is immutable.")
    else:
        registration.write_text(encoded, encoding="utf-8")
    return lane


def _load_base_local_voice_lane(settings, job_id: str, *, read_only_snapshot: bool = False) -> LocalVoiceLane | None:
    """Load a persisted opt-in only when its original request/profile/evidence still match."""
    order = ProductionOrderStore(settings).get(job_id)
    root = Path(order["artifact_dir"]).resolve()
    registration = root / "voice" / "local_voice_lane.json"
    if not registration.is_file():
        return None
    if root.drive.casefold() != "e:":
        raise LocalVoiceLaneError("Local voice registration artifacts must remain on E:.")
    record = _read_json(registration)
    lane_data = record.get("lane")
    if not isinstance(lane_data, dict):
        raise LocalVoiceLaneError("Local voice registration has no lane record.")
    fresh = _lane_body(settings, job_id, Path(record.get("approval_path", "")),
                       Path(record.get("sample_path", "")), require_idle=False,
                       read_only_snapshot=read_only_snapshot)
    if lane_data != {**fresh, "digest": content_hash(fresh)}:
        raise LocalVoiceLaneError("Local voice registration no longer matches request, profile or approval evidence.")
    return LocalVoiceLane(**lane_data)


def load_local_voice_lane(settings, job_id: str, *, read_only_snapshot: bool = False) -> LocalVoiceLane | None:
    base = _load_base_local_voice_lane(settings, job_id, read_only_snapshot=read_only_snapshot)
    if base is None:
        return None
    from atnos.production.voice_revision import effective_revision_lane
    return effective_revision_lane(settings, job_id, base)


def verify_runtime_availability(*, lightweight: bool = False) -> dict[str, Any]:
    """Check pinned local runtime/model availability without running inference."""
    from atnos.services.qwen_tts_local_runtime_service import runtime_availability
    qwen = runtime_availability()
    try:
        from atnos.services.local_ctc_alignment_service import runtime_availability as ctc_availability
        ctc = ctc_availability(verify_hash=False) if lightweight else ctc_availability()
    except (ImportError, AttributeError):
        ctc = {"available": False, "reason": "Local CTC alignment runtime has no availability check."}
    return {"available": bool(qwen.get("available") and ctc.get("available")),
            "qwen": qwen, "ctc": ctc, "provider": "qwen_local", "scope": "one_registered_job"}


def intake_voice_preset(candidate_id: str) -> str:
    if candidate_id == 'Ryan-B':return CLEAR_PRESET
    if candidate_id == 'Ryan-neutral':return NEUTRAL_PRESET
    raise LocalVoiceLaneError('Nieznany lokalny kandydat głosu.')


def _neutral_intake_sample() -> dict[str, str]:
    """Pinned curated reference, retaining the user's limited trial assessment."""
    root = NEUTRAL_INTAKE_ROOT.resolve()
    manifest_path = root/'manifest.json'
    manifest = _read_json(manifest_path)
    expected = {'sample.wav', 'sample-receipt.json', 'audition.json', 'user-assessment.json'}
    if (root.drive.casefold() != 'e:' or manifest.get('schema_version') != 1
            or manifest.get('candidate_id') != 'Ryan-neutral' or manifest.get('preset') != NEUTRAL_PRESET
            or manifest.get('status') != 'candidate_for_further_trials'
            or manifest.get('channel_default_changed') is not False or manifest.get('final_film_approved') is not False
            or manifest.get('model_id') != MODEL_ID or manifest.get('model_revision') != MODEL_REVISION
            or set(manifest.get('files', {})) != expected):
        raise LocalVoiceLaneError('Niepoprawny rejestr spokojnego Ryana.')
    for name, digest in manifest['files'].items():
        path = (root/name).resolve()
        if not path.is_relative_to(root) or not path.is_file() or file_hash(path) != digest:
            raise LocalVoiceLaneError('Zmieniona próbka lub pochodzenie spokojnego Ryana.')
    sample = _read_json(root/'sample-receipt.json')
    audition = _read_json(root/'audition.json')
    assessment = _read_json(root/'user-assessment.json')
    feedback = assessment.get('feedback') or {}
    instruction_hash = hashlib.sha256(NEUTRAL_INSTRUCTION.encode('utf-8')).hexdigest()
    if (sample.get('provider') != 'qwen_local' or sample.get('preset') != NEUTRAL_PRESET
            or sample.get('model_id') != MODEL_ID or sample.get('revision') != MODEL_REVISION
            or sample.get('speaker') != 'Ryan' or sample.get('instruction') != NEUTRAL_INSTRUCTION
            or sample.get('instruction_sha256') != instruction_hash
            or sample.get('production_enabled') is not False or sample.get('user_approved') is not False
            or audition.get('sha256') != manifest['files']['sample.wav']
            or audition.get('raw_sha256') != sample.get('output_sha256')
            or audition.get('time_stretch') is not False
            or assessment.get('status') != 'USER_SELECTED_VOICE_FOR_FURTHER_WB_TRIALS'
            or assessment.get('preset') != NEUTRAL_PRESET
            or assessment.get('source_job_id') != manifest.get('source_job_id')
            or assessment.get('channel_default_changed') is not False or assessment.get('final_film_approved') is not False
            or not assessment.get('user_answer') or feedback.get('active') is not True
            or feedback.get('category') != 'voice' or feedback.get('scope') != 'film'
            or feedback.get('source_job_id') != manifest.get('source_job_id')
            or assessment['user_answer'] not in feedback.get('note', '')
            or assessment.get('video_sha256', '') not in feedback.get('note', '')
            or not assessment.get('video_sha256')):
        raise LocalVoiceLaneError('Spokojny Ryan nie ma zgodnej próbki i oceny użytkownika.')
    return {'sample_path': str(root/'sample.wav'), 'sample_sha256': manifest['files']['sample.wav'],
            'receipt_sha256': file_hash(manifest_path)}


def intake_voice_sample(candidate_id: str = 'Ryan-B') -> dict[str, str]:
    """Closed candidate registry; a past sample is provenance, not new approval."""
    if candidate_id == 'Ryan-neutral':return _neutral_intake_sample()
    if candidate_id != 'Ryan-B':raise LocalVoiceLaneError('Nieznany lokalny kandydat głosu.')
    receipt = _read_json(INTAKE_SAMPLE_RECEIPT)
    sample = Path(str(receipt.get('output_path') or '')).resolve()
    instruction_hash = hashlib.sha256(CLEAR_INSTRUCTION.encode('utf-8')).hexdigest()
    if (sample.drive.casefold() != 'e:' or not sample.is_file()
            or receipt.get('output_sha256') != file_hash(sample)
            or receipt.get('sample_id') != 'ryan_clear' or receipt.get('speaker') != 'Ryan'
            or receipt.get('provider') != 'qwen_local' or receipt.get('model_id') != MODEL_ID
            or receipt.get('revision') != MODEL_REVISION
            or receipt.get('instruction') != CLEAR_INSTRUCTION
            or receipt.get('instruction_sha256') != instruction_hash):
        raise LocalVoiceLaneError('Lokalny kandydat Ryan B nie ma poprawnej próbki i pochodzenia.')
    return {'sample_path': str(sample), 'sample_sha256': file_hash(sample),
            'receipt_sha256': file_hash(INTAKE_SAMPLE_RECEIPT)}


def validate_intake_voice_selection(settings, order) -> dict[str, str] | None:
    if order.voice_candidate not in LOCAL_INTAKE_CANDIDATES:
        return None
    if order.provider_policy != 'strict_local' or order.delivery_mode != 'local_only':
        raise LocalVoiceLaneError('Ryan B wymaga trybu strict_local/local_only.')
    selection = intake_voice_sample() if order.voice_candidate == 'Ryan-B' else intake_voice_sample(order.voice_candidate)
    if not verify_runtime_availability().get('available'):
        raise LocalVoiceLaneError('Lokalny Ryan B lub niezależne wyrównanie napisów są niedostępne. Nie wybrano zastępczego głosu.')
    return selection


def register_intake_voice_lane(settings, job_id: str, selection: dict[str, str]) -> LocalVoiceLane:
    """Bind the explicit request choice before dispatch; never accept film quality."""
    store = ProductionOrderStore(settings)
    context = store.context(job_id)
    if (context.artifact_dir / 'voice/local_voice_lane.json').exists() and store.get(job_id).get('current_step') != 'voice_setup':
        return load_local_voice_lane(settings, job_id, read_only_snapshot=True)
    order = store.get(job_id)
    request = order['request']
    if request.get('voice_candidate') not in LOCAL_INTAKE_CANDIDATES or request.get('provider_policy') != 'strict_local':
        raise LocalVoiceLaneError('Brak jawnego wyboru Ryana B w tym zleceniu.')
    approval = context.artifact_dir / 'voice/intake-selection.json'
    receipt = {'job_id': job_id, 'decision': 'selected_at_intake_for_trial', 'preset': intake_voice_preset(request['voice_candidate']),
        'scope': "This order's trial AV only; explicit intake choice, not channel-default or quality acceptance",
        'channel_default_changed': False, 'frozen_profile_changed': False, 'final_film_approved': False,
        'quality_approved_by_user': False, 'request_hash': content_hash(request),
        'sample': selection['sample_path'], 'sample_sha256': selection['sample_sha256'],
        'sample_receipt_sha256': selection['receipt_sha256']}
    if request['voice_candidate'] != 'Ryan-B':receipt['candidate_id'] = request['voice_candidate']
    encoded = json.dumps(receipt, ensure_ascii=False, indent=2) + '\n'
    approval.parent.mkdir(parents=True, exist_ok=True)
    if approval.exists() and approval.read_text(encoding='utf-8') != encoded:
        raise LocalVoiceLaneError('Istniejący wybór głosu jest niezmienny.')
    if not approval.exists():
        approval.write_text(encoded, encoding='utf-8')
    lane = register_local_voice_lane(settings, job_id, approval_path=approval,
        sample_path=selection['sample_path'], authorized=True)
    from atnos.intelligence.storage import connect
    from atnos.production.order_store import _event, utc_now_iso, ProductionBlocked
    with connect(settings) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT j.status,j.current_step,o.lease_token FROM pipeline_jobs j JOIN pipeline_job_orders o ON o.job_id=j.id WHERE j.id=?', (job_id,)).fetchone()
        if row and row['current_step'] == 'voice_setup':
            if row['status'] != 'blocked' or row['lease_token']:
                raise ProductionBlocked('Aktywnego workera nie można zwolnić z blokady wyboru głosu.')
            db.execute("UPDATE pipeline_jobs SET status='queued',current_step='queued',error=NULL,updated_at=? WHERE id=? AND status='blocked' AND current_step='voice_setup'", (utc_now_iso(), job_id))
            _event(db, job_id, 'voice_setup', 'completed', {'local_voice_registered': True, 'quality_approved': False})
        db.commit()
    return lane
